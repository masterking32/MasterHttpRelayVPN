// ==============================================================================
// MasterHttpRelayVPN
// Author: MasterkinG32
// Github: https://github.com/masterking32
// Year: 2026
// ==============================================================================
package client

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"sort"
	"sync"
	"time"

	"masterhttprelayvpn/internal/config"
	"masterhttprelayvpn/internal/protocol"
)

type sendWorker struct {
	id                  int
	httpClient          *http.Client
	httpTransport       *http.Transport
	transportUseCount   int
	transportReuseLimit int
}

type dequeuedPacket struct {
	socksConn *SOCKSConnection
	item      *SOCKSOutboundQueueItem
}

func (c *Client) startSendWorkers(ctx context.Context, wg *sync.WaitGroup) {
	for i := 0; i < c.cfg.WorkerCount; i++ {
		worker := &sendWorker{
			id: i + 1,
		}
		worker.resetHTTPClient(c.cfg)

		wg.Add(1)
		go func(w *sendWorker) {
			defer wg.Done()
			w.run(ctx, c)
		}(worker)
	}
}

func (w *sendWorker) run(ctx context.Context, c *Client) {
	for {
		select {
		case <-ctx.Done():
			return
		default:
		}

		c.reclaimExpiredInFlight()
		c.reclaimExpiredReorder()
		connections := c.socksConnections.Snapshot()
		totalQueuedBytes := queuedBytesAcross(connections)
		waitInterval := c.effectiveWaitInterval(totalQueuedBytes)

		if !c.tryAcquireBatchSlot(totalQueuedBytes) {
			c.waitForSendWork(ctx, c.jitterDuration(waitInterval))
			continue
		}

		batch, selected := c.buildNextBatch(connections, totalQueuedBytes)
		if len(batch.Packets) == 0 {
			c.releaseBatchSlot()
			c.waitForSendWork(ctx, c.jitterDuration(waitInterval))
			continue
		}

		if err := batch.Validate(); err != nil {
			c.log.Errorf("<red>worker=<cyan>%d</cyan> invalid batch: <cyan>%v</cyan></red>", w.id, err)
			if isPingOnlyBatch(batch) {
				c.failPing()
			}
			c.requeueSelected(selected)
			c.releaseBatchSlot()
			c.waitForSendWork(ctx, c.jitterDuration(waitInterval))
			continue
		}

		c.markSelectedInFlight(selected)

		body, err := protocol.EncryptBatch(batch, c.cfg.AESEncryptionKey)
		if err != nil {
			c.log.Errorf("<red>worker=<cyan>%d</cyan> encrypt batch failed: <cyan>%v</cyan></red>", w.id, err)
			if isPingOnlyBatch(batch) {
				c.failPing()
			}
			c.requeueSelected(selected)
			c.releaseBatchSlot()
			c.waitForSendWork(ctx, c.jitterDuration(waitInterval))
			continue
		}

		if err := w.postBatch(ctx, c, batch, body); err != nil {
			c.log.Warnf("<yellow>worker=<cyan>%d</cyan> send failed for batch=<cyan>%s</cyan>: <cyan>%v</cyan></yellow>", w.id, batch.BatchID, err)
			c.requeueSelected(selected)
			c.releaseBatchSlot()
			c.waitForSendWork(ctx, c.jitterDuration(waitInterval))
			continue
		}
		c.releaseBatchSlot()

		c.log.Debugf(
			"<green>worker=<cyan>%d</cyan> sent batch=<cyan>%s</cyan> packets=<cyan>%d</cyan> bytes=<cyan>%d</cyan></green>",
			w.id, batch.BatchID, len(batch.Packets), len(body),
		)
		if !isPingOnlyBatch(batch) {
			c.noteMeaningfulActivity(time.Now())
		}
	}
}

func (c *Client) waitForSendWork(ctx context.Context, interval time.Duration) {
	timer := time.NewTimer(interval)
	defer timer.Stop()

	select {
	case <-ctx.Done():
	case <-c.workCh:
	case <-timer.C:
	}
}

