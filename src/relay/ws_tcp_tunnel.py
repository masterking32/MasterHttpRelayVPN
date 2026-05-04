"""
WebSocket TCP tunnel client (RFC 6455, stdlib only).

Establishes a domain-fronted TLS connection to a WebSocket relay endpoint,
upgrades to WebSocket, then exposes an async send/recv interface for
bidirectional raw TCP proxying.

No external dependencies — uses only asyncio, ssl, hashlib, base64,
struct, os, urllib.parse from stdlib.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import socket
import ssl
import struct
from urllib.parse import urlparse

try:
    import certifi
except Exception:
    certifi = None

from core.constants import (
    WS_TCP_RELAY_CONNECT_TIMEOUT,
    WS_TCP_RELAY_PING_INTERVAL,
    WS_TCP_RELAY_READ_CHUNK,
)

log = logging.getLogger("WSTcpTunnel")

# RFC 6455 magic GUID used in Sec-WebSocket-Accept computation.
_WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# WebSocket opcodes we care about.
_OP_BINARY = 0x2
_OP_TEXT   = 0x1
_OP_CLOSE  = 0x8
_OP_PING   = 0x9
_OP_PONG   = 0xA


# ── RFC 6455 frame codec ─────────────────────────────────────────────────

def _encode_frame(opcode: int, payload: bytes, mask: bool = True) -> bytes:
    """Encode a single complete (FIN=1) WebSocket frame."""
    plen = len(payload)
    if plen <= 125:
        length_byte = plen
        ext = b""
    elif plen <= 65535:
        length_byte = 126
        ext = struct.pack("!H", plen)
    else:
        length_byte = 127
        ext = struct.pack("!Q", plen)

    fin_opcode = 0x80 | (opcode & 0x0F)  # FIN=1

    if mask:
        length_byte |= 0x80
        mask_key = os.urandom(4)
        # XOR in 4-byte strides for speed; handle remainder separately.
        n, r = divmod(plen, 4)
        chunks = bytearray(n * 4)
        mk = struct.unpack_from("!I", mask_key)[0]
        for i in range(n):
            word = struct.unpack_from("!I", payload, i * 4)[0]
            struct.pack_into("!I", chunks, i * 4, word ^ mk)
        tail = bytes(payload[n * 4 + j] ^ mask_key[j] for j in range(r))
        return bytes([fin_opcode, length_byte]) + ext + mask_key + bytes(chunks) + tail
    else:
        return bytes([fin_opcode, length_byte]) + ext + payload


async def _read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """Read exactly one WebSocket frame from `reader`.

    Returns (opcode, payload).  Raises asyncio.IncompleteReadError or
    ConnectionError on EOF / closed connection.
    """
    header = await reader.readexactly(2)
    opcode = header[0] & 0x0F
    masked = bool(header[1] & 0x80)
    plen   = header[1] & 0x7F

    if plen == 126:
        ext  = await reader.readexactly(2)
        plen = struct.unpack("!H", ext)[0]
    elif plen == 127:
        ext  = await reader.readexactly(8)
        plen = struct.unpack("!Q", ext)[0]

    if masked:
        mask_key = await reader.readexactly(4)
        raw      = await reader.readexactly(plen)
        payload  = bytes(b ^ mask_key[i % 4] for i, b in enumerate(raw))
    else:
        payload = await reader.readexactly(plen)

    return opcode, payload


# ── WSTcpTunnel ──────────────────────────────────────────────────────────

class WSTcpTunnel:
    """Async WebSocket tunnel to a remote TCP endpoint via a relay.

    The relay is expected to accept a WebSocket upgrade at:
        wss://<relay_host>/tcp?k=<auth_key>&host=<target>&port=<port>

    It then connects to <target>:<port> over TCP and forwards bytes
    bidirectionally through the WebSocket.

    Domain fronting: if `front_ip` and/or `front_domain` are set, the TCP
    connection goes to `front_ip` and the TLS SNI is set to `front_domain`,
    while the HTTP Host header still uses the relay's actual hostname so the
    CDN routes the request correctly.

    Usage::

        tunnel = WSTcpTunnel(
            ws_url="wss://my-relay.deno.dev/tcp",
            auth_key="secret",
            target_host="ssh.example.com",
            target_port=22,
        )
        await tunnel.connect()
        await tunnel.send(b"...")
        data = await tunnel.recv()   # b"" on close
        await tunnel.close()
    """

    def __init__(
        self,
        ws_url: str,
        auth_key: str,
        target_host: str,
        target_port: int,
        *,
        front_ip: str | None = None,
        front_domain: str | None = None,
        verify_ssl: bool = True,
        connect_timeout: float = WS_TCP_RELAY_CONNECT_TIMEOUT,
        ping_interval: float = WS_TCP_RELAY_PING_INTERVAL,
    ):
        self._ws_url       = ws_url
        self._auth_key     = auth_key
        self._target_host  = target_host
        self._target_port  = target_port
        self._front_ip     = front_ip
        self._front_domain = front_domain
        self._verify_ssl   = verify_ssl
        self._connect_timeout = connect_timeout
        self._ping_interval   = ping_interval

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._closed = False
        self._write_lock = asyncio.Lock()
        self._ping_task: asyncio.Task | None = None

    # ── Public API ───────────────────────────────────────────────────

    async def connect(self) -> None:
        """Establish the WebSocket connection.  Raises on failure."""
        parsed   = urlparse(self._ws_url)
        scheme   = parsed.scheme.lower()  # "wss" or "ws"
        url_host = parsed.hostname or ""
        url_port = parsed.port or (443 if scheme == "wss" else 80)
        path     = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        # Append relay params (auth + target) to the path.
        sep = "&" if "?" in path else "?"
        path = (
            f"{path}{sep}k={_pct_encode(self._auth_key)}"
            f"&host={_pct_encode(self._target_host)}"
            f"&port={self._target_port}"
        )

        # Connection target: front_ip overrides DNS if set.
        connect_addr = self._front_ip or url_host
        connect_port = url_port

        # TLS SNI: front_domain overrides if set (domain fronting).
        sni_host = self._front_domain or url_host

        use_tls = (scheme == "wss")
        ssl_ctx: ssl.SSLContext | None = None
        if use_tls:
            ssl_ctx = ssl.create_default_context()
            if certifi is not None:
                try:
                    ssl_ctx.load_verify_locations(cafile=certifi.where())
                except Exception:
                    pass
            if not self._verify_ssl:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode    = ssl.CERT_NONE

        # asyncio.open_connection handles DNS resolution automatically.
        # server_hostname controls TLS SNI independently of the connect address,
        # which is exactly what domain fronting needs (connect to front_ip but
        # present sni_host in the TLS handshake).
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(
                    connect_addr, connect_port,
                    ssl=ssl_ctx,
                    server_hostname=sni_host if use_tls else None,
                ),
                timeout=self._connect_timeout,
            )
        except Exception:
            raise

        # TCP_NODELAY after connection — important for SSH interactive latency.
        try:
            sock = self._writer.get_extra_info("socket")
            if sock:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass

        # HTTP/1.1 WebSocket upgrade handshake.
        ws_key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {url_host}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        self._writer.write(request.encode())
        await self._writer.drain()

        # Read and validate the 101 response.
        response_bytes = await asyncio.wait_for(
            self._reader.readuntil(b"\r\n\r\n"),
            timeout=self._connect_timeout,
        )
        response_text = response_bytes.decode("utf-8", errors="replace")
        if "101" not in response_text.split("\r\n", 1)[0]:
            raise ConnectionError(
                f"WebSocket upgrade failed: {response_text.split(chr(10), 1)[0].strip()}"
            )

        # Verify Sec-WebSocket-Accept.
        expected_accept = base64.b64encode(
            hashlib.sha1((ws_key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        if expected_accept.lower() not in response_text.lower():
            raise ConnectionError("Sec-WebSocket-Accept mismatch")

        log.debug("WS tunnel connected → %s:%d via %s",
                  self._target_host, self._target_port, self._ws_url)

        self._ping_task = asyncio.create_task(self._keepalive_loop())

    async def send(self, data: bytes) -> None:
        """Send raw bytes as a binary WebSocket frame."""
        if self._closed:
            raise ConnectionError("WSTcpTunnel is closed")
        frame = _encode_frame(_OP_BINARY, data, mask=True)
        async with self._write_lock:
            if self._closed:
                raise ConnectionError("WSTcpTunnel is closed")
            self._writer.write(frame)
            await self._writer.drain()

    async def recv(self) -> bytes:
        """Receive the next chunk of raw bytes from the relay.

        Returns b"" when the connection is closed (either side).
        """
        while True:
            try:
                opcode, payload = await _read_frame(self._reader)
            except (asyncio.IncompleteReadError, ConnectionError, OSError):
                self._closed = True
                return b""

            if opcode in (_OP_BINARY, _OP_TEXT):
                return payload

            if opcode == _OP_PING:
                # Respond to server ping.
                try:
                    async with self._write_lock:
                        self._writer.write(_encode_frame(_OP_PONG, payload, mask=True))
                        await self._writer.drain()
                except Exception:
                    pass
                continue

            if opcode == _OP_PONG:
                # Keepalive pong from server — just discard.
                continue

            if opcode == _OP_CLOSE:
                # Echo close frame and signal EOF.
                try:
                    async with self._write_lock:
                        self._writer.write(_encode_frame(_OP_CLOSE, b"", mask=True))
                        await self._writer.drain()
                except Exception:
                    pass
                self._closed = True
                return b""

            # Unknown opcode — treat as close.
            self._closed = True
            return b""

    async def close(self) -> None:
        """Send a WS close frame and close the underlying TCP connection."""
        if self._closed:
            return
        self._closed = True
        if self._ping_task is not None:
            self._ping_task.cancel()
            try:
                await self._ping_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            async with self._write_lock:
                self._writer.write(_encode_frame(_OP_CLOSE, b"", mask=True))
                await self._writer.drain()
        except Exception:
            pass
        try:
            self._writer.close()
        except Exception:
            pass

    # ── Internal ─────────────────────────────────────────────────────

    async def _keepalive_loop(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(self._ping_interval)
                if self._closed:
                    break
                try:
                    async with self._write_lock:
                        if not self._closed:
                            self._writer.write(
                                _encode_frame(_OP_PING, b"\x00" * 4, mask=True)
                            )
                            await self._writer.drain()
                except Exception:
                    break
        except asyncio.CancelledError:
            pass


def _pct_encode(value: str) -> str:
    """Percent-encode a string for use in a URL query parameter."""
    safe = (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz"
        "0123456789-._~"
    )
    return "".join(c if c in safe else f"%{ord(c):02X}" for c in value)
