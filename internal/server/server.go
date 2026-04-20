// ==============================================================================
// MasterHttpRelayVPN
// Author: MasterkinG32
// Github: https://github.com/masterking32
// Year: 2026
// ==============================================================================
package server

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"strings"
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
	DrainCursor      uint64
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
	OutboundSequence uint64
	UpstreamConn     net.Conn
	upstreamWriteMu  sync.Mutex
	upstreamCloseMu  sync.Mutex
	upstreamReadEOF  bool
	upstreamWriteEOF bool
	queueMu          sync.Mutex
	OutboundQueue    []protocol.Packet
	QueuedBytes      int
	MaxQueueBytes    int
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
	s.log.Debugf(
		"<gray>relay request method=<cyan>%s</cyan> remote=<cyan>%s</cyan> body_bytes=<cyan>%d</cyan></gray>",
		r.Method, r.RemoteAddr, len(body),
	)

	batch, err := protocol.DecryptBatch(body, s.cfg.AESEncryptionKey)
	if err != nil {
		s.log.Warnf("<yellow>decrypt batch failed: <cyan>%v</cyan></yellow>", err)
		http.Error(w, "invalid encrypted payload", http.StatusBadRequest)
		return
	}
	s.log.Debugf(
		"<gray>decrypted batch=<cyan>%s</cyan> client_session_key=<cyan>%s</cyan> packets=<cyan>%d</cyan></gray>",
		batch.BatchID, batch.ClientSessionKey, len(batch.Packets),
	)

	responseBatch, err := s.processBatch(batch)
	if err != nil {
		s.log.Warnf("<yellow>process batch=<cyan>%s</cyan> failed: <cyan>%v</cyan></yellow>", batch.BatchID, err)
		http.Error(w, "batch processing failed", http.StatusBadRequest)
		return
	}

	if len(responseBatch.Packets) == 0 {
		s.log.Debugf(
			"<gray>batch=<cyan>%s</cyan> produced no response packets client_session_key=<cyan>%s</cyan></gray>",
			batch.BatchID, batch.ClientSessionKey,
		)
		w.WriteHeader(http.StatusNoContent)
		return
	}

	encrypted, err := protocol.EncryptBatch(responseBatch, s.cfg.AESEncryptionKey)
	if err != nil {
		s.log.Errorf("<red>encrypt response batch failed: <cyan>%v</cyan></red>", err)
		http.Error(w, "response encryption failed", http.StatusInternalServerError)
		return
	}
	s.log.Debugf(
		"<gray>response batch=<cyan>%s</cyan> packets=<cyan>%d</cyan> encrypted_bytes=<cyan>%d</cyan> client_session_key=<cyan>%s</cyan></gray>",
		responseBatch.BatchID, len(responseBatch.Packets), len(encrypted), responseBatch.ClientSessionKey,
	)

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
		s.log.Debugf(
			"<gray>processing batch=<cyan>%s</cyan> client_session_key=<cyan>%s</cyan> packet=<cyan>%s</cyan> socks_id=<cyan>%d</cyan> seq=<cyan>%d</cyan> payload_bytes=<cyan>%d</cyan> final=<cyan>%t</cyan></gray>",
			batch.BatchID, batch.ClientSessionKey, packet.Type, packet.SOCKSID, packet.Sequence, len(packet.Payload), packet.Final,
		)
		response, err := s.processPacketLocked(session, packet, now)
		if err != nil {
			s.mu.Unlock()
			return protocol.Batch{}, err
		}
		if response != nil {
			s.log.Debugf(
				"<gray>generated direct response packet=<cyan>%s</cyan> socks_id=<cyan>%d</cyan> seq=<cyan>%d</cyan> payload_bytes=<cyan>%d</cyan></gray>",
				response.Type, response.SOCKSID, response.Sequence, len(response.Payload),
			)
			responses = append(responses, *response)
		}
	}
	for _, outbound := range s.drainSessionOutboundLocked(session) {
		s.log.Debugf(
			"<gray>drained queued response packet=<cyan>%s</cyan> socks_id=<cyan>%d</cyan> seq=<cyan>%d</cyan> payload_bytes=<cyan>%d</cyan></gray>",
			outbound.Type, outbound.SOCKSID, outbound.Sequence, len(outbound.Payload),
		)
		responses = append(responses, outbound)
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
				LastSequenceSeen: packet.Sequence,
				MaxQueueBytes:    s.cfg.MaxServerQueueBytes,
			}
			session.SOCKSConnections[packet.SOCKSID] = socksState
		} else {
			socksState.LastActivityAt = now
			socksState.Target = packet.Target
			socksState.ConnectSeen = true
			if packet.Sequence > socksState.LastSequenceSeen {
				socksState.LastSequenceSeen = packet.Sequence
			}
		}

		if socksState.UpstreamConn == nil {
			s.log.Debugf(
				"<gray>dial upstream socks_id=<cyan>%d</cyan> target=<cyan>%s</cyan> client_session_key=<cyan>%s</cyan></gray>",
				packet.SOCKSID, packet.Target.Address(), session.ClientSessionKey,
			)
			upstreamConn, err := net.DialTimeout("tcp", packet.Target.Address(), 10*time.Second)
			if err != nil {
				s.log.Warnf(
					"<yellow>upstream dial failed socks_id=<cyan>%d</cyan> target=<cyan>%s</cyan> client_session_key=<cyan>%s</cyan> error=<cyan>%v</cyan></yellow>",
					packet.SOCKSID, packet.Target.Address(), session.ClientSessionKey, err,
				)
				response := protocol.NewPacket(session.ClientSessionKey, protocol.PacketTypeSOCKSUpstreamUnavailable)
				response.SOCKSID = packet.SOCKSID
				response.Sequence = packet.Sequence
				response.Payload = []byte(err.Error())
				return &response, nil
			}
			socksState.UpstreamConn = upstreamConn
			socksState.ConnectAcked = true
			s.log.Infof(
				"<green>upstream connected socks_id=<cyan>%d</cyan> target=<cyan>%s</cyan> client_session_key=<cyan>%s</cyan></green>",
				packet.SOCKSID, packet.Target.Address(), session.ClientSessionKey,
			)
			go s.upstreamReadLoop(session.ClientSessionKey, socksState)
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
		s.log.Debugf(
			"<gray>write upstream socks_id=<cyan>%d</cyan> seq=<cyan>%d</cyan> payload_bytes=<cyan>%d</cyan> client_session_key=<cyan>%s</cyan></gray>",
			packet.SOCKSID, packet.Sequence, len(packet.Payload), session.ClientSessionKey,
		)
		if err := socksState.writeUpstream(packet.Payload); err != nil {
			s.log.Warnf(
				"<yellow>write upstream failed socks_id=<cyan>%d</cyan> seq=<cyan>%d</cyan> client_session_key=<cyan>%s</cyan> error=<cyan>%v</cyan></yellow>",
				packet.SOCKSID, packet.Sequence, session.ClientSessionKey, err,
			)
			response := protocol.NewPacket(session.ClientSessionKey, protocol.PacketTypeSOCKSRST)
			response.SOCKSID = packet.SOCKSID
			response.Sequence = packet.Sequence
			return &response, nil
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
		s.log.Debugf(
			"<gray>received close_read socks_id=<cyan>%d</cyan> seq=<cyan>%d</cyan> client_session_key=<cyan>%s</cyan></gray>",
			packet.SOCKSID, packet.Sequence, session.ClientSessionKey,
		)
		_ = socksState.closeUpstreamRead()
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
		s.log.Debugf(
			"<gray>received close_write socks_id=<cyan>%d</cyan> seq=<cyan>%d</cyan> client_session_key=<cyan>%s</cyan></gray>",
			packet.SOCKSID, packet.Sequence, session.ClientSessionKey,
		)
		_ = socksState.closeUpstreamWrite()
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
		s.log.Debugf(
			"<gray>received rst socks_id=<cyan>%d</cyan> seq=<cyan>%d</cyan> client_session_key=<cyan>%s</cyan></gray>",
			packet.SOCKSID, packet.Sequence, session.ClientSessionKey,
		)
		_ = socksState.closeUpstream()
		socksState.release()
		delete(session.SOCKSConnections, packet.SOCKSID)
		return &response, nil

	case protocol.PacketTypePing:
		s.log.Debugf(
			"<gray>received ping client_session_key=<cyan>%s</cyan> payload_bytes=<cyan>%d</cyan></gray>",
			session.ClientSessionKey, len(packet.Payload),
		)
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
		MaxQueueBytes:    s.cfg.MaxServerQueueBytes,
	}
	session.SOCKSConnections[packet.SOCKSID] = socksState
	s.log.Debugf(
		"<gray>created socks state client_session_key=<cyan>%s</cyan> socks_id=<cyan>%d</cyan> target=<cyan>%s</cyan></gray>",
		session.ClientSessionKey, packet.SOCKSID, targetAddressForLog(packet.Target),
	)
	return socksState
}

