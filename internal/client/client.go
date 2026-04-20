// ==============================================================================
// MasterHttpRelayVPN
// Author: MasterkinG32
// Github: https://github.com/masterking32
// Year: 2026
// ==============================================================================
package client

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"net"
	"sync"
	"sync/atomic"
	"time"

	"masterhttprelayvpn/internal/config"
	"masterhttprelayvpn/internal/logger"
)

type Client struct {
	cfg              config.Config
	log              *logger.Logger
	clientSessionKey string
	socksConnections *SOCKSConnectionStore
	chunkPolicy      ChunkPolicy

	connMu sync.Mutex
	conns  map[net.Conn]struct{}
	workCh chan struct{}

	lastPollUnixMS atomic.Int64
	batchCursor    atomic.Uint64
}

func New(cfg config.Config, lg *logger.Logger) *Client {
	clientSessionKey := generateClientSessionKey()

	return &Client{
		cfg:              cfg,
		log:              lg,
		clientSessionKey: clientSessionKey,
		socksConnections: NewSOCKSConnectionStore(),
		chunkPolicy:      newChunkPolicy(cfg),
		conns:            make(map[net.Conn]struct{}),
		workCh:           make(chan struct{}, 1),
	}
}

func (c *Client) Run(ctx context.Context) error {
	addr := fmt.Sprintf("%s:%d", c.cfg.SOCKSHost, c.cfg.SOCKSPort)
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return err
	}
	defer ln.Close()

	c.log.Infof("<green>SOCKS5 listener started on <cyan>%s</cyan></green>", addr)
	c.log.Infof("<green>client session key: <cyan>%s</cyan></green>", c.clientSessionKey)

	go func() {
		<-ctx.Done()
		c.log.Infof("<yellow>shutdown requested, closing listener and active sessions</yellow>")
		_ = ln.Close()
		c.closeAllConns()
		c.socksConnections.CloseAll()
	}()

	var wg sync.WaitGroup
	c.startSendWorkers(ctx, &wg)
	defer wg.Wait()

	for {
		conn, err := ln.Accept()
		if err != nil {
			select {
			case <-ctx.Done():
				return nil
			default:
			}

			if ne, ok := err.(net.Error); ok && ne.Temporary() {
				time.Sleep(100 * time.Millisecond)
				continue
			}
			return err
		}

		wg.Add(1)
		go func() {
			defer wg.Done()
			c.handleConn(ctx, conn)
		}()
	}
}

func (c *Client) registerConn(conn net.Conn) {
	c.connMu.Lock()
	c.conns[conn] = struct{}{}
	c.connMu.Unlock()
}

func (c *Client) unregisterConn(conn net.Conn) {
	c.connMu.Lock()
	delete(c.conns, conn)
	c.connMu.Unlock()
}

func (c *Client) closeAllConns() {
	c.connMu.Lock()
	conns := make([]net.Conn, 0, len(c.conns))
	for conn := range c.conns {
		conns = append(conns, conn)
	}
	c.connMu.Unlock()

	for _, conn := range conns {
		_ = conn.Close()
	}
}

func (c *Client) signalSendWork() {
	select {
	case c.workCh <- struct{}{}:
	default:
	}
}

func generateClientSessionKey() string {
	now := time.Now().UTC().Format("20060102T150405.000000000Z")
	random := make([]byte, 16)
	if _, err := rand.Read(random); err != nil {
		return fmt.Sprintf("%s_fallback", now)
	}

	return fmt.Sprintf("%s_%s", now, hex.EncodeToString(random))
}
