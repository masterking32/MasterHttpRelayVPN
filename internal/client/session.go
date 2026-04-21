// ==============================================================================
// MasterHttpRelayVPN
// Author: MasterkinG32
// Github: https://github.com/masterking32
// Year: 2026
// ==============================================================================
package client

import (
	"context"
	"errors"
	"net"
	"sync"
	"sync/atomic"
	"time"

	"masterhttprelayvpn/internal/config"
	"masterhttprelayvpn/internal/protocol"
)

var ErrSOCKSQueueFull = errors.New("socks outbound queue is full")
var ErrSOCKSConnectTimeout = errors.New("socks connect timeout")

type ChunkPolicy struct {
	MaxChunkSize          int
	MaxPacketsPerBatch    int
	MaxBatchBytes         int
	WorkerCount           int
	MaxQueueBytesPerSOCKS int
}

func newChunkPolicy(cfg config.Config) ChunkPolicy {
	return ChunkPolicy{
		MaxChunkSize:          cfg.MaxChunkSize,
		MaxPacketsPerBatch:    cfg.MaxPacketsPerBatch,
		MaxBatchBytes:         cfg.MaxBatchBytes,
		WorkerCount:           cfg.WorkerCount,
		MaxQueueBytesPerSOCKS: cfg.MaxQueueBytesPerSOCKS,
	}
}

type SOCKSOutboundQueueItem struct {
	IdentityKey string
	Packet      protocol.Packet
	QueuedAt    time.Time
	SentAt      time.Time
	PayloadSize int
	RetryCount  int
}

type SOCKSConnection struct {
	ID                uint64
	ClientSessionKey  string
	ChunkPolicy       ChunkPolicy
	CreatedAt         time.Time
	LastActivityAt    time.Time
	ClientAddress     string
	TargetHost        string
	TargetPort        uint16
	TargetAddressType byte
	BufferedBytes     int
	NextSequence      uint64
	SOCKSAuthMethod   byte
	SOCKSUsername     string
	HandshakeDone     bool
	ConnectAccepted   bool
	ConnectFailure    string
	CloseReadSent     bool
	CloseWriteSent    bool
	ResetSent         bool

	LocalConn           net.Conn
	localWriteMu        sync.Mutex
	localCloseMu        sync.Mutex
	reorderMu           sync.Mutex
	localReadEOF        bool
	localWriteEOF       bool
	closedC             chan struct{}
	closeOnce           sync.Once
	connectResultC      chan error
	queueMu             sync.Mutex
	OutboundQueue       []*SOCKSOutboundQueueItem
	QueuedBytes         int
	InFlight            map[string]*SOCKSOutboundQueueItem
	NextInboundSequence uint64
	PendingInbound      map[uint64][]protocol.PendingPacket
}

type SOCKSConnectionStore struct {
	nextID atomic.Uint64
	mu     sync.RWMutex
	items  map[uint64]*SOCKSConnection
}

func NewSOCKSConnectionStore() *SOCKSConnectionStore {
	return &SOCKSConnectionStore{
		items: make(map[uint64]*SOCKSConnection),
	}
}

func (s *SOCKSConnectionStore) New(clientSessionKey string, clientAddress string, chunkPolicy ChunkPolicy) *SOCKSConnection {
	id := s.nextID.Add(1)
	now := time.Now()
	socksConn := &SOCKSConnection{
		ID:               id,
		ClientSessionKey: clientSessionKey,
		ChunkPolicy:      chunkPolicy,
		CreatedAt:        now,
		LastActivityAt:   now,
		ClientAddress:    clientAddress,
		closedC:          make(chan struct{}),
		connectResultC:   make(chan error, 1),
		InFlight:         make(map[string]*SOCKSOutboundQueueItem),
		PendingInbound:   make(map[uint64][]protocol.PendingPacket),
	}

	s.mu.Lock()
	s.items[id] = socksConn
	s.mu.Unlock()
	return socksConn
}