func (c *Client) buildNextBatch(connections []*SOCKSConnection, totalQueuedBytes int) (protocol.Batch, []dequeuedPacket) {
	start := 0
	if len(connections) > 0 {
		sort.Slice(connections, func(i, j int) bool {
			return connections[i].ID < connections[j].ID
		})
	}
	if len(connections) > 1 {
		rotationEvery := c.effectiveMuxRotateEveryBatches()
		if rotationEvery < 1 {
			rotationEvery = 1
		}
		turn := c.batchCursor.Add(1) - 1
		start = int((turn / uint64(rotationEvery)) % uint64(len(connections)))
		if offset := c.randomMuxStartOffset(len(connections)); offset > 0 {
			start = (start + offset) % len(connections)
		}
	}
	maxPackets, maxBatchBytes := c.effectiveBatchLimits(totalQueuedBytes)
	maxPerSOCKS := c.cfg.MaxPacketsPerSOCKSPerBatch

	selected := make([]dequeuedPacket, 0, maxPackets)
	packets := make([]protocol.Packet, 0, maxPackets)
	selectedPerSOCKS := make(map[uint64]int, len(connections))
	totalBytes := 0

	for len(selected) < maxPackets {
		progress := false

		for offset := range connections {
			if len(selected) >= maxPackets {
				break
			}

			socksConn := connections[(start+offset)%len(connections)]
			if selectedPerSOCKS[socksConn.ID] >= maxPerSOCKS {
				continue
			}

			item := socksConn.DequeuePacket()
			if item == nil {
				continue
			}

			packetBytes := len(item.Packet.Payload)
			if len(selected) > 0 && totalBytes+packetBytes > maxBatchBytes {
				socksConn.RequeueFront([]*SOCKSOutboundQueueItem{item})
				continue
			}

			selected = append(selected, dequeuedPacket{
				socksConn: socksConn,
				item:      item,
			})
			packets = append(packets, item.Packet)
			selectedPerSOCKS[socksConn.ID]++
			totalBytes += packetBytes
			progress = true
		}

		if !progress {
			break
		}
	}

	if len(packets) == 0 {
		if pingBatch, ok := c.buildPollBatch(connections, totalQueuedBytes); ok {
			return pingBatch, nil
		}
		return protocol.Batch{}, nil
	}

	batch := protocol.NewBatch(c.clientSessionKey, protocol.NewBatchID(), packets)
	return batch, selected
}

func (c *Client) buildPollBatch(connections []*SOCKSConnection, totalQueuedBytes int) (protocol.Batch, bool) {
	now := time.Now()
	if !c.shouldSendPing(connections, totalQueuedBytes, now) {
		return protocol.Batch{}, false
	}
	if !c.tryBeginPing(now) {
		return protocol.Batch{}, false
	}

	packet := protocol.NewPacket(c.clientSessionKey, protocol.PacketTypePing)
	packet.Payload = buildPingPayload(now)
	batch := protocol.NewBatch(c.clientSessionKey, protocol.NewBatchID(), []protocol.Packet{packet})
	return batch, true
}

func (c *Client) shouldSendPing(connections []*SOCKSConnection, totalQueuedBytes int, now time.Time) bool {
	if totalQueuedBytes > 0 {
		return false
	}
	if hasQueuedPackets(connections) {
		return false
	}
	if c.pingInFlight.Load() > 0 {
		return false
	}

	lastMeaningfulUnixMS := c.lastMeaningfulActivityUnixMS.Load()
	sessionActive := len(connections) > 0 || lastMeaningfulUnixMS > 0
	if !sessionActive {
		return false
	}

	nextDueUnixMS := c.nextPingDueUnixMS.Load()
	if nextDueUnixMS <= 0 {
		return lastMeaningfulUnixMS > 0
	}

	return now.UnixMilli() >= nextDueUnixMS
}

func (c *Client) effectiveBatchLimits(totalQueuedBytes int) (int, int) {
	maxPackets := c.cfg.MaxPacketsPerBatch
	maxBatchBytes := c.cfg.MaxBatchBytes
	if totalQueuedBytes < c.effectiveBurstThresholdBytes() {
		if reducedPackets := maxPackets / 2; reducedPackets >= 1 {
			maxPackets = reducedPackets
		}
		if reducedBytes := maxBatchBytes / 2; reducedBytes >= c.cfg.MaxChunkSize {
			maxBatchBytes = reducedBytes
		}
	}
	if !c.cfg.HTTPBatchRandomize {
		return maxPackets, maxBatchBytes
	}

	if c.cfg.HTTPBatchPacketsJitter > 0 && maxPackets > 1 {
		delta := randomIndex(c.cfg.HTTPBatchPacketsJitter + 1)
		if adjusted := maxPackets - delta; adjusted >= 1 {
			maxPackets = adjusted
		}
	}

	if c.cfg.HTTPBatchBytesJitter > 0 && maxBatchBytes > c.cfg.MaxChunkSize {
		delta := randomIndex(c.cfg.HTTPBatchBytesJitter + 1)
		if adjusted := maxBatchBytes - delta; adjusted >= c.cfg.MaxChunkSize {
			maxBatchBytes = adjusted
		}
	}

	return maxPackets, maxBatchBytes
}

