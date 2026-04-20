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
	PayloadSize int
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
