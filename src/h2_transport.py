"""
HTTP/2 multiplexed transport for domain-fronted connections.

One TLS connection → many concurrent HTTP/2 streams → massive throughput.
Eliminates per-request TLS handshake overhead entirely.

Instead of a pool of 30 HTTP/1.1 connections (each handling 1 request),
this uses a SINGLE HTTP/2 connection handling 100+ concurrent requests.

Performance comparison:
  HTTP/1.1 pool: 30 connections × 1 request = 30 concurrent requests max
  HTTP/2 mux:    1 connection  × 100 streams = 100 concurrent requests

Requires: pip install h2
"""

import asyncio
import logging
import socket
import ssl
from urllib.parse import urlparse

try:
    import certifi
except Exception:  # optional dependency fallback
    certifi = None

import codec

log = logging.getLogger("H2")

try:
    import h2.connection
    import h2.config
    import h2.events
    import h2.settings
    H2_AVAILABLE = True
except ImportError:
    H2_AVAILABLE = False


class _StreamState:
    """State for a single in-flight HTTP/2 stream."""
    __slots__ = ("status", "headers", "data", "done", "error")

    def __init__(self):
        self.status = 0
        self.headers: dict[str, str] = {}
        self.data = bytearray()
        self.done = asyncio.Event()
        self.error: str | None = None


