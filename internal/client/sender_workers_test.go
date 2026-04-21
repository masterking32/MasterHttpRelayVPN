package client

import (
	"net"
	"testing"

	"masterhttprelayvpn/internal/config"
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

func TestBuildNextBatchRotatesAcrossConnections(t *testing.T) {
	cfg := config.Config{
		MaxChunkSize:          1024,
		MaxPacketsPerBatch:    1,
		MaxBatchBytes:         4096,
		WorkerCount:           1,
		MaxQueueBytesPerSOCKS: 4096,
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

	expected := []uint64{conn1.ID, conn2.ID, conn3.ID}
	for i, want := range expected {
		batch, selected := client.buildNextBatch()
		if len(batch.Packets) != 1 || len(selected) != 1 {
			t.Fatalf("iteration %d: expected one selected packet, got packets=%d selected=%d", i, len(batch.Packets), len(selected))
		}
		if got := batch.Packets[0].SOCKSID; got != want {
			t.Fatalf("iteration %d: expected socks_id=%d, got %d", i, want, got)
		}
	}
}
