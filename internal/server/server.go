// ==============================================================================
// MasterHttpRelayVPN
// Author: MasterkinG32
// Github: https://github.com/masterking32
// Year: 2026
// ==============================================================================
package server

import (
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"sync"
	"time"

	"masterhttprelayvpn/internal/config"
	"masterhttprelayvpn/internal/logger"
	"masterhttprelayvpn/internal/protocol"
)

type Server struct {
	cfg config.Config
	log *logger.Logger

	mu       sync.RWMutex
	sessions map[string]*ClientSession
}

type ClientSession struct {
	ClientSessionKey string
	CreatedAt        time.Time
	LastActivityAt   time.Time
	SOCKSConnections map[uint64]*SOCKSState
}

type SOCKSState struct {
	ID               uint64
	CreatedAt        time.Time
	LastActivityAt   time.Time
	Target           *protocol.Target
	ConnectSeen      bool
	ConnectAcked     bool
	CloseReadSeen    bool
	CloseWriteSeen   bool
	ResetSeen        bool
	ReceivedBytes    uint64
	LastSequenceSeen uint64
}

func New(cfg config.Config, lg *logger.Logger) *Server {
	return &Server{
		cfg:      cfg,
		log:      lg,
		sessions: make(map[string]*ClientSession),
	}
}

func (s *Server) Run(ctx context.Context) error {
	addr := fmt.Sprintf("%s:%d", s.cfg.ServerHost, s.cfg.ServerPort)

	mux := http.NewServeMux()
	mux.HandleFunc("/", s.handleRelay)
	mux.HandleFunc("/relay", s.handleRelay)

	httpServer := &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	go s.cleanupLoop(ctx)
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = httpServer.Shutdown(shutdownCtx)
	}()

	s.log.Infof("<green>server listening on <cyan>%s</cyan></green>", addr)
	if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		return err
	}
	return nil
}

