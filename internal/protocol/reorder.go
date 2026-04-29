// ==============================================================================
// MasterHttpRelayVPN
// Author: MasterkinG32
// Github: https://github.com/masterking32
// Year: 2026
// ==============================================================================
package protocol

import "time"

type PendingPacket struct {
	Packet   Packet
	QueuedAt time.Time
}

func ContainsPendingPacket(pendingPackets []PendingPacket, packet Packet) bool {
	for _, pending := range pendingPackets {
		if pending.Packet.Type == packet.Type &&
			pending.Packet.FragmentID == packet.FragmentID &&
			pending.Packet.TotalFragments == packet.TotalFragments {
			return true
		}
	}
	return false
}

func BufferedPendingPacketCount(pending map[uint64][]PendingPacket) int {
	total := 0
	for _, pendingPackets := range pending {
		total += len(pendingPackets)
	}
	return total
}

func SortPendingPackets(pendingPackets []PendingPacket) {
	for i := 1; i < len(pendingPackets); i++ {
		current := pendingPackets[i]
		j := i - 1
		for ; j >= 0 && PacketSortOrder(current.Packet.Type) < PacketSortOrder(pendingPackets[j].Packet.Type); j-- {
			pendingPackets[j+1] = pendingPackets[j]
		}
		pendingPackets[j+1] = current
	}
}

func PacketSortOrder(packetType PacketType) int {
	switch packetType {
	case PacketTypeSOCKSData:
		return 0
	case PacketTypeSOCKSCloseRead:
		return 1
	case PacketTypeSOCKSCloseWrite:
		return 2
	case PacketTypeSOCKSRST:
		return 3
	default:
		return 4
	}
}

func IsReorderSequencedPacket(packetType PacketType) bool {
	switch packetType {
	case PacketTypeSOCKSData,
		PacketTypeSOCKSCloseRead,
		PacketTypeSOCKSCloseWrite,
		PacketTypeSOCKSRST:
		return true
	default:
		return false
	}
}