func (s *SOCKSConnection) WaitForConnect(ctx context.Context, timeout time.Duration) error {
	timer := time.NewTimer(timeout)
	defer timer.Stop()

	select {
	case err := <-s.connectResultC:
		return err
	case <-timer.C:
		return ErrSOCKSConnectTimeout
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (s *SOCKSConnection) CompleteConnect(err error) {
	select {
	case s.connectResultC <- err:
	default:
	}
}

func (s *SOCKSConnection) nextSequence() uint64 {
	s.NextSequence++
	return s.NextSequence
}

func (s *SOCKSConnection) BuildSOCKSConnectPacket() protocol.Packet {
	packet := protocol.NewPacket(s.ClientSessionKey, protocol.PacketTypeSOCKSConnect)
	packet.SOCKSID = s.ID
	packet.Target = &protocol.Target{
		Host:        s.TargetHost,
		Port:        s.TargetPort,
		AddressType: s.TargetAddressType,
	}
	return packet
}

func (s *SOCKSConnection) BuildSOCKSDataPacket(payload []byte, final bool) protocol.Packet {
	packet := protocol.NewPacket(s.ClientSessionKey, protocol.PacketTypeSOCKSData)
	packet.SOCKSID = s.ID
	packet.Sequence = s.nextSequence()
	packet.Final = final
	if len(payload) > 0 {
		packet.Payload = append([]byte(nil), payload...)
	}
	return packet
}

func (s *SOCKSConnection) BuildSOCKSCloseReadPacket() protocol.Packet {
	s.CloseReadSent = true

	packet := protocol.NewPacket(s.ClientSessionKey, protocol.PacketTypeSOCKSCloseRead)
	packet.SOCKSID = s.ID
	packet.Sequence = s.nextSequence()
	packet.Final = true
	return packet
}

func (s *SOCKSConnection) BuildSOCKSCloseWritePacket() protocol.Packet {
	s.CloseWriteSent = true

	packet := protocol.NewPacket(s.ClientSessionKey, protocol.PacketTypeSOCKSCloseWrite)
	packet.SOCKSID = s.ID
	packet.Sequence = s.nextSequence()
	packet.Final = true
	return packet
}

func (s *SOCKSConnection) BuildSOCKSRSTPacket() protocol.Packet {
	s.ResetSent = true

	packet := protocol.NewPacket(s.ClientSessionKey, protocol.PacketTypeSOCKSRST)
	packet.SOCKSID = s.ID
	packet.Sequence = s.nextSequence()
	packet.Final = true
	return packet
}

func (s *SOCKSConnection) WriteToLocal(payload []byte) error {
	s.localWriteMu.Lock()
	defer s.localWriteMu.Unlock()

	if s.LocalConn == nil || len(payload) == 0 {
		return nil
	}
	_, err := s.LocalConn.Write(payload)
	return err
}

func (s *SOCKSConnection) CloseLocal() error {
	var err error
	s.closeOnce.Do(func() {
		s.localWriteMu.Lock()
		defer s.localWriteMu.Unlock()
		if s.LocalConn != nil {
			err = s.LocalConn.Close()
		}
		close(s.closedC)
	})
	return err
}

func (s *SOCKSConnection) CloseLocalWrite() error {
	s.localCloseMu.Lock()
	defer s.localCloseMu.Unlock()

	if s.localWriteEOF {
		return nil
	}
	s.localWriteEOF = true

	if tcpConn, ok := s.LocalConn.(*net.TCPConn); ok {
		return tcpConn.CloseWrite()
	}
	return s.CloseLocal()
}

func (s *SOCKSConnection) CloseLocalRead() error {
	s.localCloseMu.Lock()
	defer s.localCloseMu.Unlock()

	if s.localReadEOF {
		return nil
	}
	s.localReadEOF = true

	if tcpConn, ok := s.LocalConn.(*net.TCPConn); ok {
		return tcpConn.CloseRead()
	}
	return nil
}

func (s *SOCKSConnection) MarkLocalReadEOF() {
	s.localCloseMu.Lock()
	s.localReadEOF = true
	s.localCloseMu.Unlock()
}

func (s *SOCKSConnection) BothLocalSidesClosed() bool {
	s.localCloseMu.Lock()
	defer s.localCloseMu.Unlock()
	return s.localReadEOF && s.localWriteEOF
}

func (s *SOCKSConnection) WaitUntilClosed(ctx context.Context) {
	select {
	case <-ctx.Done():
	case <-s.closedC:
	}
}

func (s *SOCKSConnection) ResetTransportState() {
	s.queueMu.Lock()
	for i := range s.OutboundQueue {
		s.OutboundQueue[i] = nil
	}
	s.OutboundQueue = nil
	s.QueuedBytes = 0
	clear(s.InFlight)
	s.queueMu.Unlock()

	s.BufferedBytes = 0
	s.reorderMu.Lock()
	clear(s.PendingInbound)
	s.NextInboundSequence = 0
	s.reorderMu.Unlock()
}

func (s *SOCKSConnection) queueInboundPacket(packet protocol.Packet, maxBuffered int) ([]protocol.Packet, bool, bool) {
	s.reorderMu.Lock()
	defer s.reorderMu.Unlock()

	expected := s.expectedInboundSequenceLocked()
	if packet.Sequence < expected {
		return nil, true, false
	}
	pendingForSequence := s.PendingInbound[packet.Sequence]
	if protocol.ContainsPendingPacket(pendingForSequence, packet) {
		return nil, true, false
	}
	if protocol.BufferedPendingPacketCount(s.PendingInbound) >= maxBuffered {
		return nil, false, true
	}

	s.PendingInbound[packet.Sequence] = append(s.PendingInbound[packet.Sequence], protocol.PendingPacket{
		Packet:   packet,
		QueuedAt: time.Now(),
	})

	if !s.ConnectAccepted {
		return nil, false, false
	}
	return s.drainReadyInboundLocked(), false, false
}

func (s *SOCKSConnection) activateInboundDrain() []protocol.Packet {
	s.reorderMu.Lock()
	defer s.reorderMu.Unlock()
	return s.drainReadyInboundLocked()
}

func (s *SOCKSConnection) expectedInboundSequenceLocked() uint64 {
	if s.NextInboundSequence == 0 {
		return 1
	}
	return s.NextInboundSequence
}

func (s *SOCKSConnection) drainReadyInboundLocked() []protocol.Packet {
	expected := s.expectedInboundSequenceLocked()
	ready := make([]protocol.Packet, 0)
	for {
		pendingPackets, ok := s.PendingInbound[expected]
		if !ok || len(pendingPackets) == 0 {
			break
		}
		protocol.SortPendingPackets(pendingPackets)
		for _, pending := range pendingPackets {
			ready = append(ready, pending.Packet)
		}
		delete(s.PendingInbound, expected)
		expected++
	}
	s.NextInboundSequence = expected
	return ready
}

func (s *SOCKSConnection) hasExpiredInboundGap(timeout time.Duration) bool {
	if timeout <= 0 {
		return false
	}

	s.reorderMu.Lock()
	defer s.reorderMu.Unlock()
	now := time.Now()
	for _, pendingPackets := range s.PendingInbound {
		for _, pending := range pendingPackets {
			if now.Sub(pending.QueuedAt) >= timeout {
				clear(s.PendingInbound)
				return true
			}
		}
	}
	return false
}

func (s *SOCKSConnection) EnqueuePacket(packet protocol.Packet) error {
	if err := packet.Validate(); err != nil {
		return err
	}

	item := &SOCKSOutboundQueueItem{
		IdentityKey: protocol.PacketIdentityKey(
			packet.ClientSessionKey,
			packet.SOCKSID,
			packet.Type,
			packet.Sequence,
			packet.FragmentID,
		),
		Packet:      packet,
		QueuedAt:    time.Now(),
		PayloadSize: len(packet.Payload),
	}

	s.queueMu.Lock()
	defer s.queueMu.Unlock()

	nextBytes := s.QueuedBytes + item.PayloadSize
	if nextBytes > s.ChunkPolicy.MaxQueueBytesPerSOCKS {
		return ErrSOCKSQueueFull
	}

	s.OutboundQueue = append(s.OutboundQueue, item)
	s.QueuedBytes = nextBytes
	return nil
}

func (s *SOCKSConnection) EnqueuePayloadChunks(payload []byte, final bool) (int, error) {
	chunks := splitPayloadChunks(payload, s.ChunkPolicy.MaxChunkSize)
	if len(chunks) == 0 && !final {
		return 0, nil
	}

	enqueued := 0
	for i, chunk := range chunks {
		packetFinal := final && i == len(chunks)-1
		packet := s.BuildSOCKSDataPacket(chunk, packetFinal)
		if err := s.EnqueuePacket(packet); err != nil {
			return enqueued, err
		}
		enqueued++
	}

	return enqueued, nil
}

func (s *SOCKSConnection) QueueSnapshot() (items int, bytes int) {
	s.queueMu.Lock()
	defer s.queueMu.Unlock()
	return len(s.OutboundQueue), s.QueuedBytes
}

func (s *SOCKSConnection) InFlightCount() int {
	s.queueMu.Lock()
	defer s.queueMu.Unlock()
	return len(s.InFlight)
}

func (s *SOCKSConnection) DequeuePacket() *SOCKSOutboundQueueItem {
	s.queueMu.Lock()
	defer s.queueMu.Unlock()

	if len(s.OutboundQueue) == 0 {
		return nil
	}

	item := s.OutboundQueue[0]
	s.OutboundQueue[0] = nil
	s.OutboundQueue = s.OutboundQueue[1:]
	s.QueuedBytes -= item.PayloadSize
	if s.QueuedBytes < 0 {
		s.QueuedBytes = 0
	}
	return item
}

func (s *SOCKSConnection) RequeueFront(items []*SOCKSOutboundQueueItem) {
	if len(items) == 0 {
		return
	}

	s.queueMu.Lock()
	defer s.queueMu.Unlock()

	front := make([]*SOCKSOutboundQueueItem, 0, len(items)+len(s.OutboundQueue))
	for _, item := range items {
		if item == nil {
			continue
		}
		front = append(front, item)
		s.QueuedBytes += item.PayloadSize
	}
	front = append(front, s.OutboundQueue...)
	s.OutboundQueue = front
}

func (s *SOCKSConnection) MarkInFlight(items []*SOCKSOutboundQueueItem) {
	if len(items) == 0 {
		return
	}

	s.queueMu.Lock()
	defer s.queueMu.Unlock()

	for _, item := range items {
		if item == nil {
			continue
		}
		item.SentAt = time.Now()
		s.InFlight[item.IdentityKey] = item
	}
}

func (s *SOCKSConnection) AckPacket(packet protocol.Packet) bool {
	identityKey := protocol.PacketIdentityKey(
		packet.ClientSessionKey,
		packet.SOCKSID,
		ackTargetPacketType(packet.Type),
		packet.Sequence,
		packet.FragmentID,
	)

	s.queueMu.Lock()
	defer s.queueMu.Unlock()
	if _, ok := s.InFlight[identityKey]; ok {
		delete(s.InFlight, identityKey)
		return true
	}
	return false
}

func (s *SOCKSConnection) RequeueInFlightByIdentity(identityKeys []string) {
	if len(identityKeys) == 0 {
		return
	}

	s.queueMu.Lock()
	defer s.queueMu.Unlock()

	front := make([]*SOCKSOutboundQueueItem, 0, len(identityKeys)+len(s.OutboundQueue))
	for _, identityKey := range identityKeys {
		item, ok := s.InFlight[identityKey]
		if !ok || item == nil {
			continue
		}
		delete(s.InFlight, identityKey)
		item.SentAt = time.Time{}
		front = append(front, item)
		s.QueuedBytes += item.PayloadSize
	}
	front = append(front, s.OutboundQueue...)
	s.OutboundQueue = front
}

func (s *SOCKSConnection) ReclaimExpiredInFlight(ackTimeout time.Duration, maxRetryCount int) (requeued int, dropped int) {
	now := time.Now()

	s.queueMu.Lock()
	defer s.queueMu.Unlock()

	if len(s.InFlight) == 0 {
		return 0, 0
	}

	front := make([]*SOCKSOutboundQueueItem, 0, len(s.InFlight)+len(s.OutboundQueue))
	for identityKey, item := range s.InFlight {
		if item == nil || item.SentAt.IsZero() || now.Sub(item.SentAt) < ackTimeout {
			continue
		}

		delete(s.InFlight, identityKey)
		if item.RetryCount >= maxRetryCount {
			dropped++
			continue
		}

		item.RetryCount++
		item.SentAt = time.Time{}
		front = append(front, item)
		s.QueuedBytes += item.PayloadSize
		requeued++
	}

	if len(front) > 0 {
		front = append(front, s.OutboundQueue...)
		s.OutboundQueue = front
	}
	return requeued, dropped
}

func ackTargetPacketType(packetType protocol.PacketType) protocol.PacketType {
	switch packetType {
	case protocol.PacketTypeSOCKSConnectAck,
		protocol.PacketTypeSOCKSConnectFail,
		protocol.PacketTypeSOCKSRuleSetDenied,
		protocol.PacketTypeSOCKSNetworkUnreachable,
		protocol.PacketTypeSOCKSHostUnreachable,
		protocol.PacketTypeSOCKSConnectionRefused,
		protocol.PacketTypeSOCKSTTLExpired,
		protocol.PacketTypeSOCKSCommandUnsupported,
		protocol.PacketTypeSOCKSAddressTypeUnsupported,
		protocol.PacketTypeSOCKSAuthFailed,
		protocol.PacketTypeSOCKSUpstreamUnavailable:
		return protocol.PacketTypeSOCKSConnect
	case protocol.PacketTypeSOCKSDataAck:
		return protocol.PacketTypeSOCKSData
	case protocol.PacketTypeSOCKSCloseRead:
		return protocol.PacketTypeSOCKSCloseRead
	case protocol.PacketTypeSOCKSCloseWrite:
		return protocol.PacketTypeSOCKSCloseWrite
	case protocol.PacketTypeSOCKSRST:
		return protocol.PacketTypeSOCKSRST
	default:
		return packetType
	}
}

func splitPayloadChunks(payload []byte, maxChunkSize int) [][]byte {
	if len(payload) == 0 || maxChunkSize <= 0 {
		return nil
	}

	chunks := make([][]byte, 0, (len(payload)+maxChunkSize-1)/maxChunkSize)
	for start := 0; start < len(payload); start += maxChunkSize {
		end := start + maxChunkSize
		if end > len(payload) {
			end = len(payload)
		}
		chunk := append([]byte(nil), payload[start:end]...)
		chunks = append(chunks, chunk)
	}
	return chunks
}

func (s *SOCKSConnectionStore) Get(id uint64) *SOCKSConnection {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.items[id]
}

func (s *SOCKSConnectionStore) Delete(id uint64) {
	s.mu.Lock()
	item := s.items[id]
	delete(s.items, id)
	s.mu.Unlock()

	if item != nil {
		item.ResetTransportState()
		_ = item.CloseLocal()
	}
}

func (s *SOCKSConnectionStore) CloseAll() {
	s.mu.Lock()
	items := make([]*SOCKSConnection, 0, len(s.items))
	for _, item := range s.items {
		items = append(items, item)
	}
	s.items = make(map[uint64]*SOCKSConnection)
	s.mu.Unlock()

	for _, item := range items {
		item.ResetTransportState()
		_ = item.CloseLocal()
	}
}

func (s *SOCKSConnectionStore) Snapshot() []*SOCKSConnection {
	s.mu.RLock()
	defer s.mu.RUnlock()

	items := make([]*SOCKSConnection, 0, len(s.items))
	for _, item := range s.items {
		items = append(items, item)
	}
	return items
}
