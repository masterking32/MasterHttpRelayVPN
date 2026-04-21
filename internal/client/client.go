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
	headerBuilder    *relayHeaderBuilder

	connMu sync.Mutex
	conns  map[net.Conn]struct{}
	workCh chan struct{}

	lastMeaningfulActivityUnixMS atomic.Int64
	lastPingMeaningfulSnapshotMS atomic.Int64
	nextPingDueUnixMS            atomic.Int64
	activeBatches                atomic.Int64
	pingInFlight                 atomic.Int64
	idlePongStreak               atomic.Int64
	batchCursor                  atomic.Uint64
}

func New(cfg config.Config, lg *logger.Logger) *Client {
	clientSessionKey := generateClientSessionKey()

	return &Client{
		cfg:              cfg,
		log:              lg,
		clientSessionKey: clientSessionKey,
		socksConnections: NewSOCKSConnectionStore(),
		chunkPolicy:      newChunkPolicy(cfg),
		headerBuilder:    newRelayHeaderBuilder(cfg, lg),
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

func (c *Client) noteMeaningfulActivity(now time.Time) {
	c.lastMeaningfulActivityUnixMS.Store(now.UnixMilli())
	c.idlePongStreak.Store(0)
	c.nextPingDueUnixMS.Store(now.Add(time.Duration(c.cfg.IdlePollIntervalMS) * time.Millisecond).UnixMilli())
}

func (c *Client) tryBeginPing(now time.Time) bool {
	if !c.pingInFlight.CompareAndSwap(0, 1) {
		return false
	}

	c.lastPingMeaningfulSnapshotMS.Store(c.lastMeaningfulActivityUnixMS.Load())
	return true
}

func (c *Client) completePingWithPong() {
	c.pingInFlight.Store(0)
	now := time.Now()

	if c.lastMeaningfulActivityUnixMS.Load() == c.lastPingMeaningfulSnapshotMS.Load() {
		lastMeaningfulAt := time.UnixMilli(c.lastMeaningfulActivityUnixMS.Load())
		idleFor := now.Sub(lastMeaningfulAt)
		warmThreshold := time.Duration(c.cfg.PingWarmThresholdMS) * time.Millisecond
		if idleFor < warmThreshold {
			c.idlePongStreak.Store(0)
			c.nextPingDueUnixMS.Store(now.Add(time.Duration(c.cfg.IdlePollIntervalMS) * time.Millisecond).UnixMilli())
			return
		}

		streak := c.idlePongStreak.Add(1)
		c.nextPingDueUnixMS.Store(now.Add(c.idleIntervalForStreak(streak)).UnixMilli())
		return
	}

	c.idlePongStreak.Store(0)
	c.nextPingDueUnixMS.Store(now.Add(time.Duration(c.cfg.IdlePollIntervalMS) * time.Millisecond).UnixMilli())
}

func (c *Client) failPing() {
	c.pingInFlight.Store(0)
	c.nextPingDueUnixMS.Store(time.Now().Add(time.Duration(c.cfg.IdlePollIntervalMS) * time.Millisecond).UnixMilli())
}

func (c *Client) idleIntervalForStreak(streak int64) time.Duration {
	interval := c.cfg.PingBackoffBaseMS + int(streak)*c.cfg.PingBackoffStepMS
	if interval > c.cfg.PingMaxIntervalMS {
		interval = c.cfg.PingMaxIntervalMS
	}
	return time.Duration(interval) * time.Millisecond
}

func generateClientSessionKey() string {
	now := time.Now().UTC().Format("20060102T150405.000000000Z")
	random := make([]byte, 16)
	if _, err := rand.Read(random); err != nil {
		return fmt.Sprintf("%s_fallback", now)
	}

	return fmt.Sprintf("%s_%s", now, hex.EncodeToString(random))
}