func (c *Client) effectiveWaitInterval(totalQueuedBytes int) time.Duration {
	interval := time.Duration(c.cfg.WorkerPollIntervalMS) * time.Millisecond
	if totalQueuedBytes >= c.effectiveBurstThresholdBytes() {
		if burst := interval / 2; burst >= 25*time.Millisecond {
			return burst
		}
		return 25 * time.Millisecond
	}
	return interval
}

func (c *Client) effectiveConcurrentBatches(totalQueuedBytes int) int {
	if totalQueuedBytes >= c.effectiveBurstThresholdBytes() {
		return c.cfg.MaxConcurrentBatches
	}
	return 1
}

func (c *Client) tryAcquireBatchSlot(totalQueuedBytes int) bool {
	limit := c.effectiveConcurrentBatches(totalQueuedBytes)
	if limit < 1 {
		limit = 1
	}

	for {
		current := c.activeBatches.Load()
		if int(current) >= limit {
			return false
		}
		if c.activeBatches.CompareAndSwap(current, current+1) {
			return true
		}
	}
}

func (c *Client) releaseBatchSlot() {
	for {
		current := c.activeBatches.Load()
		if current <= 0 {
			return
		}
		if c.activeBatches.CompareAndSwap(current, current-1) {
			return
		}
	}
}

func queuedBytesAcross(connections []*SOCKSConnection) int {
	total := 0
	for _, socksConn := range connections {
		_, queuedBytes := socksConn.QueueSnapshot()
		total += queuedBytes
	}
	return total
}

func hasQueuedPackets(connections []*SOCKSConnection) bool {
	for _, socksConn := range connections {
		queueItems, _ := socksConn.QueueSnapshot()
		if queueItems > 0 {
			return true
		}
	}
	return false
}

func buildPingPayload(now time.Time) []byte {
	random := make([]byte, 8)
	if _, err := rand.Read(random); err != nil {
		return []byte(fmt.Sprintf("ping:%d:fallback", now.UnixMilli()))
	}

	return []byte(fmt.Sprintf("ping:%d:%s", now.UnixMilli(), hex.EncodeToString(random)))
}

func isPingOnlyBatch(batch protocol.Batch) bool {
	return len(batch.Packets) == 1 && batch.Packets[0].Type == protocol.PacketTypePing
}

func (c *Client) jitterDuration(base time.Duration) time.Duration {
	if base <= 0 || c.cfg.HTTPTimingJitterMS <= 0 {
		return base
	}

	jitter := time.Duration(randomIndex(c.cfg.HTTPTimingJitterMS+1)) * time.Millisecond
	return base + jitter
}

func (c *Client) pingIntervalWithJitter(base time.Duration) time.Duration {
	if base <= 0 || !c.cfg.HTTPRandomizeTransport || c.cfg.PingIntervalJitterMS <= 0 {
		return base
	}

	jitter := time.Duration(randomIndex(c.cfg.PingIntervalJitterMS+1)) * time.Millisecond
	return base + jitter
}

func (c *Client) effectiveBurstThresholdBytes() int {
	threshold := c.cfg.MuxBurstThresholdBytes
	if !c.cfg.HTTPRandomizeTransport || c.cfg.MuxBurstThresholdJitterBytes <= 0 {
		return threshold
	}

	delta := randomIndex(c.cfg.MuxBurstThresholdJitterBytes + 1)
	if randomIndex(2) == 0 {
		if adjusted := threshold - delta; adjusted >= c.cfg.MaxChunkSize {
			return adjusted
		}
		return c.cfg.MaxChunkSize
	}
	return threshold + delta
}

func (c *Client) effectiveMuxRotateEveryBatches() int {
	rotationEvery := c.cfg.MuxRotateEveryBatches
	if !c.cfg.HTTPRandomizeTransport || c.cfg.MuxRotateJitterBatches <= 0 {
		return rotationEvery
	}
	return rotationEvery + randomIndex(c.cfg.MuxRotateJitterBatches+1)
}

