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
	pendingForSequence := s.PendingInbound[packet.Sequence]
	if containsPendingInboundPacket(pendingForSequence, packet) {
		return nil, true, false
	}
	if bufferedInboundPacketCount(s.PendingInbound) >= maxBuffered {
		return nil, false, true
	}

	s.PendingInbound[packet.Sequence] = append(s.PendingInbound[packet.Sequence], PendingInboundPacket{
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
		sortPendingInboundPackets(pendingPackets)
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

func containsPendingInboundPacket(pendingPackets []PendingInboundPacket, packet protocol.Packet) bool {
	for _, pending := range pendingPackets {
		if pending.Packet.Type == packet.Type &&
			pending.Packet.FragmentID == packet.FragmentID &&
			pending.Packet.TotalFragments == packet.TotalFragments {
			return true
		}
	}
	return false
}

func bufferedInboundPacketCount(pending map[uint64][]PendingInboundPacket) int {
	total := 0
	for _, pendingPackets := range pending {
		total += len(pendingPackets)
	}
	return total
}

func sortPendingInboundPackets(pendingPackets []PendingInboundPacket) {
	for i := 1; i < len(pendingPackets); i++ {
		current := pendingPackets[i]
		j := i - 1
		for ; j >= 0 && inboundPacketSortOrder(current.Packet.Type) < inboundPacketSortOrder(pendingPackets[j].Packet.Type); j-- {
			pendingPackets[j+1] = pendingPackets[j]
		}
		pendingPackets[j+1] = current
	}
}

func inboundPacketSortOrder(packetType protocol.PacketType) int {
	switch packetType {
	case protocol.PacketTypeSOCKSData:
		return 0
	case protocol.PacketTypeSOCKSCloseRead:
		return 1
	case protocol.PacketTypeSOCKSCloseWrite:
		return 2
	case protocol.PacketTypeSOCKSRST:
		return 3
	default:
		return 4
	}
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
