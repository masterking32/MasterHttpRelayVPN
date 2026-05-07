# h2_transport.py
import asyncio
import gzip
import logging
import socket
import ssl
from urllib.parse import urlparse

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
    __slots__ = ("status", "headers", "data", "done", "error")
    def __init__(self):
        self.status = 0
        self.headers: dict[str, str] = {}
        self.data = bytearray()
        self.done = asyncio.Event()
        self.error: str | None = None

class H2Transport:
    def __init__(self, connect_host: str, sni_host: str, verify_ssl: bool = True):
        self.connect_host = connect_host
        self.sni_host = sni_host
        self.verify_ssl = verify_ssl
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._h2: "h2.connection.H2Connection | None" = None
        self._connected = False
        self._write_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._read_task: asyncio.Task | None = None
        self._streams: dict[int, _StreamState] = {}
        self.total_requests = 0
        self.total_streams = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def ensure_connected(self):
        if self._connected:
            return
        async with self._connect_lock:
            if self._connected:
                return
            await self._do_connect()

    async def _do_connect(self):
        ctx = ssl.create_default_context()
        ctx.set_alpn_protocols(["h2", "http/1.1"])
        if not self.verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        raw.setblocking(False)
        try:
            await asyncio.wait_for(asyncio.get_event_loop().sock_connect(raw, (self.connect_host, 443)), timeout=15)
            self._reader, self._writer = await asyncio.wait_for(asyncio.open_connection(ssl=ctx, server_hostname=self.sni_host, sock=raw), timeout=15)
        except Exception:
            raw.close()
            raise
        ssl_obj = self._writer.get_extra_info("ssl_object")
        negotiated = ssl_obj.selected_alpn_protocol() if ssl_obj else None
        if negotiated != "h2":
            self._writer.close()
            raise RuntimeError(f"H2 ALPN negotiation failed (got {negotiated!r})")
        config = h2.config.H2Configuration(client_side=True, header_encoding="utf-8")
        self._h2 = h2.connection.H2Connection(config=config)
        self._h2.initiate_connection()
        self._h2.increment_flow_control_window(2 ** 24 - 65535)
        self._h2.update_settings({h2.settings.SettingCodes.INITIAL_WINDOW_SIZE: 1 * 1024 * 1024, h2.settings.SettingCodes.ENABLE_PUSH: 0})
        await self._flush()
        self._connected = True
        self._read_task = asyncio.create_task(self._reader_loop())

    async def reconnect(self):
        await self._close_internal()
        await self._do_connect()

    async def _close_internal(self):
        self._connected = False
        if self._read_task:
            self._read_task.cancel()
            self._read_task = None
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass
            self._writer = None
        for state in self._streams.values():
            state.error = "Connection closed"
            state.done.set()
        self._streams.clear()

    async def request(self, method: str, path: str, host: str, headers: dict | None = None, body: bytes | None = None, timeout: float = 25, follow_redirects: int = 5) -> tuple[int, dict, bytes]:
        await self.ensure_connected()
        self.total_requests += 1
        for _ in range(follow_redirects + 1):
            status, resp_headers, resp_body = await self._single_request(method, path, host, headers, body, timeout)
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
            headers = None
        return status, resp_headers, resp_body

    async def _single_request(self, method, path, host, headers, body, timeout) -> tuple[int, dict, bytes]:
        if not self._connected:
            await self.ensure_connected()
        stream_id = None
        async with self._write_lock:
            try:
                stream_id = self._h2.get_next_available_stream_id()
            except Exception:
                await self.reconnect()
                stream_id = self._h2.get_next_available_stream_id()
            h2_headers = [(":method", method), (":path", path), (":authority", host), (":scheme", "https"), ("accept-encoding", "gzip")]
            if headers:
                for k, v in headers.items():
                    h2_headers.append((k.lower(), str(v)))
            end_stream = not body
            self._h2.send_headers(stream_id, h2_headers, end_stream=end_stream)
            if body:
                self._send_body(stream_id, body)
            state = _StreamState()
            self._streams[stream_id] = state
            self.total_streams += 1
            await self._flush()
        try:
            await asyncio.wait_for(state.done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            self._streams.pop(stream_id, None)
            raise TimeoutError(f"H2 stream {stream_id} timed out ({timeout}s)")
        self._streams.pop(stream_id, None)
        if state.error:
            raise ConnectionError(f"H2 stream error: {state.error}")
        resp_body = bytes(state.data)
        if state.headers.get("content-encoding", "").lower() == "gzip":
            try:
                resp_body = gzip.decompress(resp_body)
            except Exception:
                pass
        return state.status, state.headers, resp_body

    def _send_body(self, stream_id: int, body: bytes):
        while body:
            max_size = self._h2.local_settings.max_frame_size
            window = self._h2.local_flow_control_window(stream_id)
            send_size = min(len(body), max_size, window)
            if send_size <= 0:
                break
            end = send_size >= len(body)
            self._h2.send_data(stream_id, body[:send_size], end_stream=end)
            body = body[send_size:]

    async def _reader_loop(self):
        try:
            while self._connected:
                data = await self._reader.read(65536)
                if not data:
                    break
                try:
                    events = self._h2.receive_data(data)
                except Exception:
                    break
                for event in events:
                    self._dispatch(event)
                async with self._write_lock:
                    await self._flush()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        finally:
            self._connected = False
            for state in self._streams.values():
                if not state.done.is_set():
                    state.error = "Connection lost"
                    state.done.set()

    def _dispatch(self, event):
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
            self._h2.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
        elif isinstance(event, h2.events.StreamEnded):
            state = self._streams.get(event.stream_id)
            if state:
                state.done.set()
        elif isinstance(event, h2.events.StreamReset):
            state = self._streams.get(event.stream_id)
            if state:
                state.error = f"Stream reset (code={event.error_code})"
                state.done.set()

    async def _flush(self):
        data = self._h2.data_to_send()
        if data and self._writer:
            self._writer.write(data)
            await self._writer.drain()

    async def close(self):
        if self._h2 and self._connected:
            try:
                self._h2.close_connection()
                async with self._write_lock:
                    await self._flush()
            except Exception:
                pass
        await self._close_internal()

    async def ping(self):
        if not self._connected or not self._h2:
            return
        try:
            async with self._write_lock:
                if not self._connected:
                    return
                self._h2.ping(b"\x00" * 8)
                await self._flush()
        except Exception:
            pass
