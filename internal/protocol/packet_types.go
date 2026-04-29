// ==============================================================================
// MasterHttpRelayVPN
// Author: MasterkinG32
// Github: https://github.com/masterking32
// Year: 2026
// ==============================================================================
package protocol

type PacketType string

const (
	PacketTypeSOCKSConnect                PacketType = "socks_connect"
	PacketTypeSOCKSConnectAck             PacketType = "socks_connect_ack"
	PacketTypeSOCKSConnectFail            PacketType = "socks_connect_fail"
	PacketTypeSOCKSRuleSetDenied          PacketType = "socks_ruleset_denied"
	PacketTypeSOCKSNetworkUnreachable     PacketType = "socks_network_unreachable"
	PacketTypeSOCKSHostUnreachable        PacketType = "socks_host_unreachable"
	PacketTypeSOCKSConnectionRefused      PacketType = "socks_connection_refused"
	PacketTypeSOCKSTTLExpired             PacketType = "socks_ttl_expired"
	PacketTypeSOCKSCommandUnsupported     PacketType = "socks_command_unsupported"
	PacketTypeSOCKSAddressTypeUnsupported PacketType = "socks_address_type_unsupported"
	PacketTypeSOCKSAuthFailed             PacketType = "socks_auth_failed"
	PacketTypeSOCKSUpstreamUnavailable    PacketType = "socks_upstream_unavailable"
	PacketTypeSOCKSData                   PacketType = "socks_data"
	PacketTypeSOCKSDataAck                PacketType = "socks_data_ack"
	PacketTypeSOCKSCloseRead              PacketType = "socks_close_read"
	PacketTypeSOCKSCloseWrite             PacketType = "socks_close_write"
	PacketTypeSOCKSRST                    PacketType = "socks_rst"
	PacketTypePing                        PacketType = "ping"
	PacketTypePong                        PacketType = "pong"
)

func (p PacketType) String() string {
	return string(p)
}

func (p PacketType) Valid() bool {
	switch p {
	case PacketTypeSOCKSConnect,
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
		PacketTypeSOCKSUpstreamUnavailable,
		PacketTypeSOCKSData,
		PacketTypeSOCKSDataAck,
		PacketTypeSOCKSCloseRead,
		PacketTypeSOCKSCloseWrite,
		PacketTypeSOCKSRST,
		PacketTypePing,
		PacketTypePong:
		return true
	default:
		return false
	}
}

func PacketTypeNeedsStreamID(packetType PacketType) bool {
	switch packetType {
	case PacketTypePing, PacketTypePong:
		return false
	default:
		return packetType.Valid()
	}
}

func PacketTypeNeedsSequence(packetType PacketType) bool {
	switch packetType {
	case PacketTypeSOCKSData,
		PacketTypeSOCKSDataAck,
		PacketTypeSOCKSCloseRead,
		PacketTypeSOCKSCloseWrite,
		PacketTypeSOCKSRST:
		return true
	default:
		return false
	}
}

func PacketTypeAllowsPayload(packetType PacketType) bool {
	switch packetType {
	case PacketTypeSOCKSConnect,
		PacketTypeSOCKSConnectFail,
		PacketTypeSOCKSRuleSetDenied,
		PacketTypeSOCKSNetworkUnreachable,
		PacketTypeSOCKSHostUnreachable,
		PacketTypeSOCKSConnectionRefused,
		PacketTypeSOCKSTTLExpired,
		PacketTypeSOCKSCommandUnsupported,
		PacketTypeSOCKSAddressTypeUnsupported,
		PacketTypeSOCKSAuthFailed,
		PacketTypeSOCKSUpstreamUnavailable,
		PacketTypeSOCKSData,
		PacketTypePing,
		PacketTypePong:
		return true
	default:
		return false
	}
}
