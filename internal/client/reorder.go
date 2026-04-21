// ==============================================================================
// MasterHttpRelayVPN
// Author: MasterkinG32
// Github: https://github.com/masterking32
// Year: 2026
// ==============================================================================
package client

import (
	"time"

	"masterhttprelayvpn/internal/protocol"
)

func (s *SOCKSConnection) queueInboundPacket(packet protocol.Packet, maxBuffered int) ([]protocol.Packet, bool, bool) {
	s.reorderMu.Lock()
	defer s.reorderMu.Unlock()

	expected := s.expectedInboundSequenceLocked()
	if packet.Sequence < expected {
		return nil, true, false
	}
	if _, exists := s.PendingInbound[packet.Sequence]; exists {
		return nil, true, false
	}
	if len(s.PendingInbound) >= maxBuffered {
		return nil, false, true
	}

	s.PendingInbound[packet.Sequence] = PendingInboundPacket{
		Packet:   packet,
		QueuedAt: time.Now(),
	}

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
		pending, ok := s.PendingInbound[expected]
		if !ok {
			break
		}
		ready = append(ready, pending.Packet)
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
	for _, pending := range s.PendingInbound {
		if now.Sub(pending.QueuedAt) >= timeout {
			clear(s.PendingInbound)
			return true
		}
	}
	return false
}

func isReorderSequencedPacket(packetType protocol.PacketType) bool {
	switch packetType {
	case protocol.PacketTypeSOCKSData,
		protocol.PacketTypeSOCKSCloseRead,
		protocol.PacketTypeSOCKSCloseWrite,
		protocol.PacketTypeSOCKSRST:
		return true
	default:
		return false
	}
}