func (s *Server) drainSessionOutboundLocked(session *ClientSession) []protocol.Packet {
	packets := make([]protocol.Packet, 0)
	if len(session.SOCKSConnections) == 0 {
		return packets
	}

	states := make([]*SOCKSState, 0, len(session.SOCKSConnections))
	for _, socksState := range session.SOCKSConnections {
		states = append(states, socksState)
	}

	start := 0
	if len(states) > 1 {
		start = int(session.DrainCursor % uint64(len(states)))
		session.DrainCursor++
	}

	remainingPackets := s.cfg.MaxPacketsPerBatch
	remainingBytes := s.cfg.MaxBatchBytes
	for offset := 0; offset < len(states) && remainingPackets > 0 && remainingBytes > 0; offset++ {
		socksState := states[(start+offset)%len(states)]
		drained := socksState.drainOutbound(remainingPackets, remainingBytes)
		if len(drained) > 0 {
			s.log.Debugf(
				"<gray>drained outbound queue client_session_key=<cyan>%s</cyan> socks_id=<cyan>%d</cyan> packets=<cyan>%d</cyan></gray>",
				session.ClientSessionKey, socksState.ID, len(drained),
			)
			remainingPackets -= len(drained)
			for _, packet := range drained {
				remainingBytes -= len(packet.Payload)
			}
		}
		packets = append(packets, drained...)
	}
	return packets
}