func (c *Client) randomMuxStartOffset(connectionCount int) int {
	if !c.cfg.HTTPRandomizeTransport || connectionCount <= 1 {
		return 0
	}
	return randomIndex(connectionCount)
}

func (c *Client) requeueSelected(selected []dequeuedPacket) {
	grouped := make(map[*SOCKSConnection][]string)
	for _, entry := range selected {
		grouped[entry.socksConn] = append(grouped[entry.socksConn], entry.item.IdentityKey)
	}

	for socksConn, identityKeys := range grouped {
		socksConn.RequeueInFlightByIdentity(identityKeys)
	}

	if len(grouped) > 0 {
		c.signalSendWork()
	}
}

func (c *Client) markSelectedInFlight(selected []dequeuedPacket) {
	grouped := make(map[*SOCKSConnection][]*SOCKSOutboundQueueItem)
	for _, entry := range selected {
		grouped[entry.socksConn] = append(grouped[entry.socksConn], entry.item)
	}

	for socksConn, items := range grouped {
		socksConn.MarkInFlight(items)
	}
}

func (c *Client) reclaimExpiredInFlight() {
	ackTimeout := time.Duration(c.cfg.AckTimeoutMS) * time.Millisecond
	for _, socksConn := range c.socksConnections.Snapshot() {
		requeued, dropped := socksConn.ReclaimExpiredInFlight(ackTimeout, c.cfg.MaxRetryCount)
		if requeued > 0 || dropped > 0 {
			c.log.Warnf(
				"<yellow>socks_id=<cyan>%d</cyan> reclaimed inflight requeued=<cyan>%d</cyan> dropped=<cyan>%d</cyan></yellow>",
				socksConn.ID, requeued, dropped,
			)

			if requeued > 0 {
				c.signalSendWork()
			}

			if dropped > 0 {
				socksConn.CompleteConnect(fmt.Errorf("max retry exceeded"))
				socksConn.ResetTransportState()
				_ = socksConn.CloseLocal()
			}
		}
	}
}

func (c *Client) reclaimExpiredReorder() {
	timeout := time.Duration(c.cfg.ReorderTimeoutMS) * time.Millisecond
	for _, socksConn := range c.socksConnections.Snapshot() {
		if !socksConn.hasExpiredInboundGap(timeout) {
			continue
		}
		c.log.Warnf(
			"<yellow>socks_id=<cyan>%d</cyan> inbound reorder gap expired, closing connection</yellow>",
			socksConn.ID,
		)
		socksConn.ResetTransportState()
		_ = socksConn.CloseLocal()
	}
}

func (w *sendWorker) postBatch(ctx context.Context, c *Client, batch protocol.Batch, body []byte) error {
	pingOnly := isPingOnlyBatch(batch)
	relayURL := c.nextRelayURL()
	if c.headerBuilder != nil {
		relayURL = c.headerBuilder.BuildRelayURL(relayURL)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, relayURL, bytes.NewReader(body))
	if err != nil {
		if pingOnly {
			c.failPing()
		}
		return err
	}

	req.Header.Set("Content-Type", "application/octet-stream")
	req.Header.Set("X-Relay-Version", fmt.Sprintf("%d", protocol.CurrentVersion))
	if c.headerBuilder != nil {
		c.headerBuilder.Apply(req)
	}

	resp, err := w.httpClient.Do(req)
	w.recordTransportUse(c.cfg)
	if err != nil {
		if pingOnly {
			c.failPing()
		}
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		if pingOnly {
			c.failPing()
		}
		respBody, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return fmt.Errorf("unexpected status %d: %s", resp.StatusCode, string(respBody))
	}

	if resp.StatusCode == http.StatusNoContent {
		if pingOnly {
			c.failPing()
		}
		c.log.Debugf(
			"<gray>worker=<cyan>%d</cyan> batch=<cyan>%s</cyan> got no-content response</gray>",
			w.id, batch.BatchID,
		)
		return nil
	}

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		if pingOnly {
			c.failPing()
		}
		return err
	}

	if len(respBody) == 0 {
		if pingOnly {
			c.failPing()
		}
		return nil
	}

	responseBatch, err := protocol.DecryptBatch(respBody, c.cfg.AESEncryptionKey)
	if err != nil {
		if pingOnly {
			c.failPing()
		}
		return err
	}

	c.log.Debugf(
		"<gray>worker=<cyan>%d</cyan> received response batch=<cyan>%s</cyan> packets=<cyan>%d</cyan> bytes=<cyan>%d</cyan></gray>",
		w.id, responseBatch.BatchID, len(responseBatch.Packets), len(respBody),
	)

	if err := c.applyResponseBatch(responseBatch); err != nil {
		return err
	}

	return nil
}

