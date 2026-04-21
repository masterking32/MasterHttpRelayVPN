package server

import (
	"testing"

	"masterhttprelayvpn/internal/config"
	"masterhttprelayvpn/internal/protocol"
)

func TestDrainSessionOutboundLockedRespectsGlobalLimits(t *testing.T) {
	srv := &Server{
		cfg: config.Config{
			MaxPacketsPerBatch: 2,
			MaxBatchBytes:      10,
		},
	}

	session := &ClientSession{
		ClientSessionKey: "client-session",
		SOCKSConnections: map[uint64]*SOCKSState{
			1: {ID: 1},
			2: {ID: 2},
			3: {ID: 3},
		},
	}

	session.SOCKSConnections[1].OutboundQueue = []protocol.Packet{
		testDataPacket("client-session", 1, 1, "abcd"),
	}
	session.SOCKSConnections[1].QueuedBytes = 4

	session.SOCKSConnections[2].OutboundQueue = []protocol.Packet{
		testDataPacket("client-session", 2, 1, "efgh"),
	}
	session.SOCKSConnections[2].QueuedBytes = 4

	session.SOCKSConnections[3].OutboundQueue = []protocol.Packet{
		testDataPacket("client-session", 3, 1, "ijkl"),
	}
	session.SOCKSConnections[3].QueuedBytes = 4

	drained := srv.drainSessionOutboundLocked(session)
	if len(drained) != 2 {
		t.Fatalf("expected 2 drained packets, got %d", len(drained))
	}

	totalBytes := 0
	for _, packet := range drained {
		totalBytes += len(packet.Payload)
	}
	if totalBytes > srv.cfg.MaxBatchBytes {
		t.Fatalf("expected drained bytes <= %d, got %d", srv.cfg.MaxBatchBytes, totalBytes)
	}

	remainingPackets := 0
	for _, socksState := range session.SOCKSConnections {
		remainingPackets += len(socksState.OutboundQueue)
	}
	if remainingPackets != 1 {
		t.Fatalf("expected one packet to remain queued, got %d", remainingPackets)
	}
}

func TestSOCKSStateReleaseClearsQueueState(t *testing.T) {
	socksState := &SOCKSState{
		Target: &protocol.Target{Host: "example.com", Port: 443},
		OutboundQueue: []protocol.Packet{
			testDataPacket("client-session", 1, 1, "hello"),
			testDataPacket("client-session", 1, 2, "world"),
		},
		QueuedBytes: 10,
	}

	socksState.release()

	if socksState.Target != nil {
		t.Fatal("expected target to be cleared")
	}
	if len(socksState.OutboundQueue) != 0 {
		t.Fatalf("expected empty outbound queue, got %d items", len(socksState.OutboundQueue))
	}
	if socksState.QueuedBytes != 0 {
		t.Fatalf("expected queued bytes to be reset, got %d", socksState.QueuedBytes)
	}
}

func testDataPacket(clientSessionKey string, socksID uint64, sequence uint64, payload string) protocol.Packet {
	packet := protocol.NewPacket(clientSessionKey, protocol.PacketTypeSOCKSData)
	packet.SOCKSID = socksID
	packet.Sequence = sequence
	packet.Payload = []byte(payload)
	return packet
}
