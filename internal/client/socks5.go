// ==============================================================================
// MasterHttpRelayVPN
// Author: MasterkinG32
// Github: https://github.com/masterking32
// Year: 2026
// ==============================================================================
package client

import (
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"slices"
	"strconv"
	"time"
)

const (
	socksVersion5 = 0x05

	socksMethodNoAuth       = 0x00
	socksMethodUserPass     = 0x02
	socksMethodNoAcceptable = 0xFF

	socksCmdConnect = 0x01

	socksAtypIPv4   = 0x01
	socksAtypDomain = 0x03
	socksAtypIPv6   = 0x04

	socksReplySuccess            = 0x00
	socksReplyGeneralFailure     = 0x01
	socksReplyCommandUnsupported = 0x07
	socksReplyAddressUnsupported = 0x08

	socksUserPassVersion = 0x01
	socksAuthSuccess     = 0x00
	socksAuthFailure     = 0x01
)

func (c *Client) handleConn(ctx context.Context, conn net.Conn) {
	c.registerConn(conn)
	defer c.unregisterConn(conn)
	defer conn.Close()

	socksConn := c.socksConnections.New(c.clientSessionKey, c.chunkPolicy)
	socksConn.LocalConn = conn
	defer c.socksConnections.Delete(socksConn.ID)

	c.log.Infof(
		"<green>accepted client <cyan>%s</cyan> socks_id=<cyan>%d</cyan> client_session_key=<cyan>%s</cyan></green>",
		conn.RemoteAddr(), socksConn.ID, socksConn.ClientSessionKey,
	)

	if err := c.handleSOCKS5(ctx, conn, socksConn); err != nil {
		c.log.Errorf("<red>socks_id=<cyan>%d</cyan> closed: <cyan>%v</cyan></red>", socksConn.ID, err)
		return
	}
}

func (c *Client) handleSOCKS5(ctx context.Context, conn net.Conn, socksConn *SOCKSConnection) error {
	version := make([]byte, 1)
	if _, err := io.ReadFull(conn, version); err != nil {
		return err
	}
	if version[0] != socksVersion5 {
		return fmt.Errorf("<red>unsupported SOCKS version: <cyan>%d</cyan></red>", version[0])
	}

	method, err := c.negotiateAuth(conn, socksConn)
	if err != nil {
		return err
	}

	if method == socksMethodUserPass {
		if err := c.handleUserPassAuth(conn, socksConn); err != nil {
			return err
		}
	}

	targetHost, targetPort, atyp, err := readConnectRequest(conn)
	if err != nil {
		return err
	}

	socksConn.TargetHost = targetHost
	socksConn.TargetPort = targetPort
	socksConn.TargetAddressType = atyp
	socksConn.ConnectAccepted = true
	socksConn.LastActivityAt = time.Now()

	c.log.Infof(
		"<green>socks_id=<cyan>%d</cyan> CONNECT target=<cyan>%s:%d</cyan> auth_method=<cyan>%d</cyan> client_session_key=<cyan>%s</cyan></green>",
		socksConn.ID, socksConn.TargetHost, socksConn.TargetPort, socksConn.SOCKSAuthMethod, socksConn.ClientSessionKey,
	)

	if err := socksConn.EnqueuePacket(socksConn.BuildSOCKSConnectPacket()); err != nil {
		return err
	}
	c.signalSendWork()

	if err := socksConn.WaitForConnect(ctx, 30*time.Second); err != nil {
		_ = writeSocksReply(conn, socksReplyGeneralFailure)
		return err
	}

	if err := writeSocksReply(conn, socksReplySuccess); err != nil {
		return err
	}

	return c.relayLocalPayload(ctx, conn, socksConn)
}

func (c *Client) negotiateAuth(conn net.Conn, socksConn *SOCKSConnection) (byte, error) {
	countBuf := make([]byte, 1)
	if _, err := io.ReadFull(conn, countBuf); err != nil {
		return 0, err
	}

	methodCount := int(countBuf[0])
	methods := make([]byte, methodCount)
	if _, err := io.ReadFull(conn, methods); err != nil {
		return 0, err
	}

	selected := byte(socksMethodNoAcceptable)
	if c.cfg.SOCKSAuth {
		if slices.Contains(methods, socksMethodUserPass) {
			selected = socksMethodUserPass
		}
	} else {
		if slices.Contains(methods, socksMethodNoAuth) {
			selected = socksMethodNoAuth
		}
	}

	if _, err := conn.Write([]byte{socksVersion5, selected}); err != nil {
		return 0, err
	}
	if selected == socksMethodNoAcceptable {
		return 0, errors.New("no acceptable auth method")
	}

	socksConn.SOCKSAuthMethod = selected
	return selected, nil
}

func (c *Client) handleUserPassAuth(conn net.Conn, socksConn *SOCKSConnection) error {
	header := make([]byte, 2)
	if _, err := io.ReadFull(conn, header); err != nil {
		return err
	}
	if header[0] != socksUserPassVersion {
		return fmt.Errorf("<red>invalid username/password auth version: <cyan>%d</cyan></red>", header[0])
	}

	username := make([]byte, int(header[1]))
	if _, err := io.ReadFull(conn, username); err != nil {
		return err
	}

	passLen := make([]byte, 1)
	if _, err := io.ReadFull(conn, passLen); err != nil {
		return err
	}

	password := make([]byte, int(passLen[0]))
	if _, err := io.ReadFull(conn, password); err != nil {
		return err
	}

	ok := string(username) == c.cfg.SOCKSUsername && string(password) == c.cfg.SOCKSPassword
	if ok {
		_, err := conn.Write([]byte{socksUserPassVersion, socksAuthSuccess})
		return err
	}

	_, _ = conn.Write([]byte{socksUserPassVersion, socksAuthFailure})
	return errors.New("invalid SOCKS username/password")
}

