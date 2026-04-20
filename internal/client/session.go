// ==============================================================================
// MasterHttpRelayVPN
// Author: MasterkinG32
// Github: https://github.com/masterking32
// Year: 2026
// ==============================================================================
package client

import (
	"encoding/hex"
	"net"
	"sync"
	"sync/atomic"
	"time"
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
	connectResultC chan error
	queueMu        sync.Mutex
	OutboundQueue  []*SOCKSOutboundQueueItem
	QueuedBytes    int
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
		connectResultC:   make(chan error, 1),
	}

	s.mu.Lock()
	s.items[id] = socksConn
	s.mu.Unlock()
	return socksConn
}

func (s *SOCKSConnection) WaitForConnect(timeout time.Duration) error {
	select {
	case err := <-s.connectResultC:
		return err
	case <-time.After(timeout):
		return ErrSOCKSConnectTimeout
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
	s.localWriteMu.Lock()
	defer s.localWriteMu.Unlock()

	if s.LocalConn == nil {
		return nil
	}
	return s.LocalConn.Close()
}

func (s *SOCKSConnectionStore) Get(id uint64) *SOCKSConnection {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.items[id]
}

func (s *SOCKSConnectionStore) Delete(id uint64) {
	s.mu.Lock()
	delete(s.items, id)
	s.mu.Unlock()
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
