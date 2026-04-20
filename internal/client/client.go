// ==============================================================================
// MasterHttpRelayVPN
// Author: MasterkinG32
// Github: https://github.com/masterking32
// Year: 2026
// ==============================================================================
package client

import (
	"context"
	"fmt"
	"net"
	"sync"
	"time"

	"masterhttprelayvpn/internal/config"
	"masterhttprelayvpn/internal/logger"
)

type Client struct {
	cfg      config.Config
	log      *logger.Logger
	sessions *SessionStore

	connMu sync.Mutex
	conns  map[net.Conn]struct{}
}

func New(cfg config.Config, lg *logger.Logger) *Client {
	return &Client{
		cfg:      cfg,
		log:      lg,
		sessions: NewSessionStore(),
		conns:    make(map[net.Conn]struct{}),
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

	go func() {
		<-ctx.Done()
		c.log.Infof("<yellow>shutdown requested, closing listener and active sessions</yellow>")
		_ = ln.Close()
		c.closeAllConns()
	}()

	var wg sync.WaitGroup
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
