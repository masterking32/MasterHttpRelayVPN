// ==============================================================================
// MasterHttpRelayVPN
// Author: MasterkinG32
// Github: https://github.com/masterking32
// Year: 2026
// ==============================================================================
package client

import (
	"errors"
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
