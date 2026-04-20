// ==============================================================================
// MasterHttpRelayVPN
// Author: MasterkinG32
// Github: https://github.com/masterking32
// Year: 2026
// ==============================================================================
package client

import (
	"encoding/hex"
	"sync"
	"sync/atomic"
	"time"
)

type Stream struct {
	ID                uint64
	ClientSessionKey  string
	CreatedAt         time.Time
	LastActivityAt    time.Time
	ClientAddress     string
	TargetHost        string
	TargetPort        uint16
	TargetAddressType byte
	InitialPayload    []byte
	BufferedBytes     int
	SOCKSAuthMethod   byte
	SOCKSUsername     string
	HandshakeDone     bool
	ConnectAccepted   bool
}

func (s *Stream) InitialPayloadHex() string {
	if len(s.InitialPayload) == 0 {
		return ""
	}
	return hex.EncodeToString(s.InitialPayload)
}

type StreamStore struct {
	nextID atomic.Uint64
	mu     sync.RWMutex
	items  map[uint64]*Stream
}

func NewStreamStore() *StreamStore {
	return &StreamStore{
		items: make(map[uint64]*Stream),
	}
}

func (s *StreamStore) New(clientSessionKey string, clientAddress string) *Stream {
	id := s.nextID.Add(1)
	now := time.Now()
	stream := &Stream{
		ID:               id,
		ClientSessionKey: clientSessionKey,
		CreatedAt:        now,
		LastActivityAt:   now,
		ClientAddress:    clientAddress,
	}

	s.mu.Lock()
	s.items[id] = stream
	s.mu.Unlock()
	return stream
}

func (s *StreamStore) Delete(id uint64) {
	s.mu.Lock()
	delete(s.items, id)
	s.mu.Unlock()
}
