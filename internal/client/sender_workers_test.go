package client

import (
	"net"
	"testing"
	"time"

	"masterhttprelayvpn/internal/config"
	"masterhttprelayvpn/internal/protocol"
)

func testClientConfig() config.Config {
	return config.Config{
		MaxChunkSize:               1024,
		MaxPacketsPerBatch:         4,
		MaxBatchBytes:              4096,
		WorkerCount:                2,
		MaxConcurrentBatches:       2,
		MaxPacketsPerSOCKSPerBatch: 1,
		MuxRotateEveryBatches:      1,
		MuxBurstThresholdBytes:     1024,
		WorkerPollIntervalMS:       200,
		IdlePollIntervalMS:         1000,
		PingWarmThresholdMS:        5000,
		PingBackoffBaseMS:          5000,
		PingBackoffStepMS:          5000,
		PingMaxIntervalMS:          60000,
		MaxQueueBytesPerSOCKS:      4096,
		HTTPBatchRandomize:         false,
	}
}

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
	socksConn.BufferedBytes = len("initial-payload")

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
		PendingInbound:  make(map[uint64][]protocol.PendingPacket),
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
		PendingInbound: make(map[uint64][]protocol.PendingPacket),
	}
	socksConn.PendingInbound[5] = []protocol.PendingPacket{{
		Packet:   protocol.Packet{Sequence: 5},
		QueuedAt: time.Now().Add(-2 * time.Second),
	}}

	if !socksConn.hasExpiredInboundGap(500 * time.Millisecond) {
		t.Fatal("expected inbound gap timeout to trigger")
	}
	if len(socksConn.PendingInbound) != 0 {
		t.Fatalf("expected pending inbound buffer to be cleared, got %d items", len(socksConn.PendingInbound))
	}
}

