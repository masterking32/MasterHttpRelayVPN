// ==============================================================================
// MasterHttpRelayVPN
// Author: MasterkinG32
// Github: https://github.com/masterking32
// Year: 2026
// ==============================================================================
package client

import "masterhttprelayvpn/internal/protocol"

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
	if len(s.InitialPayload) > 0 {
		packet.Payload = append([]byte(nil), s.InitialPayload...)
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