func (s *Server) upstreamReadLoop(clientSessionKey string, socksState *SOCKSState) {
	if socksState.UpstreamConn == nil {
		return
	}

	buffer := make([]byte, s.cfg.MaxChunkSize)
	for {
		n, err := socksState.UpstreamConn.Read(buffer)
		if n > 0 {
			chunk := append([]byte(nil), buffer[:n]...)
			if !socksState.enqueueOutboundData(clientSessionKey, chunk, false) {
				s.log.Warnf(
					"<yellow>server outbound queue full socks_id=<cyan>%d</cyan> target=<cyan>%s</cyan> client_session_key=<cyan>%s</cyan></yellow>",
					socksState.ID, targetAddressForLog(socksState.Target), clientSessionKey,
				)
				socksState.forceResetPacket(clientSessionKey)
				_ = socksState.closeUpstream()
				return
			}
			socksState.LastActivityAt = time.Now()
			queueDepth, queueBytes := socksState.queueSnapshot()
			s.log.Debugf(
				"<gray>read upstream socks_id=<cyan>%d</cyan> target=<cyan>%s</cyan> bytes=<cyan>%d</cyan> queue_depth=<cyan>%d</cyan> queue_bytes=<cyan>%d</cyan> client_session_key=<cyan>%s</cyan></gray>",
				socksState.ID, targetAddressForLog(socksState.Target), n, queueDepth, queueBytes, clientSessionKey,
			)
		}

		if err != nil {
			if errors.Is(err, io.EOF) {
				s.log.Debugf(
					"<gray>upstream eof socks_id=<cyan>%d</cyan> target=<cyan>%s</cyan> client_session_key=<cyan>%s</cyan></gray>",
					socksState.ID, targetAddressForLog(socksState.Target), clientSessionKey,
				)
				_ = socksState.enqueueControlPacket(clientSessionKey, protocol.PacketTypeSOCKSCloseRead, true)
				_ = socksState.closeUpstreamRead()
				return
			}
			if isClosedConnError(err) {
				s.log.Debugf(
					"<gray>upstream closed locally socks_id=<cyan>%d</cyan> target=<cyan>%s</cyan> client_session_key=<cyan>%s</cyan></gray>",
					socksState.ID, targetAddressForLog(socksState.Target), clientSessionKey,
				)
				return
			}
			s.log.Warnf(
				"<yellow>upstream read failed socks_id=<cyan>%d</cyan> target=<cyan>%s</cyan> client_session_key=<cyan>%s</cyan> error=<cyan>%v</cyan></yellow>",
				socksState.ID, targetAddressForLog(socksState.Target), clientSessionKey, err,
			)
			socksState.forceResetPacket(clientSessionKey)
			_ = socksState.closeUpstream()
			return
		}
	}
}

func (s *SOCKSState) nextOutboundSequence() uint64 {
	s.OutboundSequence++
	return s.OutboundSequence
}

func (s *SOCKSState) enqueueOutboundData(clientSessionKey string, payload []byte, final bool) bool {
	packet := protocol.NewPacket(clientSessionKey, protocol.PacketTypeSOCKSData)
	packet.SOCKSID = s.ID
	packet.Sequence = s.nextOutboundSequence()
	packet.Final = final
	packet.Payload = payload
	return s.enqueuePacket(packet)
}

func (s *SOCKSState) enqueueControlPacket(clientSessionKey string, packetType protocol.PacketType, final bool) bool {
	packet := protocol.NewPacket(clientSessionKey, packetType)
	packet.SOCKSID = s.ID
	packet.Sequence = s.nextOutboundSequence()
	packet.Final = final
	return s.enqueuePacket(packet)
}

