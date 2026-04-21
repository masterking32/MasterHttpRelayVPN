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
	"fmt"
	"io"
	"net/http"
	"sort"
	"sync"
	"time"

	"masterhttprelayvpn/internal/protocol"
)

type sendWorker struct {
	id         int
	httpClient *http.Client
}

type dequeuedPacket struct {
	socksConn *SOCKSConnection
	item      *SOCKSOutboundQueueItem
}

func (c *Client) startSendWorkers(ctx context.Context, wg *sync.WaitGroup) {
	for i := 0; i < c.cfg.WorkerCount; i++ {
		worker := &sendWorker{
			id: i + 1,
			httpClient: &http.Client{
				Timeout: time.Duration(c.cfg.HTTPRequestTimeoutMS) * time.Millisecond,
			},
		}

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
			c.requeueSelected(selected)
			c.releaseBatchSlot()
			c.waitForSendWork(ctx, c.jitterDuration(waitInterval))
			continue
		}

		c.markSelectedInFlight(selected)

		body, err := protocol.EncryptBatch(batch, c.cfg.AESEncryptionKey)
		if err != nil {
			c.log.Errorf("<red>worker=<cyan>%d</cyan> encrypt batch failed: <cyan>%v</cyan></red>", w.id, err)
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
	if len(connections) == 0 {
		return protocol.Batch{}, nil
	}

	sort.Slice(connections, func(i, j int) bool {
		return connections[i].ID < connections[j].ID
	})

	start := 0
	if len(connections) > 1 {
		rotationEvery := c.cfg.MuxRotateEveryBatches
		if rotationEvery < 1 {
			rotationEvery = 1
		}
		turn := c.batchCursor.Add(1) - 1
		start = int((turn / uint64(rotationEvery)) % uint64(len(connections)))
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
	if len(connections) == 0 {
		return protocol.Batch{}, false
	}

	now := time.Now()
	nowUnixMS := now.UnixMilli()
	lastUnixMS := c.lastPollUnixMS.Load()
	minInterval := c.jitterDuration(c.effectiveIdlePollInterval(totalQueuedBytes))
	if lastUnixMS > 0 && nowUnixMS-lastUnixMS < minInterval.Milliseconds() {
		return protocol.Batch{}, false
	}

	if !c.lastPollUnixMS.CompareAndSwap(lastUnixMS, nowUnixMS) {
		return protocol.Batch{}, false
	}

	packet := protocol.NewPacket(c.clientSessionKey, protocol.PacketTypePing)
	packet.Payload = []byte("poll")
	batch := protocol.NewBatch(c.clientSessionKey, protocol.NewBatchID(), []protocol.Packet{packet})
	return batch, true
}

func (c *Client) effectiveBatchLimits(totalQueuedBytes int) (int, int) {
	maxPackets := c.cfg.MaxPacketsPerBatch
	maxBatchBytes := c.cfg.MaxBatchBytes
	if totalQueuedBytes < c.cfg.MuxBurstThresholdBytes {
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
	if totalQueuedBytes >= c.cfg.MuxBurstThresholdBytes {
		if burst := interval / 2; burst >= 25*time.Millisecond {
			return burst
		}
		return 25 * time.Millisecond
	}
	return interval
}

func (c *Client) effectiveIdlePollInterval(totalQueuedBytes int) time.Duration {
	interval := time.Duration(c.cfg.IdlePollIntervalMS) * time.Millisecond
	if totalQueuedBytes >= c.cfg.MuxBurstThresholdBytes {
		if burst := interval / 2; burst >= time.Duration(c.cfg.WorkerPollIntervalMS)*time.Millisecond {
			return burst
		}
	}
	return interval
}

func (c *Client) effectiveConcurrentBatches(totalQueuedBytes int) int {
	if totalQueuedBytes >= c.cfg.MuxBurstThresholdBytes {
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

func (c *Client) jitterDuration(base time.Duration) time.Duration {
	if base <= 0 || c.cfg.HTTPTimingJitterMS <= 0 {
		return base
	}

	jitter := time.Duration(randomIndex(c.cfg.HTTPTimingJitterMS+1)) * time.Millisecond
	return base + jitter
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
				socksConn.ConnectFailure = "max retry exceeded"
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
		socksConn.ConnectFailure = "inbound reorder timeout"
		socksConn.ResetTransportState()
		_ = socksConn.CloseLocal()
	}
}

func (w *sendWorker) postBatch(ctx context.Context, c *Client, batch protocol.Batch, body []byte) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.cfg.RelayURL, bytes.NewReader(body))
	if err != nil {
		return err
	}

	req.Header.Set("Content-Type", "application/octet-stream")
	req.Header.Set("X-Relay-Version", fmt.Sprintf("%d", protocol.CurrentVersion))
	if c.headerBuilder != nil {
		c.headerBuilder.Apply(req)
	}

	resp, err := w.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		respBody, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return fmt.Errorf("unexpected status %d: %s", resp.StatusCode, string(respBody))
	}

	if resp.StatusCode == http.StatusNoContent {
		c.log.Debugf(
			"<gray>worker=<cyan>%d</cyan> batch=<cyan>%s</cyan> got no-content response</gray>",
			w.id, batch.BatchID,
		)
		return nil
	}

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}

	if len(respBody) == 0 {
		return nil
	}

	responseBatch, err := protocol.DecryptBatch(respBody, c.cfg.AESEncryptionKey)
	if err != nil {
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

func (c *Client) applyResponseBatch(batch protocol.Batch) error {
	for _, packet := range batch.Packets {
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

	if isReorderSequencedPacket(packet.Type) {
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
			socksConn.ConnectFailure = "inbound reorder overflow"
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
		socksConn.ConnectFailure = message
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
