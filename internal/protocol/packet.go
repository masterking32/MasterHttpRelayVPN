// ==============================================================================
// MasterHttpRelayVPN
// Author: MasterkinG32
// Github: https://github.com/masterking32
// Year: 2026
// ==============================================================================
package protocol

import (
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"net"
	"strconv"
	"strings"
	"time"
)

const CurrentVersion = 1

var (
	ErrInvalidPacketType   = errors.New("invalid packet type")
	ErrMissingClientKey    = errors.New("missing client session key")
	ErrMissingSOCKSID      = errors.New("missing socks id")
	ErrMissingSequence     = errors.New("missing sequence number")
	ErrPayloadNotAllowed   = errors.New("payload not allowed for packet type")
	ErrInvalidTargetPort   = errors.New("invalid target port")
	ErrInvalidTargetHost   = errors.New("invalid target host")
	ErrTargetNotAllowed    = errors.New("target not allowed for packet type")
	ErrMissingBatchID      = errors.New("missing batch id")
	ErrEmptyBatch          = errors.New("empty batch")
	ErrMixedClientKeyBatch = errors.New("batch contains multiple client session keys")
)

type Target struct {
	Host        string `json:"host"`
	Port        uint16 `json:"port"`
	AddressType byte   `json:"address_type,omitempty"`
}

func (t Target) Address() string {
	if t.Host == "" || t.Port == 0 {
		return ""
	}
	return net.JoinHostPort(t.Host, strconv.Itoa(int(t.Port)))
}

func (t Target) Validate() error {
	if strings.TrimSpace(t.Host) == "" {
		return ErrInvalidTargetHost
	}
	if t.Port == 0 {
		return ErrInvalidTargetPort
	}
	return nil
}

type Packet struct {
	Version          int        `json:"v"`
	ClientSessionKey string     `json:"client_session_key"`
	SOCKSID          uint64     `json:"socks_id,omitempty"`
	Type             PacketType `json:"type"`
	Sequence         uint64     `json:"seq,omitempty"`
	FragmentID       uint32     `json:"fragment_id,omitempty"`
	TotalFragments   uint32     `json:"total_fragments,omitempty"`
	Final            bool       `json:"final,omitempty"`
	CreatedAtUnixMS  int64      `json:"created_at_unix_ms"`
	Target           *Target    `json:"target,omitempty"`
	Payload          []byte     `json:"payload,omitempty"`
}

func NewPacket(clientSessionKey string, packetType PacketType) Packet {
	return Packet{
		Version:          CurrentVersion,
		ClientSessionKey: clientSessionKey,
		Type:             packetType,
		CreatedAtUnixMS:  time.Now().UnixMilli(),
	}
}

func (p Packet) Validate() error {
	if p.Version <= 0 {
		return fmt.Errorf("invalid packet version: %d", p.Version)
	}
	if !p.Type.Valid() {
		return ErrInvalidPacketType
	}
	if strings.TrimSpace(p.ClientSessionKey) == "" {
		return ErrMissingClientKey
	}
	if PacketTypeNeedsStreamID(p.Type) && p.SOCKSID == 0 {
		return ErrMissingSOCKSID
	}
	if PacketTypeNeedsSequence(p.Type) && p.Sequence == 0 {
		return ErrMissingSequence
	}
	if !PacketTypeAllowsPayload(p.Type) && len(p.Payload) > 0 {
		return ErrPayloadNotAllowed
	}

	switch p.Type {
	case PacketTypeSOCKSConnect:
		if p.Target == nil {
			return ErrTargetNotAllowed
		}
		if err := p.Target.Validate(); err != nil {
			return err
		}
	case PacketTypeSOCKSData,
		PacketTypeSOCKSDataAck,
		PacketTypeSOCKSCloseRead,
		PacketTypeSOCKSCloseWrite,
		PacketTypeSOCKSRST,
		PacketTypeSOCKSConnectAck,
		PacketTypeSOCKSConnectFail,
		PacketTypeSOCKSRuleSetDenied,
		PacketTypeSOCKSNetworkUnreachable,
		PacketTypeSOCKSHostUnreachable,
		PacketTypeSOCKSConnectionRefused,
		PacketTypeSOCKSTTLExpired,
		PacketTypeSOCKSCommandUnsupported,
		PacketTypeSOCKSAddressTypeUnsupported,
		PacketTypeSOCKSAuthFailed,
		PacketTypeSOCKSUpstreamUnavailable:
		if p.Target != nil {
			return ErrTargetNotAllowed
		}
	}

	if p.TotalFragments > 0 && p.FragmentID >= p.TotalFragments {
		return fmt.Errorf("invalid fragment range: id=%d total=%d", p.FragmentID, p.TotalFragments)
	}

	return nil
}