class H2Transport:
    """
    Persistent HTTP/2 connection with automatic stream multiplexing.

    All relay requests share ONE TLS connection. Each request becomes
    an independent HTTP/2 stream, running fully concurrently.

    Features:
      - Auto-connect on first use
      - Auto-reconnect on connection loss
      - Redirect following (as new streams, same connection)
      - Gzip decompression
      - Configurable max concurrency
    """

    def __init__(self, connect_host: str, sni_host: str,
                 verify_ssl: bool = True,
                 sni_hosts: list[str] | None = None):
        self.connect_host = connect_host
        self.sni_host = sni_host
        self.verify_ssl = verify_ssl
        # Optional SNI rotation pool — picked round-robin on each new connect.
        # Falls back to the single sni_host if no pool is given.
        self._sni_hosts: list[str] = [h for h in (sni_hosts or []) if h] or [sni_host]
        self._sni_idx: int = 0

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._h2: "h2.connection.H2Connection | None" = None
        self._connected = False

        self._write_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._read_task: asyncio.Task | None = None
        self._conn_generation = 0
        self._last_reconnect_at: float = 0.0

        # Per-stream tracking
        self._streams: dict[int, _StreamState] = {}

        # Stats
        self.total_requests = 0
        self.total_streams = 0

    # ── Connection lifecycle ──────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def ensure_connected(self):
        """Connect if not already connected."""
        if self._connected:
            return
        async with self._connect_lock:
            if self._connected:
                return
            await self._do_connect()

    async def _do_connect(self):
        """Establish the HTTP/2 connection with optimized socket settings."""
        ctx = ssl.create_default_context()
        # Some Python builds don't expose a usable default CA store.
        # Load certifi bundle when present to keep TLS verification stable.
        if certifi is not None:
            try:
                ctx.load_verify_locations(cafile=certifi.where())
            except Exception:
                pass
        # Advertise both h2 and http/1.1 — some DPI blocks h2-only ALPN
        ctx.set_alpn_protocols(["h2", "http/1.1"])
        if not self.verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        # Pick next SNI from the rotation pool so repeated reconnects
        # don't fingerprint as "always www.google.com".
        sni = self._sni_hosts[self._sni_idx % len(self._sni_hosts)]
        self._sni_idx += 1
        self.sni_host = sni  # kept for backward-compat logging

        # Create raw TCP socket with TCP_NODELAY BEFORE TLS handshake.
        # Nagle's algorithm can delay small writes (H2 frames) by up to 200ms
        # waiting to coalesce — TCP_NODELAY forces immediate send.
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        raw.setblocking(False)

        try:
            await asyncio.wait_for(
                asyncio.get_running_loop().sock_connect(
                    raw, (self.connect_host, 443)
                ),
                timeout=15,
            )
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(
                    ssl=ctx,
                    server_hostname=sni,
                    sock=raw,
                ),
                timeout=15,
            )
        except Exception:
            raw.close()
            raise

        # Verify we actually got HTTP/2
        ssl_obj = self._writer.get_extra_info("ssl_object")
        negotiated = ssl_obj.selected_alpn_protocol() if ssl_obj else None
        if negotiated != "h2":
            self._writer.close()
            raise RuntimeError(
                f"H2 ALPN negotiation failed (got {negotiated!r})"
            )

        config = h2.config.H2Configuration(
            client_side=True,
            header_encoding="utf-8",
        )
        self._h2 = h2.connection.H2Connection(config=config)
        self._h2.initiate_connection()

        # Connection-level flow control: ~16MB window
        self._h2.increment_flow_control_window(2 ** 24 - 65535)

        # Per-stream settings: 8MB initial window (covers all typical relay
        # request bodies in one shot so we never have to stall for a
        # WINDOW_UPDATE mid-send). Disable server push.
        self._h2.update_settings({
            h2.settings.SettingCodes.INITIAL_WINDOW_SIZE: 8 * 1024 * 1024,
            h2.settings.SettingCodes.ENABLE_PUSH: 0,
        })

        await self._flush()

        self._connected = True
        self._conn_generation += 1
        generation = self._conn_generation
        self._read_task = asyncio.create_task(self._reader_loop(generation))
        log.info("H2 connected → %s (SNI=%s, TCP_NODELAY=on)",
                 self.connect_host, sni)

    # Minimum seconds between successive reconnect() calls.  Without this,
    # concurrent relay failures trigger a rapid reconnect storm that causes
    # repeated "H2 connected → H2 reader loop ended" within milliseconds.
    _RECONNECT_MIN_INTERVAL = 1.0

    async def reconnect(self):
        """Close current connection and re-establish, with backoff."""
        async with self._connect_lock:
            loop = asyncio.get_running_loop()
            elapsed = loop.time() - self._last_reconnect_at
            if elapsed < self._RECONNECT_MIN_INTERVAL:
                await asyncio.sleep(self._RECONNECT_MIN_INTERVAL - elapsed)
            self._last_reconnect_at = loop.time()
            await self._close_internal()
            await self._do_connect()

    async def _close_internal(self):
        self._connected = False
        read_task = self._read_task
        self._read_task = None
        if read_task:
            read_task.cancel()
            await asyncio.gather(read_task, return_exceptions=True)
        if self._writer:
            try:
                writer = self._writer
                self._writer = None
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        # Wake all pending streams so they can raise
        for state in self._streams.values():
            state.error = "Connection closed"
            state.done.set()
        self._streams.clear()

    # ── Public API ────────────────────────────────────────────────

    async def request(self, method: str, path: str, host: str,
                      headers: dict | None = None,
                      body: bytes | None = None,
                      timeout: float = 25,
                      follow_redirects: int = 5) -> tuple[int, dict, bytes]:
        """
        Send an HTTP/2 request and return (status, headers, body).

        Thread-safe: many concurrent calls each get their own stream.
        Redirects are followed as new streams on the same connection.
        """
        await self.ensure_connected()
        self.total_requests += 1

        for _ in range(follow_redirects + 1):
            status, resp_headers, resp_body = await self._single_request(
                method, path, host, headers, body, timeout,
            )

            if status not in (301, 302, 303, 307, 308):
                return status, resp_headers, resp_body

            location = resp_headers.get("location", "")
            if not location:
                return status, resp_headers, resp_body

            parsed = urlparse(location)
            path = parsed.path + ("?" + parsed.query if parsed.query else "")
            host = parsed.netloc or host
            method = "GET"
            body = None
            headers = None  # Drop request headers on redirect

        return status, resp_headers, resp_body

    # ── Stream handling ───────────────────────────────────────────

    async def _single_request(self, method, path, host, headers, body,
                              timeout) -> tuple[int, dict, bytes]:
        """Send one HTTP/2 request on a new stream, wait for response."""
        if not self._connected:
            await self.ensure_connected()

        stream_id = None

        async with self._write_lock:
            try:
                stream_id = self._h2.get_next_available_stream_id()
            except Exception:
                # Connection is stale — reconnect
                await self.reconnect()
                stream_id = self._h2.get_next_available_stream_id()

            h2_headers = [
                (":method", method),
                (":path", path),
                (":authority", host),
                (":scheme", "https"),
                ("accept-encoding", codec.supported_encodings()),
            ]
            if headers:
                for k, v in headers.items():
                    h2_headers.append((k.lower(), str(v)))

            end_stream = not body
            self._h2.send_headers(stream_id, h2_headers, end_stream=end_stream)

            if body:
                # Send body (may need chunking for flow control)
                self._send_body(stream_id, body)

            state = _StreamState()
            self._streams[stream_id] = state
            self.total_streams += 1

            await self._flush()

        # Wait for complete response
        try:
            await asyncio.wait_for(state.done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            self._streams.pop(stream_id, None)
            raise TimeoutError(
                f"H2 stream {stream_id} timed out ({timeout}s)"
            )

        self._streams.pop(stream_id, None)

        if state.error:
            raise ConnectionError(f"H2 stream error: {state.error}")

        # Auto-decompress (gzip / deflate / brotli / zstd)
        resp_body = bytes(state.data)
        enc = state.headers.get("content-encoding", "")
        if enc:
            resp_body = codec.decode(resp_body, enc)

        return state.status, state.headers, resp_body

    def _send_body(self, stream_id: int, body: bytes):
        """Send request body, respecting H2 flow control window.

        The initial per-stream window is 8 MB (see _do_connect) which
        comfortably covers all relay JSON payloads. If the body is ever
        larger than the available window, we raise rather than silently
        truncate — the caller will retry on a fresh connection.
        """
        sent = 0
        total = len(body)
        while body:
            max_size = self._h2.local_settings.max_frame_size
            window = self._h2.local_flow_control_window(stream_id)
            send_size = min(len(body), max_size, window)
            if send_size <= 0:
                raise BufferError(
                    f"H2 flow control exhausted after {sent}/{total} bytes; "
                    f"increase initial window or shrink payload"
                )
            end = send_size >= len(body)
            self._h2.send_data(stream_id, body[:send_size], end_stream=end)
            body = body[send_size:]
            sent += send_size

    # ── Background reader ─────────────────────────────────────────

    async def _reader_loop(self, generation: int):
        """Background: read H2 frames, dispatch events to waiting streams."""
        try:
            while self._connected:
                data = await self._reader.read(65536)
                if not data:
                    log.warning("H2 remote closed connection")
                    break

                try:
                    events = self._h2.receive_data(data)
                except Exception as e:
                    log.error("H2 protocol error: %s", e)
                    break

                for event in events:
                    self._dispatch(event)

                # Send pending data (acks, window updates, ping responses)
                async with self._write_lock:
                    await self._flush()

        except asyncio.CancelledError:
            pass
        except ssl.SSLError as e:
            # APPLICATION_DATA_AFTER_CLOSE_NOTIFY is raised when the server
            # sends data after its TLS close_notify — technically a protocol
            # violation but very common with CDNs.  It just means the
            # connection is closed; reconnect on the next request.
            if "APPLICATION_DATA_AFTER_CLOSE_NOTIFY" in str(e):
                log.debug("H2 TLS session closed by remote (close_notify): %s", e)
            else:
                log.error("H2 reader error: %s", e)
        except Exception as e:
            # WinError 121 (semaphore timeout) — Windows OS-level socket
            # timeout meaning the TCP connection stalled and the OS closed
            # it.  Harmless; treat as a normal drop.  On non-Windows
            # platforms .winerror is absent so getattr returns None.
            if getattr(e, 'winerror', None) == 121:
                log.warning("H2 connection dropped (OS socket timeout)")
            elif "application data after close notify" in str(e).lower():
                log.debug("H2 reader closed after close_notify: %s", e)
            else:
                log.error("H2 reader error: %s", e)
        finally:
            if generation != self._conn_generation:
                log.debug("H2 reader loop ended for stale generation %d", generation)
            else:
                self._connected = False
                for state in self._streams.values():
                    if not state.done.is_set():
                        state.error = "Connection lost"
                        state.done.set()
                log.info("H2 reader loop ended")

    def _dispatch(self, event):
        """Route a single h2 event to its stream."""
        if isinstance(event, h2.events.ResponseReceived):
            state = self._streams.get(event.stream_id)
            if state:
                for name, value in event.headers:
                    n = name if isinstance(name, str) else name.decode()
                    v = value if isinstance(value, str) else value.decode()
                    if n == ":status":
                        state.status = int(v)
                    else:
                        state.headers[n] = v

        elif isinstance(event, h2.events.DataReceived):
            state = self._streams.get(event.stream_id)
            if state:
                state.data.extend(event.data)
            # Always acknowledge received data for flow control
            self._h2.acknowledge_received_data(
                event.flow_controlled_length, event.stream_id
            )

        elif isinstance(event, h2.events.StreamEnded):
            state = self._streams.get(event.stream_id)
            if state:
                state.done.set()

        elif isinstance(event, h2.events.StreamReset):
            state = self._streams.get(event.stream_id)
            if state:
                state.error = f"Stream reset (code={event.error_code})"
                state.done.set()

        elif isinstance(event, h2.events.WindowUpdated):
            pass  # h2 library handles window bookkeeping

        elif isinstance(event, h2.events.SettingsAcknowledged):
            pass

        elif isinstance(event, h2.events.PingReceived):
            pass  # h2 library auto-responds

        elif isinstance(event, h2.events.PingAckReceived):
            pass  # keepalive confirmed

    # ── Internal ──────────────────────────────────────────────────

    async def _flush(self):
        """Write pending H2 frame data to the socket."""
        data = self._h2.data_to_send()
        if data and self._writer:
            self._writer.write(data)
            await self._writer.drain()

    async def close(self):
        """Gracefully close the HTTP/2 connection."""
        if self._h2 and self._connected:
            try:
                self._h2.close_connection()
                async with self._write_lock:
                    await self._flush()
            except Exception:
                pass
        await self._close_internal()

    async def ping(self):
        """Send an H2 PING frame to keep the connection alive."""
        if not self._connected or not self._h2:
            return
        try:
            async with self._write_lock:
                if not self._connected:
                    return
                self._h2.ping(b"\x00" * 8)
                await self._flush()
        except Exception as e:
            log.debug("H2 PING failed: %s", e)