func (s *Server) handleRelay(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	r.Body = http.MaxBytesReader(w, r.Body, int64(s.cfg.ReadBodyLimitBytes))
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "read body failed", http.StatusBadRequest)
		return
	}

	batch, err := protocol.DecryptBatch(body, s.cfg.AESEncryptionKey)
	if err != nil {
		s.log.Warnf("<yellow>decrypt batch failed: <cyan>%v</cyan></yellow>", err)
		http.Error(w, "invalid encrypted payload", http.StatusBadRequest)
		return
	}

	responseBatch, err := s.processBatch(batch)
	if err != nil {
		s.log.Warnf("<yellow>process batch=<cyan>%s</cyan> failed: <cyan>%v</cyan></yellow>", batch.BatchID, err)
		http.Error(w, "batch processing failed", http.StatusBadRequest)
		return
	}

	if len(responseBatch.Packets) == 0 {
		w.WriteHeader(http.StatusNoContent)
		return
	}

	encrypted, err := protocol.EncryptBatch(responseBatch, s.cfg.AESEncryptionKey)
	if err != nil {
		s.log.Errorf("<red>encrypt response batch failed: <cyan>%v</cyan></red>", err)
		http.Error(w, "response encryption failed", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("X-Relay-Version", fmt.Sprintf("%d", protocol.CurrentVersion))
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(encrypted)
}

func (s *Server) processBatch(batch protocol.Batch) (protocol.Batch, error) {
	session := s.getOrCreateSession(batch.ClientSessionKey)
	now := time.Now()

	s.mu.Lock()
	session.LastActivityAt = now

	responses := make([]protocol.Packet, 0, len(batch.Packets))
	for _, packet := range batch.Packets {
		response, err := s.processPacketLocked(session, packet, now)
		if err != nil {
			s.mu.Unlock()
			return protocol.Batch{}, err
		}
		if response != nil {
			responses = append(responses, *response)
		}
	}
	s.mu.Unlock()

	if len(responses) == 0 {
		return protocol.Batch{}, nil
	}
	return protocol.NewBatch(batch.ClientSessionKey, protocol.NewBatchID(), responses), nil
}

func (s *Server) processPacketLocked(session *ClientSession, packet protocol.Packet, now time.Time) (*protocol.Packet, error) {
	if packet.ClientSessionKey != session.ClientSessionKey {
		return nil, fmt.Errorf("packet client session key mismatch")
	}

	switch packet.Type {
	case protocol.PacketTypeSOCKSConnect:
		if packet.Target == nil {
			return nil, fmt.Errorf("socks_connect missing target")
		}

		socksState, exists := session.SOCKSConnections[packet.SOCKSID]
		if !exists {
			socksState = &SOCKSState{
				ID:               packet.SOCKSID,
				CreatedAt:        now,
				LastActivityAt:   now,
				Target:           packet.Target,
				ConnectSeen:      true,
				ConnectAcked:     true,
				LastSequenceSeen: packet.Sequence,
			}
			session.SOCKSConnections[packet.SOCKSID] = socksState
		} else {
			socksState.LastActivityAt = now
			socksState.Target = packet.Target
			socksState.ConnectSeen = true
			socksState.ConnectAcked = true
			if packet.Sequence > socksState.LastSequenceSeen {
				socksState.LastSequenceSeen = packet.Sequence
			}
		}

		response := protocol.NewPacket(session.ClientSessionKey, protocol.PacketTypeSOCKSConnectAck)
		response.SOCKSID = packet.SOCKSID
		response.Sequence = packet.Sequence
		return &response, nil

	case protocol.PacketTypeSOCKSData:
		socksState := s.getOrCreateSOCKSStateLocked(session, packet, now)
		socksState.LastActivityAt = now
		socksState.ReceivedBytes += uint64(len(packet.Payload))
		if packet.Sequence > socksState.LastSequenceSeen {
			socksState.LastSequenceSeen = packet.Sequence
		}

		response := protocol.NewPacket(session.ClientSessionKey, protocol.PacketTypeSOCKSDataAck)
		response.SOCKSID = packet.SOCKSID
		response.Sequence = packet.Sequence
		response.FragmentID = packet.FragmentID
		response.TotalFragments = packet.TotalFragments
		response.Final = packet.Final
		return &response, nil

	case protocol.PacketTypeSOCKSCloseRead:
		socksState := s.getOrCreateSOCKSStateLocked(session, packet, now)
		socksState.LastActivityAt = now
		socksState.CloseReadSeen = true
		if packet.Sequence > socksState.LastSequenceSeen {
			socksState.LastSequenceSeen = packet.Sequence
		}
		response := protocol.NewPacket(session.ClientSessionKey, protocol.PacketTypeSOCKSCloseRead)
		response.SOCKSID = packet.SOCKSID
		response.Sequence = packet.Sequence
		return &response, nil

	case protocol.PacketTypeSOCKSCloseWrite:
		socksState := s.getOrCreateSOCKSStateLocked(session, packet, now)
		socksState.LastActivityAt = now
		socksState.CloseWriteSeen = true
		if packet.Sequence > socksState.LastSequenceSeen {
			socksState.LastSequenceSeen = packet.Sequence
		}
		response := protocol.NewPacket(session.ClientSessionKey, protocol.PacketTypeSOCKSCloseWrite)
		response.SOCKSID = packet.SOCKSID
		response.Sequence = packet.Sequence
		return &response, nil

	case protocol.PacketTypeSOCKSRST:
		socksState := s.getOrCreateSOCKSStateLocked(session, packet, now)
		socksState.LastActivityAt = now
		socksState.ResetSeen = true
		if packet.Sequence > socksState.LastSequenceSeen {
			socksState.LastSequenceSeen = packet.Sequence
		}
		response := protocol.NewPacket(session.ClientSessionKey, protocol.PacketTypeSOCKSRST)
		response.SOCKSID = packet.SOCKSID
		response.Sequence = packet.Sequence
		delete(session.SOCKSConnections, packet.SOCKSID)
		return &response, nil

	case protocol.PacketTypePing:
		response := protocol.NewPacket(session.ClientSessionKey, protocol.PacketTypePong)
		response.Payload = append([]byte(nil), packet.Payload...)
		return &response, nil

	case protocol.PacketTypeSOCKSConnectAck,
		protocol.PacketTypeSOCKSConnectFail,
		protocol.PacketTypeSOCKSRuleSetDenied,
		protocol.PacketTypeSOCKSNetworkUnreachable,
		protocol.PacketTypeSOCKSHostUnreachable,
		protocol.PacketTypeSOCKSConnectionRefused,
		protocol.PacketTypeSOCKSTTLExpired,
		protocol.PacketTypeSOCKSCommandUnsupported,
		protocol.PacketTypeSOCKSAddressTypeUnsupported,
		protocol.PacketTypeSOCKSAuthFailed,
		protocol.PacketTypeSOCKSUpstreamUnavailable,
		protocol.PacketTypeSOCKSDataAck,
		protocol.PacketTypePong:
		return nil, nil

	default:
		return nil, fmt.Errorf("unsupported packet type: %s", packet.Type)
	}
}

func (s *Server) getOrCreateSession(clientSessionKey string) *ClientSession {
	s.mu.RLock()
	existing := s.sessions[clientSessionKey]
	s.mu.RUnlock()
	if existing != nil {
		return existing
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	existing = s.sessions[clientSessionKey]
	if existing != nil {
		return existing
	}

	now := time.Now()
	session := &ClientSession{
		ClientSessionKey: clientSessionKey,
		CreatedAt:        now,
		LastActivityAt:   now,
		SOCKSConnections: make(map[uint64]*SOCKSState),
	}
	s.sessions[clientSessionKey] = session
	s.log.Infof("<green>created client session <cyan>%s</cyan></green>", clientSessionKey)
	return session
}

func (s *Server) getOrCreateSOCKSStateLocked(session *ClientSession, packet protocol.Packet, now time.Time) *SOCKSState {
	socksState := session.SOCKSConnections[packet.SOCKSID]
	if socksState != nil {
		return socksState
	}

	socksState = &SOCKSState{
		ID:               packet.SOCKSID,
		CreatedAt:        now,
		LastActivityAt:   now,
		Target:           packet.Target,
		LastSequenceSeen: packet.Sequence,
	}
	session.SOCKSConnections[packet.SOCKSID] = socksState
	return socksState
}

func (s *Server) cleanupLoop(ctx context.Context) {
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			s.cleanupExpired()
		}
	}
}

