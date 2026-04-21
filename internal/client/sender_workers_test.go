package client

import (
	"net"
	"testing"
	"time"

	"masterhttprelayvpn/internal/config"
	"masterhttprelayvpn/internal/protocol"
)

func TestSOCKSConnectionStoreDeleteClearsTransportState(t *testing.T) {
	store := NewSOCKSConnectionStore()
	chunkPolicy := ChunkPolicy{
		MaxChunkSize:          1024,
		MaxPacketsPerBatch:    4,
		MaxBatchBytes:         4096,
		WorkerCount:           1,
		MaxQueueBytesPerSOCKS: 4096,
	}

	localConn, peerConn := net.Pipe()
	defer peerConn.Close()

	socksConn := store.New("client-session", "127.0.0.1:1000", chunkPolicy)
	socksConn.LocalConn = localConn
	socksConn.InitialPayload = []byte("initial-payload")
	socksConn.BufferedBytes = len(socksConn.InitialPayload)

	if err := socksConn.EnqueuePacket(socksConn.BuildSOCKSDataPacket([]byte("hello"), false)); err != nil {
		t.Fatalf("enqueue first packet: %v", err)
	}
	if err := socksConn.EnqueuePacket(socksConn.BuildSOCKSDataPacket([]byte("world"), false)); err != nil {
		t.Fatalf("enqueue second packet: %v", err)
	}

	item := socksConn.DequeuePacket()
	if item == nil {
		t.Fatal("expected dequeued item")
	}
	socksConn.MarkInFlight([]*SOCKSOutboundQueueItem{item})

	store.Delete(socksConn.ID)

	if got := store.Get(socksConn.ID); got != nil {
		t.Fatal("expected connection to be removed from store")
	}
	if len(socksConn.OutboundQueue) != 0 {
		t.Fatalf("expected empty outbound queue, got %d items", len(socksConn.OutboundQueue))
	}
	if socksConn.QueuedBytes != 0 {
		t.Fatalf("expected zero queued bytes, got %d", socksConn.QueuedBytes)
	}
	if len(socksConn.InFlight) != 0 {
		t.Fatalf("expected empty inflight map, got %d items", len(socksConn.InFlight))
	}
	if socksConn.InitialPayload != nil {
		t.Fatal("expected initial payload to be cleared")
	}
	if socksConn.BufferedBytes != 0 {
		t.Fatalf("expected buffered bytes to be reset, got %d", socksConn.BufferedBytes)
	}

	select {
	case <-socksConn.closedC:
	default:
		t.Fatal("expected local connection close signal")
	}
}

func TestSOCKSConnectionInboundReorderQueuesAndDrainsInOrder(t *testing.T) {
	socksConn := &SOCKSConnection{
		ConnectAccepted: true,
		PendingInbound:  make(map[uint64]PendingInboundPacket),
	}

	packet2 := protocol.NewPacket("client-session", protocol.PacketTypeSOCKSData)
	packet2.SOCKSID = 1
	packet2.Sequence = 2
	packet2.Payload = []byte("two")

	ready, duplicate, overflow := socksConn.queueInboundPacket(packet2, 8)
	if duplicate || overflow {
		t.Fatalf("unexpected duplicate=%t overflow=%t", duplicate, overflow)
	}
	if len(ready) != 0 {
		t.Fatalf("expected no ready packets before gap is filled, got %d", len(ready))
	}

	packet1 := protocol.NewPacket("client-session", protocol.PacketTypeSOCKSData)
	packet1.SOCKSID = 1
	packet1.Sequence = 1
	packet1.Payload = []byte("one")

	ready, duplicate, overflow = socksConn.queueInboundPacket(packet1, 8)
	if duplicate || overflow {
		t.Fatalf("unexpected duplicate=%t overflow=%t", duplicate, overflow)
	}
	if len(ready) != 2 {
		t.Fatalf("expected 2 ready packets after filling gap, got %d", len(ready))
	}
	if ready[0].Sequence != 1 || ready[1].Sequence != 2 {
		t.Fatalf("expected ordered sequences [1 2], got [%d %d]", ready[0].Sequence, ready[1].Sequence)
	}
}

