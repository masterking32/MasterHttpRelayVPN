// ==============================================================================
// MasterHttpRelayVPN
// Author: MasterkinG32
// Github: https://github.com/masterking32
// Year: 2026
// ==============================================================================
package client

import (
	"context"
	"encoding/hex"
	"net"
	"sync"
	"sync/atomic"
	"time"

	"masterhttprelayvpn/internal/protocol"
)

type SOCKSConnection struct {
	ID                uint64
	ClientSessionKey  string
	ChunkPolicy       ChunkPolicy
	CreatedAt         time.Time
	LastActivityAt    time.Time
	ClientAddress     string
	TargetHost        string
	TargetPort        uint16
	TargetAddressType byte
	InitialPayload    []byte
	BufferedBytes     int
	NextSequence      uint64
	SOCKSAuthMethod   byte
	SOCKSUsername     string
	HandshakeDone     bool
	ConnectAccepted   bool
	ConnectFailure    string
	CloseReadSent     bool
	CloseWriteSent    bool
	ResetSent         bool

	LocalConn      net.Conn
	localWriteMu   sync.Mutex
	localCloseMu   sync.Mutex
	reorderMu      sync.Mutex
	localReadEOF   bool
	localWriteEOF  bool
	closedC        chan struct{}
	closeOnce      sync.Once
	connectResultC chan error
	queueMu        sync.Mutex
	OutboundQueue  []*SOCKSOutboundQueueItem
	QueuedBytes    int
	InFlight       map[string]*SOCKSOutboundQueueItem
	NextInboundSequence uint64
	PendingInbound      map[uint64]PendingInboundPacket
}

type PendingInboundPacket struct {
	Packet   protocol.Packet
	QueuedAt time.Time
}

func (s *SOCKSConnection) InitialPayloadHex() string {
	if len(s.InitialPayload) == 0 {
		return ""
	}
	return hex.EncodeToString(s.InitialPayload)
}

type SOCKSConnectionStore struct {
	nextID atomic.Uint64
	mu     sync.RWMutex
	items  map[uint64]*SOCKSConnection
}

func NewSOCKSConnectionStore() *SOCKSConnectionStore {
	return &SOCKSConnectionStore{
		items: make(map[uint64]*SOCKSConnection),
	}
}

func (s *SOCKSConnectionStore) New(clientSessionKey string, clientAddress string, chunkPolicy ChunkPolicy) *SOCKSConnection {
	id := s.nextID.Add(1)
	now := time.Now()
	socksConn := &SOCKSConnection{
		ID:               id,
		ClientSessionKey: clientSessionKey,
		ChunkPolicy:      chunkPolicy,
		CreatedAt:        now,
		LastActivityAt:   now,
		ClientAddress:    clientAddress,
		closedC:          make(chan struct{}),
		connectResultC:   make(chan error, 1),
		InFlight:         make(map[string]*SOCKSOutboundQueueItem),
		PendingInbound:   make(map[uint64]PendingInboundPacket),
	}

	s.mu.Lock()
	s.items[id] = socksConn
	s.mu.Unlock()
	return socksConn
}

func (s *SOCKSConnection) WaitForConnect(ctx context.Context, timeout time.Duration) error {
	timer := time.NewTimer(timeout)
	defer timer.Stop()

	select {
	case err := <-s.connectResultC:
		return err
	case <-timer.C:
		return ErrSOCKSConnectTimeout
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (s *SOCKSConnection) CompleteConnect(err error) {
	select {
	case s.connectResultC <- err:
	default:
	}
}

func (s *SOCKSConnection) WriteToLocal(payload []byte) error {
	s.localWriteMu.Lock()
	defer s.localWriteMu.Unlock()

	if s.LocalConn == nil || len(payload) == 0 {
		return nil
	}
	_, err := s.LocalConn.Write(payload)
	return err
}

func (s *SOCKSConnection) CloseLocal() error {
	var err error
	s.closeOnce.Do(func() {
		s.localWriteMu.Lock()
		defer s.localWriteMu.Unlock()
		if s.LocalConn != nil {
			err = s.LocalConn.Close()
		}
		close(s.closedC)
	})
	return err
}

func (s *SOCKSConnection) CloseLocalWrite() error {
	s.localCloseMu.Lock()
	defer s.localCloseMu.Unlock()

	if s.localWriteEOF {
		return nil
	}
	s.localWriteEOF = true

	if tcpConn, ok := s.LocalConn.(*net.TCPConn); ok {
		return tcpConn.CloseWrite()
	}
	return s.CloseLocal()
}

func (s *SOCKSConnection) CloseLocalRead() error {
	s.localCloseMu.Lock()
	defer s.localCloseMu.Unlock()

	if s.localReadEOF {
		return nil
	}
	s.localReadEOF = true

	if tcpConn, ok := s.LocalConn.(*net.TCPConn); ok {
		return tcpConn.CloseRead()
	}
	return nil
}

func (s *SOCKSConnection) MarkLocalReadEOF() {
	s.localCloseMu.Lock()
	s.localReadEOF = true
	s.localCloseMu.Unlock()
}

func (s *SOCKSConnection) BothLocalSidesClosed() bool {
	s.localCloseMu.Lock()
	defer s.localCloseMu.Unlock()
	return s.localReadEOF && s.localWriteEOF
}

func (s *SOCKSConnection) WaitUntilClosed(ctx context.Context) {
	select {
	case <-ctx.Done():
	case <-s.closedC:
	}
}

func (s *SOCKSConnection) ResetTransportState() {
	s.queueMu.Lock()
	for i := range s.OutboundQueue {
		s.OutboundQueue[i] = nil
	}
	s.OutboundQueue = nil
	s.QueuedBytes = 0
	clear(s.InFlight)
	s.queueMu.Unlock()

	s.InitialPayload = nil
	s.BufferedBytes = 0
	s.reorderMu.Lock()
	clear(s.PendingInbound)
	s.NextInboundSequence = 0
	s.reorderMu.Unlock()
}

func (s *SOCKSConnectionStore) Get(id uint64) *SOCKSConnection {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.items[id]
}

func (s *SOCKSConnectionStore) Delete(id uint64) {
	s.mu.Lock()
	item := s.items[id]
	delete(s.items, id)
	s.mu.Unlock()

	if item != nil {
		item.ResetTransportState()
		_ = item.CloseLocal()
	}
}

func (s *SOCKSConnectionStore) CloseAll() {
	s.mu.Lock()
	items := make([]*SOCKSConnection, 0, len(s.items))
	for _, item := range s.items {
		items = append(items, item)
	}
	s.items = make(map[uint64]*SOCKSConnection)
	s.mu.Unlock()

	for _, item := range items {
		item.ResetTransportState()
		_ = item.CloseLocal()
	}
}

func (s *SOCKSConnectionStore) Snapshot() []*SOCKSConnection {
	s.mu.RLock()
	defer s.mu.RUnlock()

	items := make([]*SOCKSConnection, 0, len(s.items))
	for _, item := range s.items {
		items = append(items, item)
	}
	return items
}
