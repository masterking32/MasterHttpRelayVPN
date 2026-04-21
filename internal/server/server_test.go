package server

import (
	"testing"
	"time"

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

func TestSOCKSStateInboundReorderQueuesUntilGapFilled(t *testing.T) {
	socksState := &SOCKSState{
		ConnectAcked:   true,
		PendingInbound: make(map[uint64][]PendingInboundPacket),
		MaxQueueBytes:  1024,
	}

	packet2 := testDataPacket("client-session", 1, 2, "two")
	ready, duplicate, overflow := socksState.queueInboundPacketLocked(packet2, time.Now(), 8)
	if duplicate || overflow {
		t.Fatalf("unexpected duplicate=%t overflow=%t", duplicate, overflow)
	}
	if len(ready) != 0 {
		t.Fatalf("expected no ready packets before sequence gap is filled, got %d", len(ready))
	}

	packet1 := testDataPacket("client-session", 1, 1, "one")
	ready, duplicate, overflow = socksState.queueInboundPacketLocked(packet1, time.Now(), 8)
	if duplicate || overflow {
		t.Fatalf("unexpected duplicate=%t overflow=%t", duplicate, overflow)
	}
	if len(ready) != 2 {
		t.Fatalf("expected 2 ready packets after filling sequence gap, got %d", len(ready))
	}
	if ready[0].Sequence != 1 || ready[1].Sequence != 2 {
		t.Fatalf("expected ordered sequences [1 2], got [%d %d]", ready[0].Sequence, ready[1].Sequence)
	}
}

func TestSOCKSStateInboundGapTimeout(t *testing.T) {
	socksState := &SOCKSState{
		PendingInbound: make(map[uint64][]PendingInboundPacket),
	}
	socksState.PendingInbound[3] = []PendingInboundPacket{{
		Packet:   testDataPacket("client-session", 1, 3, "late"),
		QueuedAt: time.Now().Add(-2 * time.Second),
	}}

	if !socksState.hasExpiredInboundGapLocked(time.Now(), 500*time.Millisecond) {
		t.Fatal("expected inbound gap timeout to trigger")
	}
	if len(socksState.PendingInbound) != 0 {
		t.Fatalf("expected pending inbound buffer to be cleared, got %d items", len(socksState.PendingInbound))
	}
}

func TestSOCKSStateInboundDataWaitsForConnect(t *testing.T) {
	socksState := &SOCKSState{
		PendingInbound: make(map[uint64][]PendingInboundPacket),
	}

	packet1 := testDataPacket("client-session", 1, 1, "one")
	ready, duplicate, overflow := socksState.queueInboundPacketLocked(packet1, time.Now(), 8)
	if duplicate || overflow {
		t.Fatalf("unexpected duplicate=%t overflow=%t", duplicate, overflow)
	}
	if len(ready) != 0 {
		t.Fatalf("expected packet to stay buffered before connect, got %d ready packets", len(ready))
	}

	socksState.ConnectAcked = true
	ready = socksState.drainReadyInboundLocked()
	if len(ready) != 1 {
		t.Fatalf("expected 1 ready packet after connect, got %d", len(ready))
	}
	if ready[0].Sequence != 1 {
		t.Fatalf("expected sequence 1, got %d", ready[0].Sequence)
	}
}

func TestSOCKSStateInboundReorderAllowsMultiplePacketTypesPerSequence(t *testing.T) {
	socksState := &SOCKSState{
		ConnectAcked:   true,
		PendingInbound: make(map[uint64][]PendingInboundPacket),
	}

	closeWrite := protocol.NewPacket("client-session", protocol.PacketTypeSOCKSCloseWrite)
	closeWrite.SOCKSID = 1
	closeWrite.Sequence = 2

	closeRead := protocol.NewPacket("client-session", protocol.PacketTypeSOCKSCloseRead)
	closeRead.SOCKSID = 1
	closeRead.Sequence = 2

	ready, duplicate, overflow := socksState.queueInboundPacketLocked(closeWrite, time.Now(), 8)
	if duplicate || overflow || len(ready) != 0 {
		t.Fatalf("expected first close packet to buffer, duplicate=%t overflow=%t ready=%d", duplicate, overflow, len(ready))
	}

	ready, duplicate, overflow = socksState.queueInboundPacketLocked(closeRead, time.Now(), 8)
	if duplicate || overflow || len(ready) != 0 {
		t.Fatalf("expected second close packet on same sequence to buffer, duplicate=%t overflow=%t ready=%d", duplicate, overflow, len(ready))
	}

	data := testDataPacket("client-session", 1, 1, "one")
	ready, duplicate, overflow = socksState.queueInboundPacketLocked(data, time.Now(), 8)
	if duplicate || overflow || len(ready) != 3 {
		t.Fatalf("expected data and both close packets to drain, duplicate=%t overflow=%t ready=%d", duplicate, overflow, len(ready))
	}
	if ready[0].Type != protocol.PacketTypeSOCKSData || ready[1].Type != protocol.PacketTypeSOCKSCloseRead || ready[2].Type != protocol.PacketTypeSOCKSCloseWrite {
		t.Fatalf("unexpected drain order: %s, %s, %s", ready[0].Type, ready[1].Type, ready[2].Type)
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