func (s *SOCKSState) enqueuePacket(packet protocol.Packet) bool {
	s.queueMu.Lock()
	defer s.queueMu.Unlock()
	packetBytes := len(packet.Payload)
	if s.MaxQueueBytes > 0 && s.QueuedBytes+packetBytes > s.MaxQueueBytes {
		return false
	}
	s.OutboundQueue = append(s.OutboundQueue, packet)
	s.QueuedBytes += packetBytes
	return true
}

func (s *SOCKSState) forceResetPacket(clientSessionKey string) {
	packet := protocol.NewPacket(clientSessionKey, protocol.PacketTypeSOCKSRST)
	packet.SOCKSID = s.ID
	packet.Sequence = s.nextOutboundSequence()
	packet.Final = true

	s.queueMu.Lock()
	defer s.queueMu.Unlock()
	s.OutboundQueue = []protocol.Packet{packet}
	s.QueuedBytes = 0
}

func (s *SOCKSState) release() {
	s.queueMu.Lock()
	for i := range s.OutboundQueue {
		s.OutboundQueue[i] = protocol.Packet{}
	}
	s.OutboundQueue = nil
	s.QueuedBytes = 0
	s.queueMu.Unlock()
	s.Target = nil
}

func (s *SOCKSState) queueSnapshot() (items int, bytes int) {
	s.queueMu.Lock()
	defer s.queueMu.Unlock()
	return len(s.OutboundQueue), s.QueuedBytes
}

func (s *SOCKSState) drainOutbound(maxPackets int, maxBytes int) []protocol.Packet {
	s.queueMu.Lock()
	defer s.queueMu.Unlock()

	if len(s.OutboundQueue) == 0 {
		return nil
	}

	selected := make([]protocol.Packet, 0, maxPackets)
	totalBytes := 0
	count := 0
	remaining := s.OutboundQueue[:0]

	for _, packet := range s.OutboundQueue {
		packetBytes := len(packet.Payload)
		if count < maxPackets && (count == 0 || totalBytes+packetBytes <= maxBytes) {
			selected = append(selected, packet)
			totalBytes += packetBytes
			count++
			s.QueuedBytes -= packetBytes
			continue
		}
		remaining = append(remaining, packet)
	}

	s.OutboundQueue = remaining
	if s.QueuedBytes < 0 {
		s.QueuedBytes = 0
	}
	return selected
}

func (s *SOCKSState) writeUpstream(payload []byte) error {
	if len(payload) == 0 {
		return nil
	}
	s.upstreamWriteMu.Lock()
	defer s.upstreamWriteMu.Unlock()
	if s.UpstreamConn == nil {
		return fmt.Errorf("upstream connection is not established")
	}
	_, err := s.UpstreamConn.Write(payload)
	return err
}

func (s *SOCKSState) closeUpstream() error {
	s.upstreamCloseMu.Lock()
	defer s.upstreamCloseMu.Unlock()
	if s.UpstreamConn == nil {
		return nil
	}
	err := s.UpstreamConn.Close()
	s.UpstreamConn = nil
	s.upstreamReadEOF = true
	s.upstreamWriteEOF = true
	return err
}

func (s *SOCKSState) closeUpstreamRead() error {
	s.upstreamCloseMu.Lock()
	defer s.upstreamCloseMu.Unlock()
	if s.upstreamReadEOF {
		return nil
	}
	s.upstreamReadEOF = true
	if tcpConn, ok := s.UpstreamConn.(*net.TCPConn); ok {
		return tcpConn.CloseRead()
	}
	return nil
}

func (s *SOCKSState) closeUpstreamWrite() error {
	s.upstreamCloseMu.Lock()
	defer s.upstreamCloseMu.Unlock()
	if s.upstreamWriteEOF {
		return nil
	}
	s.upstreamWriteEOF = true
	if tcpConn, ok := s.UpstreamConn.(*net.TCPConn); ok {
		return tcpConn.CloseWrite()
	}
	if s.UpstreamConn == nil {
		return nil
	}
	err := s.UpstreamConn.Close()
	s.UpstreamConn = nil
	s.upstreamReadEOF = true
	return err
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
				targetAddress := targetAddressForLog(socksState.Target)
				_ = socksState.closeUpstream()
				socksState.release()
				delete(session.SOCKSConnections, socksID)
				s.log.Debugf("<yellow>expired socks state session=<cyan>%s</cyan> socks_id=<cyan>%d</cyan> target=<cyan>%s</cyan></yellow>", clientSessionKey, socksID, targetAddress)
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

func targetAddressForLog(target *protocol.Target) string {
	if target == nil {
		return ""
	}
	return target.Address()
}

func isClosedConnError(err error) bool {
	if err == nil {
		return false
	}
	return strings.Contains(err.Error(), "use of closed network connection")
}