func TestSOCKSConnectionInboundDataWaitsForConnectAck(t *testing.T) {
	socksConn := &SOCKSConnection{
		PendingInbound: make(map[uint64][]protocol.PendingPacket),
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
	cfg := testClientConfig()
	cfg.MaxPacketsPerBatch = 1
	cfg.WorkerCount = 1
	cfg.MaxConcurrentBatches = 1

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
		connections := client.socksConnections.Snapshot()
		batch, selected := client.buildNextBatch(connections, queuedBytesAcross(connections))
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

func TestBuildNextBatchHonorsPerSOCKSPacketLimit(t *testing.T) {
	cfg := testClientConfig()

	client := New(cfg, nil)
	client.chunkPolicy = newChunkPolicy(cfg)

	conn1 := client.socksConnections.New(client.clientSessionKey, "127.0.0.1:1001", client.chunkPolicy)
	conn2 := client.socksConnections.New(client.clientSessionKey, "127.0.0.1:1002", client.chunkPolicy)

	for i := 0; i < 3; i++ {
		if err := conn1.EnqueuePacket(conn1.BuildSOCKSDataPacket([]byte("a"), false)); err != nil {
			t.Fatalf("enqueue conn1 packet %d: %v", i, err)
		}
	}
	if err := conn2.EnqueuePacket(conn2.BuildSOCKSDataPacket([]byte("b"), false)); err != nil {
		t.Fatalf("enqueue conn2 packet: %v", err)
	}

	connections := client.socksConnections.Snapshot()
	batch, selected := client.buildNextBatch(connections, queuedBytesAcross(connections))
	if len(batch.Packets) != 2 || len(selected) != 2 {
		t.Fatalf("expected 2 selected packets, got packets=%d selected=%d", len(batch.Packets), len(selected))
	}

	counts := map[uint64]int{}
	for _, packet := range batch.Packets {
		counts[packet.SOCKSID]++
	}
	if counts[conn1.ID] != 1 {
		t.Fatalf("expected conn1 to contribute exactly 1 packet, got %d", counts[conn1.ID])
	}
	if counts[conn2.ID] != 1 {
		t.Fatalf("expected conn2 to contribute exactly 1 packet, got %d", counts[conn2.ID])
	}
}

func TestEffectiveConcurrentBatchesUsesBurstThreshold(t *testing.T) {
	cfg := testClientConfig()
	cfg.WorkerCount = 4
	cfg.MaxConcurrentBatches = 3
	cfg.MuxBurstThresholdBytes = 4096

	client := New(cfg, nil)
	if got := client.effectiveConcurrentBatches(1024); got != 1 {
		t.Fatalf("expected low-load concurrency of 1, got %d", got)
	}
	if got := client.effectiveConcurrentBatches(4096); got != 3 {
		t.Fatalf("expected burst concurrency of 3, got %d", got)
	}
}

func TestBuildPollBatchSkipsWhenTransportBusy(t *testing.T) {
	cfg := testClientConfig()
	client := New(cfg, nil)
	client.chunkPolicy = newChunkPolicy(cfg)

	socksConn := client.socksConnections.New(client.clientSessionKey, "127.0.0.1:1001", client.chunkPolicy)
	if err := socksConn.EnqueuePacket(socksConn.BuildSOCKSDataPacket([]byte("busy"), false)); err != nil {
		t.Fatalf("enqueue packet: %v", err)
	}

	batch, ok := client.buildPollBatch(client.socksConnections.Snapshot(), queuedBytesAcross(client.socksConnections.Snapshot()))
	if ok || len(batch.Packets) != 0 {
		t.Fatal("expected poll batch to be suppressed while queued payload exists")
	}
}

func TestBuildPollBatchAllowsOnlySinglePingInFlight(t *testing.T) {
	cfg := testClientConfig()
	client := New(cfg, nil)
	client.chunkPolicy = newChunkPolicy(cfg)
	client.socksConnections.New(client.clientSessionKey, "127.0.0.1:1001", client.chunkPolicy)
	client.noteMeaningfulActivity(time.Now().Add(-10 * time.Second))

	batch, ok := client.buildPollBatch(client.socksConnections.Snapshot(), 0)
	if !ok || len(batch.Packets) != 1 || batch.Packets[0].Type != protocol.PacketTypePing {
		t.Fatal("expected first idle batch to be a ping")
	}

	batch, ok = client.buildPollBatch(client.socksConnections.Snapshot(), 0)
	if ok || len(batch.Packets) != 0 {
		t.Fatal("expected second ping to be suppressed while first ping is still in flight")
	}
}

func TestBuildPollBatchAllowsSessionPingWithoutActiveConnections(t *testing.T) {
	cfg := testClientConfig()
	client := New(cfg, nil)

	now := time.Now()
	client.noteMeaningfulActivity(now.Add(-10 * time.Second))
	client.nextPingDueUnixMS.Store(now.Add(-1 * time.Second).UnixMilli())

	batch, ok := client.buildPollBatch(nil, 0)
	if !ok || len(batch.Packets) != 1 || batch.Packets[0].Type != protocol.PacketTypePing {
		t.Fatal("expected session-level ping even without active socks connections")
	}
}

func TestBuildPollBatchSkipsWithoutSessionActivity(t *testing.T) {
	cfg := testClientConfig()
	client := New(cfg, nil)
	client.nextPingDueUnixMS.Store(time.Now().Add(-1 * time.Second).UnixMilli())

	batch, ok := client.buildPollBatch(nil, 0)
	if ok || len(batch.Packets) != 0 {
		t.Fatal("expected ping to stay suppressed before the session has any real activity")
	}
}

func TestShouldSendPingWhenIdleIntervalHasElapsed(t *testing.T) {
	cfg := testClientConfig()
	client := New(cfg, nil)
	client.chunkPolicy = newChunkPolicy(cfg)
	client.socksConnections.New(client.clientSessionKey, "127.0.0.1:1001", client.chunkPolicy)

	now := time.Now()
	client.nextPingDueUnixMS.Store(now.Add(-2 * time.Second).UnixMilli())
	if !client.shouldSendPing(client.socksConnections.Snapshot(), 0, now) {
		t.Fatal("expected ping to be due after idle interval elapsed")
	}
}

func TestShouldNotSendPingBeforeIdleInterval(t *testing.T) {
	cfg := testClientConfig()
	client := New(cfg, nil)
	client.chunkPolicy = newChunkPolicy(cfg)
	client.socksConnections.New(client.clientSessionKey, "127.0.0.1:1001", client.chunkPolicy)

	now := time.Now()
	client.nextPingDueUnixMS.Store(now.Add(500 * time.Millisecond).UnixMilli())
	if client.shouldSendPing(client.socksConnections.Snapshot(), 0, now) {
		t.Fatal("expected ping to stay suppressed until idle interval elapses")
	}
}

func TestShouldSendPingWithOnlyInFlightPackets(t *testing.T) {
	cfg := testClientConfig()
	client := New(cfg, nil)
	client.chunkPolicy = newChunkPolicy(cfg)
	socksConn := client.socksConnections.New(client.clientSessionKey, "127.0.0.1:1001", client.chunkPolicy)

	packet := socksConn.BuildSOCKSDataPacket([]byte("hello"), false)
	item := &SOCKSOutboundQueueItem{
		IdentityKey: protocol.PacketIdentityKey(
			packet.ClientSessionKey,
			packet.SOCKSID,
			packet.Type,
			packet.Sequence,
			packet.FragmentID,
		),
		Packet:      packet,
		QueuedAt:    time.Now(),
		SentAt:      time.Now(),
		PayloadSize: len(packet.Payload),
	}
	socksConn.MarkInFlight([]*SOCKSOutboundQueueItem{item})

	now := time.Now()
	client.noteMeaningfulActivity(now.Add(-10 * time.Second))
	client.nextPingDueUnixMS.Store(now.Add(-1 * time.Second).UnixMilli())

	if !client.shouldSendPing(client.socksConnections.Snapshot(), 0, now) {
		t.Fatal("expected ping to be allowed while only in-flight packets remain")
	}
}

func TestIdleIntervalForStreakBacksOffWithIdlePongs(t *testing.T) {
	cfg := testClientConfig()
	client := New(cfg, nil)

	if got := client.idleIntervalForStreak(0); got != 5*time.Second {
		t.Fatalf("expected base backoff interval, got %v", got)
	}

	if got := client.idleIntervalForStreak(1); got != 10*time.Second {
		t.Fatalf("expected first stepped backoff interval, got %v", got)
	}

	if got := client.idleIntervalForStreak(20); got != 60*time.Second {
		t.Fatalf("expected capped backoff interval, got %v", got)
	}
}

func TestNoteMeaningfulActivitySetsBusyState(t *testing.T) {
	cfg := testClientConfig()
	client := New(cfg, nil)
	now := time.Now()

	client.noteMeaningfulActivity(now)

	if got := client.pingState.Load(); got != pingStateBusy {
		t.Fatalf("expected busy ping state, got %d", got)
	}
	if client.nextPingDueUnixMS.Load() <= now.UnixMilli() {
		t.Fatal("expected next ping due to be scheduled after meaningful activity")
	}
}

func TestCompletePingWithPongIncrementsStreakOnlyWithoutRealTraffic(t *testing.T) {
	cfg := testClientConfig()
	client := New(cfg, nil)
	now := time.Now()

	client.noteMeaningfulActivity(now.Add(-10 * time.Second))
	if !client.tryBeginPing(now) {
		t.Fatal("expected ping to start")
	}
	client.completePingWithPong()
	if got := client.idlePongStreak.Load(); got != 1 {
		t.Fatalf("expected pong streak to increment to 1, got %d", got)
	}
	if got := client.pingState.Load(); got != pingStateBackoffIdle {
		t.Fatalf("expected backoff idle ping state, got %d", got)
	}
	nextDue := client.nextPingDueUnixMS.Load()
	if nextDue <= now.UnixMilli() {
		t.Fatal("expected next ping due to be scheduled in the future after idle pong")
	}

	client.noteMeaningfulActivity(now.Add(1 * time.Second))
	if !client.tryBeginPing(now.Add(2 * time.Second)) {
		t.Fatal("expected second ping to start")
	}
	client.noteMeaningfulActivity(now.Add(3 * time.Second))
	client.completePingWithPong()
	if got := client.idlePongStreak.Load(); got != 0 {
		t.Fatalf("expected pong streak reset after real traffic, got %d", got)
	}
	if got := client.pingState.Load(); got != pingStateBusy {
		t.Fatalf("expected busy ping state after meaningful traffic, got %d", got)
	}
	if client.nextPingDueUnixMS.Load() <= now.UnixMilli() {
		t.Fatal("expected next ping due to be rescheduled after meaningful traffic")
	}
}

func TestCompletePingWithPongStaysAggressiveBeforeWarmThreshold(t *testing.T) {
	cfg := testClientConfig()
	client := New(cfg, nil)
	now := time.Now()

	client.noteMeaningfulActivity(now.Add(-3 * time.Second))
	if !client.tryBeginPing(now) {
		t.Fatal("expected ping to start")
	}

	client.completePingWithPong()

	if got := client.idlePongStreak.Load(); got != 0 {
		t.Fatalf("expected pong streak to stay at 0 before warm threshold, got %d", got)
	}
	if got := client.pingState.Load(); got != pingStateAggressiveIdle {
		t.Fatalf("expected aggressive idle ping state before warm threshold, got %d", got)
	}

	nextDue := client.nextPingDueUnixMS.Load()
	expectedMin := now.Add(900 * time.Millisecond).UnixMilli()
	expectedMax := now.Add(1100 * time.Millisecond).UnixMilli()
	if nextDue < expectedMin || nextDue > expectedMax {
		t.Fatalf("expected aggressive next ping around idle interval, got %d", nextDue)
	}
}

func TestFailPingReturnsToAggressiveIdle(t *testing.T) {
	cfg := testClientConfig()
	client := New(cfg, nil)
	now := time.Now()

	client.noteMeaningfulActivity(now.Add(-10 * time.Second))
	client.idlePongStreak.Store(3)
	client.setPingState(pingStateBackoffIdle)

	client.failPing()

	if got := client.idlePongStreak.Load(); got != 0 {
		t.Fatalf("expected pong streak reset after ping failure, got %d", got)
	}
	if got := client.pingState.Load(); got != pingStateAggressiveIdle {
		t.Fatalf("expected aggressive idle ping state after failure, got %d", got)
	}
	if client.nextPingDueUnixMS.Load() <= time.Now().UnixMilli() {
		t.Fatal("expected next ping due to be rescheduled after ping failure")
	}
}

func TestInboundReorderAllowsCloseReadAndCloseWriteOnSameSequence(t *testing.T) {
	cfg := testClientConfig()
	client := New(cfg, nil)
	client.chunkPolicy = newChunkPolicy(cfg)

	socksConn := client.socksConnections.New(client.clientSessionKey, "127.0.0.1:1001", client.chunkPolicy)
	socksConn.ConnectAccepted = true

	closeWrite := protocol.NewPacket(client.clientSessionKey, protocol.PacketTypeSOCKSCloseWrite)
	closeWrite.SOCKSID = socksConn.ID
	closeWrite.Sequence = 2

	closeRead := protocol.NewPacket(client.clientSessionKey, protocol.PacketTypeSOCKSCloseRead)
	closeRead.SOCKSID = socksConn.ID
	closeRead.Sequence = 2

	ready, duplicate, overflow := socksConn.queueInboundPacket(closeWrite, 8)
	if duplicate || overflow || len(ready) != 0 {
		t.Fatalf("expected first close packet to buffer, duplicate=%t overflow=%t ready=%d", duplicate, overflow, len(ready))
	}

	ready, duplicate, overflow = socksConn.queueInboundPacket(closeRead, 8)
	if duplicate || overflow || len(ready) != 0 {
		t.Fatalf("expected second close packet on same sequence to buffer, duplicate=%t overflow=%t ready=%d", duplicate, overflow, len(ready))
	}

	data := protocol.NewPacket(client.clientSessionKey, protocol.PacketTypeSOCKSData)
	data.SOCKSID = socksConn.ID
	data.Sequence = 1
	data.Payload = []byte("ok")

	ready, duplicate, overflow = socksConn.queueInboundPacket(data, 8)
	if duplicate || overflow || len(ready) != 3 {
		t.Fatalf("expected data and both close packets to drain, duplicate=%t overflow=%t ready=%d", duplicate, overflow, len(ready))
	}
	if ready[0].Type != protocol.PacketTypeSOCKSData || ready[1].Type != protocol.PacketTypeSOCKSCloseRead || ready[2].Type != protocol.PacketTypeSOCKSCloseWrite {
		t.Fatalf("unexpected drain order: %s, %s, %s", ready[0].Type, ready[1].Type, ready[2].Type)
	}
}