func readConnectRequest(conn net.Conn) (string, uint16, byte, error) {
	header := make([]byte, 4)
	if _, err := io.ReadFull(conn, header); err != nil {
		return "", 0, 0, err
	}

	if header[0] != socksVersion5 {
		return "", 0, 0, fmt.Errorf("<red>invalid request version: <cyan>%d</cyan></red>", header[0])
	}
	if header[1] != socksCmdConnect {
		_ = writeSocksReply(conn, socksReplyCommandUnsupported)
		return "", 0, 0, fmt.Errorf("<red>unsupported SOCKS command: <cyan>%d</cyan></red>", header[1])
	}
	if header[2] != 0x00 {
		return "", 0, 0, errors.New("non-zero reserved byte in SOCKS request")
	}

	atyp := header[3]
	host, err := readTargetHost(conn, atyp)
	if err != nil {
		_ = writeSocksReply(conn, socksReplyAddressUnsupported)
		return "", 0, 0, err
	}

	portBytes := make([]byte, 2)
	if _, err := io.ReadFull(conn, portBytes); err != nil {
		return "", 0, 0, err
	}

	return host, binary.BigEndian.Uint16(portBytes), atyp, nil
}

func readTargetHost(conn net.Conn, atyp byte) (string, error) {
	switch atyp {
	case socksAtypIPv4:
		ip := make([]byte, 4)
		if _, err := io.ReadFull(conn, ip); err != nil {
			return "", err
		}
		return net.IP(ip).String(), nil
	case socksAtypIPv6:
		ip := make([]byte, 16)
		if _, err := io.ReadFull(conn, ip); err != nil {
			return "", err
		}
		return net.IP(ip).String(), nil
	case socksAtypDomain:
		size := make([]byte, 1)
		if _, err := io.ReadFull(conn, size); err != nil {
			return "", err
		}
		domain := make([]byte, int(size[0]))
		if _, err := io.ReadFull(conn, domain); err != nil {
			return "", err
		}
		return string(domain), nil
	default:
		return "", fmt.Errorf("<red>unsupported address type: <cyan>%d</cyan></red>", atyp)
	}
}

func writeSocksReply(conn net.Conn, reply byte) error {
	resp := []byte{
		socksVersion5,
		reply,
		0x00,
		socksAtypIPv4,
		0x00, 0x00, 0x00, 0x00,
		0x00, 0x00,
	}
	_, err := conn.Write(resp)
	return err
}

func (c *Client) relayLocalPayload(ctx context.Context, conn net.Conn, socksConn *SOCKSConnection) error {
	peekTimeout := 2 * time.Second
	idleTimeout := 2 * time.Second
	buf := make([]byte, 32*1024)

	if err := conn.SetReadDeadline(time.Now().Add(peekTimeout)); err != nil {
		return err
	}

	n, err := conn.Read(buf)
	if err == nil && n > 0 {
		socksConn.BufferedBytes += n
		socksConn.LastActivityAt = time.Now()
		enqueued, enqueueErr := socksConn.EnqueuePayloadChunks(buf[:n], false)
		c.log.Debugf(
			"<green>socks_id=<cyan>%d</cyan> captured initial payload bytes=<cyan>%d</cyan> target=<cyan>%s</cyan> queued_packets=<cyan>%d</cyan> client_session_key=<cyan>%s</cyan></green>",
			socksConn.ID, n, net.JoinHostPort(socksConn.TargetHost, strconv.Itoa(int(socksConn.TargetPort))), enqueued, socksConn.ClientSessionKey,
		)
		if enqueueErr != nil {
			return enqueueErr
		}
		if enqueued > 0 {
			c.signalSendWork()
		}
	} else if ne, ok := err.(net.Error); !ok || !ne.Timeout() {
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return err
		}
	}

	for {
		select {
		case <-ctx.Done():
			return nil
		default:
		}

		if err := conn.SetReadDeadline(time.Now().Add(idleTimeout)); err != nil {
			return err
		}

		n, err := conn.Read(buf)
		if n > 0 {
			socksConn.BufferedBytes += n
			socksConn.LastActivityAt = time.Now()
			enqueued, enqueueErr := socksConn.EnqueuePayloadChunks(buf[:n], false)
			queueItems, queueBytes := socksConn.QueueSnapshot()
			c.log.Debugf(
				"<green>socks_id=<cyan>%d</cyan> buffered payload chunk=<cyan>%d</cyan> total=<cyan>%d</cyan> queued_packets=<cyan>%d</cyan> queue_depth=<cyan>%d</cyan> queue_bytes=<cyan>%d</cyan> client_session_key=<cyan>%s</cyan></green>",
				socksConn.ID, n, socksConn.BufferedBytes, enqueued, queueItems, queueBytes, socksConn.ClientSessionKey,
			)
			if enqueueErr != nil {
				return enqueueErr
			}
			if enqueued > 0 {
				c.signalSendWork()
			}
		}

		if err != nil {
			if errors.Is(err, io.EOF) {
				socksConn.MarkLocalReadEOF()
				if enqueueErr := socksConn.EnqueuePacket(socksConn.BuildSOCKSCloseWritePacket()); enqueueErr == nil {
					c.signalSendWork()
				}
				socksConn.WaitUntilClosed(ctx)
				return nil
			}
			if ne, ok := err.(net.Error); ok && ne.Timeout() {
				continue
			}
			if enqueueErr := socksConn.EnqueuePacket(socksConn.BuildSOCKSRSTPacket()); enqueueErr == nil {
				c.signalSendWork()
			}
			return err
		}
	}
}