func TestSOCKSConnectionInboundGapTimeout(t *testing.T) {
	socksConn := &SOCKSConnection{
		PendingInbound: make(map[uint64]PendingInboundPacket),
	}
	socksConn.PendingInbound[5] = PendingInboundPacket{
		Packet:   protocol.Packet{Sequence: 5},
		QueuedAt: time.Now().Add(-2 * time.Second),
	}

	if !socksConn.hasExpiredInboundGap(500 * time.Millisecond) {
		t.Fatal("expected inbound gap timeout to trigger")
	}
	if len(socksConn.PendingInbound) != 0 {
		t.Fatalf("expected pending inbound buffer to be cleared, got %d items", len(socksConn.PendingInbound))
	}
}

func TestSOCKSConnectionInboundDataWaitsForConnectAck(t *testing.T) {
	socksConn := &SOCKSConnection{
		PendingInbound: make(map[uint64]PendingInboundPacket),
	}

	packet1 := protocol.NewPacket("client-session", protocol.PacketTypeSOCKSData)
	packet1.SOCKSID = 1
	packet1.Sequence = 1
	packet1.Payload = []byte("one")

	ready, duplicate, overflow := socksConn.queueInboundPacket(packet1, 8)
	if duplicate || overflow {
		t.Fatalf("unexpected duplicate=%t overflow=%t", duplicate, overflow)
	}
	if len(ready) != 0 {
		t.Fatalf("expected buffered packet before connect ack, got %d ready packets", len(ready))
	}

	socksConn.ConnectAccepted = true
	ready = socksConn.activateInboundDrain()
	if len(ready) != 1 {
		t.Fatalf("expected 1 ready packet after connect ack, got %d", len(ready))
	}
	if ready[0].Sequence != 1 {
		t.Fatalf("expected sequence 1, got %d", ready[0].Sequence)
	}
}

func TestBuildNextBatchRotatesAcrossConnections(t *testing.T) {
	cfg := config.Config{
		MaxChunkSize:          1024,
		MaxPacketsPerBatch:    1,
		MaxBatchBytes:         4096,
		WorkerCount:           1,
		MaxQueueBytesPerSOCKS: 4096,
		HTTPBatchRandomize:    false,
	}

	client := New(cfg, nil)
	client.chunkPolicy = newChunkPolicy(cfg)

	conn1 := client.socksConnections.New(client.clientSessionKey, "127.0.0.1:1001", client.chunkPolicy)
	conn2 := client.socksConnections.New(client.clientSessionKey, "127.0.0.1:1002", client.chunkPolicy)
	conn3 := client.socksConnections.New(client.clientSessionKey, "127.0.0.1:1003", client.chunkPolicy)

	for _, socksConn := range []*SOCKSConnection{conn1, conn2, conn3} {
		if err := socksConn.EnqueuePacket(socksConn.BuildSOCKSDataPacket([]byte("x"), false)); err != nil {
			t.Fatalf("enqueue packet for socks_id=%d: %v", socksConn.ID, err)
		}
	}

	seen := make(map[uint64]bool)
	for i := 0; i < 3; i++ {
		batch, selected := client.buildNextBatch()
		if len(batch.Packets) != 1 || len(selected) != 1 {
			t.Fatalf("iteration %d: expected one selected packet, got packets=%d selected=%d", i, len(batch.Packets), len(selected))
		}
		got := batch.Packets[0].SOCKSID
		if seen[got] {
			t.Fatalf("iteration %d: duplicate socks_id=%d selected before all queues were drained", i, got)
		}
		seen[got] = true
	}
	if len(seen) != 3 {
		t.Fatalf("expected all 3 socks connections to be selected once, got %d unique selections", len(seen))
	}
}