func (p Packet) IdentityKey() string {
	return fmt.Sprintf("%s:%d:%s:%d:%d", p.ClientSessionKey, p.SOCKSID, p.Type, p.Sequence, p.FragmentID)
}

func PacketIdentityKey(clientSessionKey string, socksID uint64, packetType PacketType, sequence uint64, fragmentID uint32) string {
	switch packetType {
	case PacketTypeSOCKSData, PacketTypeSOCKSDataAck:
		return fmt.Sprintf("%s:%d:%s:%d:%d", clientSessionKey, socksID, packetType, sequence, fragmentID)
	case PacketTypeSOCKSCloseRead,
		PacketTypeSOCKSCloseWrite,
		PacketTypeSOCKSRST,
		PacketTypeSOCKSConnect,
		PacketTypeSOCKSConnectAck,
		PacketTypeSOCKSConnectFail,
		PacketTypeSOCKSRuleSetDenied,
		PacketTypeSOCKSNetworkUnreachable,
		PacketTypeSOCKSHostUnreachable,
		PacketTypeSOCKSConnectionRefused,
		PacketTypeSOCKSTTLExpired,
		PacketTypeSOCKSCommandUnsupported,
		PacketTypeSOCKSAddressTypeUnsupported,
		PacketTypeSOCKSAuthFailed,
		PacketTypeSOCKSUpstreamUnavailable:
		return fmt.Sprintf("%s:%d:%s:%d", clientSessionKey, socksID, packetType, sequence)
	default:
		return fmt.Sprintf("%s:%d:%s", clientSessionKey, socksID, packetType)
	}
}

type Batch struct {
	Version          int      `json:"v"`
	BatchID          string   `json:"batch_id"`
	ClientSessionKey string   `json:"client_session_key"`
	CreatedAtUnixMS  int64    `json:"created_at_unix_ms"`
	Packets          []Packet `json:"packets"`
}

func NewBatch(clientSessionKey string, batchID string, packets []Packet) Batch {
	return Batch{
		Version:          CurrentVersion,
		BatchID:          batchID,
		ClientSessionKey: clientSessionKey,
		CreatedAtUnixMS:  time.Now().UnixMilli(),
		Packets:          packets,
	}
}

func NewBatchID() string {
	now := time.Now().UTC().Format("20060102T150405.000000000Z")
	random := make([]byte, 8)
	if _, err := rand.Read(random); err != nil {
		return fmt.Sprintf("%s_batch", now)
	}
	return fmt.Sprintf("%s_%s", now, hex.EncodeToString(random))
}

func (b Batch) Validate() error {
	if b.Version <= 0 {
		return fmt.Errorf("invalid batch version: %d", b.Version)
	}
	if strings.TrimSpace(b.BatchID) == "" {
		return ErrMissingBatchID
	}
	if strings.TrimSpace(b.ClientSessionKey) == "" {
		return ErrMissingClientKey
	}
	if len(b.Packets) == 0 {
		return ErrEmptyBatch
	}

	for _, packet := range b.Packets {
		if err := packet.Validate(); err != nil {
			return err
		}
		if packet.ClientSessionKey != b.ClientSessionKey {
			return ErrMixedClientKeyBatch
		}
	}

	return nil
}