func (w *sendWorker) resetHTTPClient(cfg config.Config) {
	transport := &http.Transport{
		Proxy:               http.ProxyFromEnvironment,
		ForceAttemptHTTP2:   true,
		MaxIdleConns:        32,
		MaxIdleConnsPerHost: 8,
		IdleConnTimeout:     w.randomizedIdleConnTimeout(cfg),
	}

	w.httpTransport = transport
	w.httpClient = &http.Client{
		Timeout:   time.Duration(cfg.HTTPRequestTimeoutMS) * time.Millisecond,
		Transport: transport,
	}
	w.transportUseCount = 0
	w.transportReuseLimit = w.nextTransportReuseLimit(cfg)
}

func (w *sendWorker) recordTransportUse(cfg config.Config) {
	if !cfg.HTTPRandomizeTransport {
		return
	}

	w.transportUseCount++
	if w.transportReuseLimit > 0 && w.transportUseCount >= w.transportReuseLimit {
		if w.httpTransport != nil {
			w.httpTransport.CloseIdleConnections()
		}
		w.resetHTTPClient(cfg)
	}
}

func (w *sendWorker) randomizedIdleConnTimeout(cfg config.Config) time.Duration {
	minTimeout := cfg.HTTPIdleConnTimeoutMinMS
	maxTimeout := cfg.HTTPIdleConnTimeoutMaxMS
	if !cfg.HTTPRandomizeTransport || maxTimeout <= minTimeout {
		return time.Duration(minTimeout) * time.Millisecond
	}

	return time.Duration(minTimeout+randomIndex(maxTimeout-minTimeout+1)) * time.Millisecond
}

func (w *sendWorker) nextTransportReuseLimit(cfg config.Config) int {
	if !cfg.HTTPRandomizeTransport || cfg.HTTPTransportReuseMax <= cfg.HTTPTransportReuseMin {
		return cfg.HTTPTransportReuseMin
	}

	return cfg.HTTPTransportReuseMin + randomIndex(cfg.HTTPTransportReuseMax-cfg.HTTPTransportReuseMin+1)
}

func (c *Client) applyResponseBatch(batch protocol.Batch) error {
	for _, packet := range batch.Packets {
		if packet.Type == protocol.PacketTypePong {
			c.completePingWithPong()
		}
		if packet.Type != protocol.PacketTypePing && packet.Type != protocol.PacketTypePong {
			c.noteMeaningfulActivity(time.Now())
		}

		c.log.Debugf(
			"<gray>apply response packet=<cyan>%s</cyan> socks_id=<cyan>%d</cyan> seq=<cyan>%d</cyan> payload_bytes=<cyan>%d</cyan> final=<cyan>%t</cyan></gray>",
			packet.Type, packet.SOCKSID, packet.Sequence, len(packet.Payload), packet.Final,
		)

		if err := c.applyResponsePacket(packet); err != nil {
			return err
		}
	}
	return nil
}

func (c *Client) applyResponsePacket(packet protocol.Packet) error {
	switch packet.Type {
	case protocol.PacketTypePing, protocol.PacketTypePong:
		return nil
	}

	socksConn := c.socksConnections.Get(packet.SOCKSID)
	if socksConn == nil {
		return nil
	}

	if protocol.IsReorderSequencedPacket(packet.Type) {
		readyPackets, duplicate, overflow := socksConn.queueInboundPacket(packet, c.cfg.MaxReorderBufferPackets)
		if duplicate {
			c.log.Debugf(
				"<gray>ignored duplicate inbound packet socks_id=<cyan>%d</cyan> type=<cyan>%s</cyan> seq=<cyan>%d</cyan></gray>",
				socksConn.ID, packet.Type, packet.Sequence,
			)
			return nil
		}
		if overflow {
			c.log.Warnf(
				"<yellow>inbound reorder buffer overflow socks_id=<cyan>%d</cyan> type=<cyan>%s</cyan> seq=<cyan>%d</cyan></yellow>",
				socksConn.ID, packet.Type, packet.Sequence,
			)
			socksConn.ResetTransportState()
			_ = socksConn.CloseLocal()
			return nil
		}
		for _, readyPacket := range readyPackets {
			if err := c.applyOrderedResponsePacket(socksConn, readyPacket); err != nil {
				return err
			}
		}
		return nil
	}

	return c.applyOrderedResponsePacket(socksConn, packet)
}

