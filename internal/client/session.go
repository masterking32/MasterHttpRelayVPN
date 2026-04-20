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

type Session struct {
	ID              uint64
	CreatedAt       time.Time
	LastActivityAt  time.Time
	ClientAddr      string
	TargetHost      string
	TargetPort      uint16
	AddressType     byte
	InitialPayload  []byte
	BytesCaptured   int
	AuthMethod      byte
	UsernameUsed    string
	HandshakeDone   bool
	ConnectAccepted bool
}

func (s *Session) InitialPayloadHex() string {
	if len(s.InitialPayload) == 0 {
		return ""
	}
	return hex.EncodeToString(s.InitialPayload)
}

type SessionStore struct {
	nextID atomic.Uint64
	mu     sync.RWMutex
	items  map[uint64]*Session
}

func NewSessionStore() *SessionStore {
	return &SessionStore{
		items: make(map[uint64]*Session),
	}
}

func (s *SessionStore) New(clientAddr string) *Session {
	id := s.nextID.Add(1)
	now := time.Now()
	session := &Session{
		ID:             id,
		CreatedAt:      now,
		LastActivityAt: now,
		ClientAddr:     clientAddr,
	}

	s.mu.Lock()
	s.items[id] = session
	s.mu.Unlock()
	return session
}

func (s *SessionStore) Delete(id uint64) {
	s.mu.Lock()
	delete(s.items, id)
	s.mu.Unlock()
}