func (s *Server) cleanupExpired() {
	sessionTTL := time.Duration(s.cfg.SessionIdleTimeoutMS) * time.Millisecond
	socksTTL := time.Duration(s.cfg.SOCKSIdleTimeoutMS) * time.Millisecond
	now := time.Now()

	s.mu.Lock()
	defer s.mu.Unlock()

	for clientSessionKey, session := range s.sessions {
		for socksID, socksState := range session.SOCKSConnections {
			if now.Sub(socksState.LastActivityAt) > socksTTL {
				delete(session.SOCKSConnections, socksID)
				s.log.Debugf("<yellow>expired socks state session=<cyan>%s</cyan> socks_id=<cyan>%d</cyan></yellow>", clientSessionKey, socksID)
			}
		}

		if len(session.SOCKSConnections) == 0 && now.Sub(session.LastActivityAt) > sessionTTL {
			delete(s.sessions, clientSessionKey)
			s.log.Infof("<yellow>expired client session <cyan>%s</cyan></yellow>", clientSessionKey)
		}
	}
}

func (s *Server) SessionSnapshot() (sessions int, socksConnections int) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	sessions = len(s.sessions)
	for _, session := range s.sessions {
		socksConnections += len(session.SOCKSConnections)
	}
	return sessions, socksConnections
}

func LocalListenAddress(host string, port int) string {
	return net.JoinHostPort(host, fmt.Sprintf("%d", port))
}