func (c *Client) applyOrderedResponsePacket(socksConn *SOCKSConnection, packet protocol.Packet) error {
	switch packet.Type {
	case protocol.PacketTypePing, protocol.PacketTypePong:
		return nil
	}

	switch packet.Type {
	case protocol.PacketTypeSOCKSConnectAck:
		_ = socksConn.AckPacket(packet)
		socksConn.ConnectAccepted = true
		socksConn.LastActivityAt = time.Now()
		c.log.Debugf(
			"<gray>connect ack applied socks_id=<cyan>%d</cyan></gray>",
			socksConn.ID,
		)
		socksConn.CompleteConnect(nil)
		for _, readyPacket := range socksConn.activateInboundDrain() {
			if err := c.applyOrderedResponsePacket(socksConn, readyPacket); err != nil {
				return err
			}
		}
		return nil

	case protocol.PacketTypeSOCKSConnectFail,
		protocol.PacketTypeSOCKSRuleSetDenied,
		protocol.PacketTypeSOCKSNetworkUnreachable,
		protocol.PacketTypeSOCKSHostUnreachable,
		protocol.PacketTypeSOCKSConnectionRefused,
		protocol.PacketTypeSOCKSTTLExpired,
		protocol.PacketTypeSOCKSCommandUnsupported,
		protocol.PacketTypeSOCKSAddressTypeUnsupported,
		protocol.PacketTypeSOCKSAuthFailed,
		protocol.PacketTypeSOCKSUpstreamUnavailable:
		message := packet.Type.String()

		if len(packet.Payload) > 0 {
			message = string(packet.Payload)
		}

		_ = socksConn.AckPacket(packet)
		c.log.Warnf(
			"<yellow>connect failure applied socks_id=<cyan>%d</cyan> reason=<cyan>%s</cyan></yellow>",
			socksConn.ID, message,
		)

		socksConn.CompleteConnect(fmt.Errorf("%s", message))
		_ = socksConn.CloseLocal()
		return nil

	case protocol.PacketTypeSOCKSDataAck:
		_ = socksConn.AckPacket(packet)
		socksConn.LastActivityAt = time.Now()
		c.log.Debugf(
			"<gray>data ack applied socks_id=<cyan>%d</cyan> seq=<cyan>%d</cyan></gray>",
			socksConn.ID, packet.Sequence,
		)
		return nil

	case protocol.PacketTypeSOCKSData:
		socksConn.LastActivityAt = time.Now()
		c.log.Debugf(
			"<gray>writing to local socket socks_id=<cyan>%d</cyan> bytes=<cyan>%d</cyan></gray>",
			socksConn.ID, len(packet.Payload),
		)

		return socksConn.WriteToLocal(packet.Payload)

	case protocol.PacketTypeSOCKSCloseRead:
		_ = socksConn.AckPacket(packet)
		socksConn.LastActivityAt = time.Now()
		c.log.Debugf(
			"<gray>close_read applied socks_id=<cyan>%d</cyan></gray>",
			socksConn.ID,
		)

		if err := socksConn.CloseLocalWrite(); err != nil {
			return err
		}

		if socksConn.BothLocalSidesClosed() {
			return socksConn.CloseLocal()
		}

		return nil

	case protocol.PacketTypeSOCKSCloseWrite:
		_ = socksConn.AckPacket(packet)
		socksConn.LastActivityAt = time.Now()
		c.log.Debugf(
			"<gray>close_write applied socks_id=<cyan>%d</cyan></gray>",
			socksConn.ID,
		)
		if socksConn.BothLocalSidesClosed() {
			return socksConn.CloseLocal()
		}
		return nil

	case protocol.PacketTypeSOCKSRST:
		_ = socksConn.AckPacket(packet)
		socksConn.LastActivityAt = time.Now()
		c.log.Warnf(
			"<yellow>rst applied socks_id=<cyan>%d</cyan></yellow>",
			socksConn.ID,
		)
		return socksConn.CloseLocal()

	default:
		return nil
	}
}
