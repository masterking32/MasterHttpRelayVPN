import asyncio
import base64
import gzip
import json
import logging
import os
import re
import ssl
import time
from urllib.parse import urlparse
from ws import ws_encode, ws_decode

log = logging.getLogger("Fronter")

class DomainFronter:
    def __init__(self, config: dict):
        mode = config.get("mode", "domain_fronting")
        if mode == "custom_domain":
            domain = config["custom_domain"]
            self.connect_host = domain
            self.sni_host = domain
            self.http_host = domain
        elif mode == "google_fronting":
            self.connect_host = config.get("google_ip", "216.239.38.120")
            self.sni_host = config.get("front_domain", "www.google.com")
            self.http_host = config["worker_host"]
        elif mode == "apps_script":
            self.connect_host = config.get("google_ip", "216.239.38.120")
            self.sni_host = config.get("front_domain", "www.google.com")
            self.http_host = "script.google.com"
            script = config.get("script_ids") or config.get("script_id")
            self._script_ids = script if isinstance(script, list) else [script]
            self._script_idx = 0
            self.script_id = self._script_ids[0]
            self._dev_available = False
        else:
            self.connect_host = config["front_domain"]
            self.sni_host = config["front_domain"]
            self.http_host = config["worker_host"]
        self.mode = mode
        self.worker_path = config.get("worker_path", "")
        self.auth_key = config.get("auth_key", "")
        self.verify_ssl = config.get("verify_ssl", True)
        self._pool: list[tuple[asyncio.StreamReader, asyncio.StreamWriter, float]] = []
        self._pool_lock = asyncio.Lock()
        self._pool_max = 50
        self._conn_ttl = 45.0
        self._semaphore = asyncio.Semaphore(50)
        self._warmed = False
        self._refilling = False
        self._pool_min_idle = 15
        self._maintenance_task: asyncio.Task | None = None
        self._batch_lock = asyncio.Lock()
        self._batch_pending: list[tuple[dict, asyncio.Future]] = []
        self._batch_task: asyncio.Task | None = None
        self._batch_window_micro = 0.005
        self._batch_window_macro = 0.050
        self._batch_max = 50
        self._batch_enabled = True
        self._coalesce: dict[str, list[asyncio.Future]] = {}
        self._h2 = None
        if mode == "apps_script":
            try:
                from h2_transport import H2Transport, H2_AVAILABLE
                if H2_AVAILABLE:
                    self._h2 = H2Transport(self.connect_host, self.sni_host, self.verify_ssl)
            except ImportError:
                pass

    def _ssl_ctx(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if not self.verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def _open(self):
        return await asyncio.open_connection(self.connect_host, 443, ssl=self._ssl_ctx(), server_hostname=self.sni_host)

    async def _acquire(self):
        now = asyncio.get_event_loop().time()
        async with self._pool_lock:
            while self._pool:
                reader, writer, created = self._pool.pop()
                if (now - created) < self._conn_ttl and not reader.at_eof():
                    asyncio.create_task(self._add_conn_to_pool())
                    return reader, writer, created
                try:
                    writer.close()
                except Exception:
                    pass
        reader, writer = await asyncio.wait_for(self._open(), timeout=10)
        if not self._refilling:
            self._refilling = True
            asyncio.create_task(self._refill_pool())
        return reader, writer, asyncio.get_event_loop().time()

    async def _release(self, reader, writer, created):
        now = asyncio.get_event_loop().time()
        if (now - created) >= self._conn_ttl or reader.at_eof():
            try:
                writer.close()
            except Exception:
                pass
            return
        async with self._pool_lock:
            if len(self._pool) < self._pool_max:
                self._pool.append((reader, writer, created))
            else:
                try:
                    writer.close()
                except Exception:
                    pass

    def _next_script_id(self) -> str:
        sid = self._script_ids[self._script_idx % len(self._script_ids)]
        self._script_idx += 1
        return sid

    def _exec_path(self) -> str:
        sid = self._next_script_id()
        return f"/macros/s/{sid}/{'dev' if self._dev_available else 'exec'}"

    async def _flush_pool(self):
        async with self._pool_lock:
            for _, writer, _ in self._pool:
                try:
                    writer.close()
                except Exception:
                    pass
            self._pool.clear()

    async def _refill_pool(self):
        try:
            coros = [self._add_conn_to_pool() for _ in range(8)]
            await asyncio.gather(*coros, return_exceptions=True)
        finally:
            self._refilling = False

    async def _add_conn_to_pool(self):
        try:
            r, w = await asyncio.wait_for(self._open(), timeout=5)
            t = asyncio.get_event_loop().time()
            async with self._pool_lock:
                if len(self._pool) < self._pool_max:
                    self._pool.append((r, w, t))
                else:
                    try:
                        w.close()
                    except Exception:
                        pass
        except Exception:
            pass

    async def _pool_maintenance(self):
        while True:
            try:
                await asyncio.sleep(3)
                now = asyncio.get_event_loop().time()
                async with self._pool_lock:
                    alive = []
                    for r, w, t in self._pool:
                        if (now - t) < self._conn_ttl and not r.at_eof():
                            alive.append((r, w, t))
                        else:
                            try:
                                w.close()
                            except Exception:
                                pass
                    self._pool = alive
                    idle = len(self._pool)
                needed = max(0, self._pool_min_idle - idle)
                if needed > 0:
                    coros = [self._add_conn_to_pool() for _ in range(min(needed, 5))]
                    await asyncio.gather(*coros, return_exceptions=True)
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def _warm_pool(self):
        if self._warmed:
            return
        self._warmed = True
        asyncio.create_task(self._do_warm())
        if self._maintenance_task is None:
            self._maintenance_task = asyncio.create_task(self._pool_maintenance())
        if self._h2:
            asyncio.create_task(self._h2_connect_and_warm())

    async def _h2_connect(self):
        try:
            await self._h2.ensure_connected()
        except Exception:
            pass

    async def _h2_connect_and_warm(self):
        await self._h2_connect()
        if self._h2 and self._h2.is_connected:
            asyncio.create_task(self._prewarm_script())
            asyncio.create_task(self._keepalive_loop())

    async def _prewarm_script(self):
        payload = json.dumps({"m": "HEAD", "u": "http://example.com/", "k": self.auth_key}).encode()
        hdrs = {"content-type": "application/json"}
        sid = self._script_ids[0]
        try:
            dev_path = f"/macros/s/{sid}/dev"
            status, _, body = await asyncio.wait_for(self._h2.request(method="POST", path=dev_path, host=self.http_host, headers=hdrs, body=payload), timeout=15)
            data = json.loads(body.decode(errors="replace"))
            if "s" in data:
                self._dev_available = True
                return
        except Exception:
            pass
        try:
            exec_path = f"/macros/s/{sid}/exec"
            await asyncio.wait_for(self._h2.request(method="POST", path=exec_path, host=self.http_host, headers=hdrs, body=payload), timeout=15)
        except Exception:
            pass

    async def _keepalive_loop(self):
        while True:
            try:
                await asyncio.sleep(240)
                if not self._h2 or not self._h2.is_connected:
                    try:
                        await self._h2.reconnect()
                    except Exception:
                        continue
                await self._h2.ping()
                payload = {"m": "HEAD", "u": "http://example.com/", "k": self.auth_key}
                path = self._exec_path()
                await asyncio.wait_for(self._h2.request(method="POST", path=path, host=self.http_host, headers={"content-type": "application/json"}, body=json.dumps(payload).encode()), timeout=20)
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def _do_warm(self):
        count = 30
        coros = [self._add_conn_to_pool() for _ in range(count)]
        await asyncio.gather(*coros, return_exceptions=True)

    def _auth_header(self) -> str:
        return f"X-Auth-Key: {self.auth_key}\r\n" if self.auth_key else ""

    async def tunnel(self, target_host: str, target_port: int, client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter):
        try:
            remote_r, remote_w = await self._open()
        except Exception:
            return
        try:
            ws_key = base64.b64encode(os.urandom(16)).decode()
            path = f"{self.worker_path}/tunnel?host={target_host}&port={target_port}"
            handshake = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {self.http_host}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {ws_key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n"
                f"{self._auth_header()}"
                f"\r\n"
            )
            remote_w.write(handshake.encode())
            await remote_w.drain()
            resp = b""
            while b"\r\n\r\n" not in resp:
                chunk = await asyncio.wait_for(remote_r.read(4096), timeout=15)
                if not chunk:
                    raise ConnectionError("No response")
                resp += chunk
            status_line = resp.split(b"\r\n")[0]
            if b"101" not in status_line:
                raise ConnectionError("Upgrade rejected")
            await asyncio.gather(self._client_to_ws(client_r, remote_w), self._ws_to_client(remote_r, client_w))
        except Exception:
            pass
        finally:
            try:
                remote_w.close()
            except Exception:
                pass

    async def _client_to_ws(self, src: asyncio.StreamReader, dst: asyncio.StreamWriter):
        try:
            while True:
                data = await src.read(16384)
                if not data:
                    dst.write(ws_encode(b"", opcode=0x08))
                    await dst.drain()
                    break
                dst.write(ws_encode(data))
                await dst.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass

    async def _ws_to_client(self, src: asyncio.StreamReader, dst: asyncio.StreamWriter):
        buf = b""
        try:
            while True:
                chunk = await src.read(16384)
                if not chunk:
                    break
                buf += chunk
                while buf:
                    result = ws_decode(buf)
                    if result is None:
                        break
                    opcode, payload, consumed = result
                    buf = buf[consumed:]
                    if opcode == 0x08:
                        return
                    if payload:
                        dst.write(payload)
                        await dst.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass

    async def forward(self, raw_request: bytes) -> bytes:
        try:
            reader, writer, created = await self._acquire()
            request = (
                f"POST {self.worker_path}/forward HTTP/1.1\r\n"
                f"Host: {self.http_host}\r\n"
                f"Content-Type: application/octet-stream\r\n"
                f"Content-Length: {len(raw_request)}\r\n"
                f"Connection: keep-alive\r\n"
                f"{self._auth_header()}"
                f"\r\n"
            )
            writer.write(request.encode() + raw_request)
            await writer.drain()
            status, resp_headers, resp_body = await self._read_http_response(reader)
            await self._release(reader, writer, created)
            return resp_body
        except Exception:
            return b"HTTP/1.1 502 Bad Gateway\r\n\r\nDomain fronting request failed\r\n"

    async def relay(self, method: str, url: str, headers: dict, body: bytes = b"") -> bytes:
        if not self._warmed:
            await self._warm_pool()
        payload = self._build_payload(method, url, headers, body)
        has_range = False
        if headers:
            for k in headers:
                if k.lower() == "range":
                    has_range = True
                    break
        if method == "GET" and not body and not has_range:
            return await self._coalesced_submit(url, payload)
        return await self._batch_submit(payload)

    async def _coalesced_submit(self, url: str, payload: dict) -> bytes:
        if url in self._coalesce:
            future = asyncio.get_event_loop().create_future()
            self._coalesce[url].append(future)
            return await future
        self._coalesce[url] = []
        try:
            result = await self._batch_submit(payload)
            for f in self._coalesce.get(url, []):
                if not f.done():
                    f.set_result(result)
            return result
        except Exception as e:
            for f in self._coalesce.get(url, []):
                if not f.done():
                    f.set_exception(e)
            raise
        finally:
            self._coalesce.pop(url, None)

    async def relay_parallel(self, method: str, url: str, headers: dict, body: bytes = b"", chunk_size: int = 256 * 1024, max_parallel: int = 16) -> bytes:
        if method != "GET" or body:
            return await self.relay(method, url, headers, body)
        range_headers = dict(headers) if headers else {}
        range_headers["Range"] = f"bytes=0-{chunk_size - 1}"
        first_resp = await self.relay("GET", url, range_headers, b"")
        status, resp_hdrs, resp_body = self._split_raw_response(first_resp)
        if status != 206:
            return first_resp
        content_range = resp_hdrs.get("content-range", "")
        m = re.search(r"/(\d+)", content_range)
        if not m:
            return self._rewrite_206_to_200(first_resp)
        total_size = int(m.group(1))
        if total_size <= chunk_size or len(resp_body) >= total_size:
            return self._rewrite_206_to_200(first_resp)
        ranges = []
        start = len(resp_body)
        while start < total_size:
            end = min(start + chunk_size - 1, total_size - 1)
            ranges.append((start, end))
            start = end + 1
        sem = asyncio.Semaphore(max_parallel)

        async def fetch_range(s, e, max_tries: int = 3):
            async with sem:
                rh_base = dict(headers) if headers else {}
                rh_base["Range"] = f"bytes={s}-{e}"
                expected = e - s + 1
                for attempt in range(max_tries):
                    try:
                        raw = await self.relay("GET", url, rh_base, b"")
                        _, _, chunk_body = self._split_raw_response(raw)
                        if len(chunk_body) == expected:
                            return chunk_body
                    except Exception:
                        pass
                    await asyncio.sleep(0.3 * (attempt + 1))
                raise RuntimeError("chunk failed")

        results = await asyncio.gather(*[fetch_range(s, e) for s, e in ranges], return_exceptions=True)
        parts = [resp_body]
        for r in results:
            if isinstance(r, Exception):
                return self._error_response(502, f"Parallel download failed")
            parts.append(r)
        full_body = b"".join(parts)
        result = f"HTTP/1.1 200 OK\r\n"
        skip = {"transfer-encoding", "connection", "keep-alive", "content-length", "content-encoding", "content-range"}
        for k, v in resp_hdrs.items():
            if k.lower() not in skip:
                result += f"{k}: {v}\r\n"
        result += f"Content-Length: {len(full_body)}\r\n\r\n"
        return result.encode() + full_body

    @staticmethod
    def _rewrite_206_to_200(raw: bytes) -> bytes:
        sep = b"\r\n\r\n"
        if sep not in raw:
            return raw
        header_section, body = raw.split(sep, 1)
        lines = header_section.decode(errors="replace").split("\r\n")
        if not lines:
            return raw
        first = lines[0]
        if " 206" in first:
            lines[0] = first.replace(" 206 Partial Content", " 200 OK").replace(" 206", " 200 OK")
        filtered = [lines[0]]
        for ln in lines[1:]:
            low = ln.lower()
            if low.startswith("content-range:") or low.startswith("content-length:"):
                continue
            filtered.append(ln)
        filtered.append(f"Content-Length: {len(body)}")
        return ("\r\n".join(filtered) + "\r\n\r\n").encode() + body

    def _build_payload(self, method, url, headers, body):
        payload = {"m": method, "u": url, "r": True}
        if headers:
            filt = {k: v for k, v in headers.items() if k.lower() != "accept-encoding"}
            payload["h"] = filt if filt else headers
        if body:
            payload["b"] = base64.b64encode(body).decode()
            ct = headers.get("Content-Type") or headers.get("content-type")
            if ct:
                payload["ct"] = ct
        return payload

    async def _batch_submit(self, payload: dict) -> bytes:
        if not self._batch_enabled:
            return await self._relay_with_retry(payload)
        future = asyncio.get_event_loop().create_future()
        async with self._batch_lock:
            self._batch_pending.append((payload, future))
            if len(self._batch_pending) >= self._batch_max:
                batch = self._batch_pending[:]
                self._batch_pending.clear()
                if self._batch_task and not self._batch_task.done():
                    self._batch_task.cancel()
                self._batch_task = None
                asyncio.create_task(self._batch_send(batch))
            elif self._batch_task is None or self._batch_task.done():
                self._batch_task = asyncio.create_task(self._batch_timer())
        return await future

    async def _batch_timer(self):
        await asyncio.sleep(self._batch_window_micro)
        async with self._batch_lock:
            if len(self._batch_pending) <= 1:
                if self._batch_pending:
                    batch = self._batch_pending[:]
                    self._batch_pending.clear()
                    self._batch_task = None
                    asyncio.create_task(self._batch_send(batch))
                return
        await asyncio.sleep(self._batch_window_macro - self._batch_window_micro)
        async with self._batch_lock:
            if self._batch_pending:
                batch = self._batch_pending[:]
                self._batch_pending.clear()
                self._batch_task = None
                asyncio.create_task(self._batch_send(batch))

    async def _batch_send(self, batch: list):
        if len(batch) == 1:
            payload, future = batch[0]
            try:
                result = await self._relay_with_retry(payload)
                if not future.done():
                    future.set_result(result)
            except Exception as e:
                if not future.done():
                    future.set_result(self._error_response(502, str(e)))
        else:
            try:
                results = await self._relay_batch([p for p, _ in batch])
                for (_, future), result in zip(batch, results):
                    if not future.done():
                        future.set_result(result)
            except Exception as e:
                self._batch_enabled = False
                tasks = []
                for payload, future in batch:
                    tasks.append(self._relay_fallback(payload, future))
                await asyncio.gather(*tasks)

    async def _relay_fallback(self, payload, future):
        try:
            result = await self._relay_with_retry(payload)
            if not future.done():
                future.set_result(result)
        except Exception as e:
            if not future.done():
                future.set_result(self._error_response(502, str(e)))

    async def _relay_with_retry(self, payload: dict) -> bytes:
        if self._h2 and self._h2.is_connected:
            for attempt in range(2):
                try:
                    return await asyncio.wait_for(self._relay_single_h2(payload), timeout=25)
                except Exception:
                    if attempt == 0:
                        try:
                            await self._h2.reconnect()
                        except Exception:
                            break
                    else:
                        raise
        async with self._semaphore:
            for attempt in range(2):
                try:
                    return await asyncio.wait_for(self._relay_single(payload), timeout=25)
                except Exception:
                    if attempt == 0:
                        await self._flush_pool()
                    else:
                        raise

    async def _relay_single_h2(self, payload: dict) -> bytes:
        full_payload = dict(payload)
        full_payload["k"] = self.auth_key
        json_body = json.dumps(full_payload).encode()
        path = self._exec_path()
        status, headers, body = await self._h2.request(method="POST", path=path, host=self.http_host, headers={"content-type": "application/json"}, body=json_body)
        return self._parse_relay_response(body)

    async def _relay_single(self, payload: dict) -> bytes:
        full_payload = dict(payload)
        full_payload["k"] = self.auth_key
        json_body = json.dumps(full_payload).encode()
        path = self._exec_path()
        reader, writer, created = await self._acquire()
        try:
            request = (
                f"POST {path} HTTP/1.1\r\n"
                f"Host: {self.http_host}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(json_body)}\r\n"
                f"Accept-Encoding: gzip\r\n"
                f"Connection: keep-alive\r\n\r\n"
            )
            writer.write(request.encode() + json_body)
            await writer.drain()
            status, resp_headers, resp_body = await self._read_http_response(reader)
            for _ in range(5):
                if status not in (301, 302, 303, 307, 308):
                    break
                location = resp_headers.get("location")
                if not location:
                    break
                parsed = urlparse(location)
                rpath = parsed.path + ("?" + parsed.query if parsed.query else "")
                request = (
                    f"GET {rpath} HTTP/1.1\r\n"
                    f"Host: {parsed.netloc}\r\n"
                    f"Accept-Encoding: gzip\r\n"
                    f"Connection: keep-alive\r\n\r\n"
                )
                writer.write(request.encode())
                await writer.drain()
                status, resp_headers, resp_body = await self._read_http_response(reader)
            await self._release(reader, writer, created)
            return self._parse_relay_response(resp_body)
        except Exception:
            try:
                writer.close()
            except Exception:
                pass
            raise

    async def _relay_batch(self, payloads: list[dict]) -> list[bytes]:
        batch_payload = {"k": self.auth_key, "q": payloads}
        json_body = json.dumps(batch_payload).encode()
        path = self._exec_path()
        if self._h2 and self._h2.is_connected:
            try:
                status, headers, body = await asyncio.wait_for(self._h2.request(method="POST", path=path, host=self.http_host, headers={"content-type": "application/json"}, body=json_body), timeout=30)
                return self._parse_batch_body(body, payloads)
            except Exception:
                pass
        async with self._semaphore:
            reader, writer, created = await self._acquire()
            try:
                request = (
                    f"POST {path} HTTP/1.1\r\n"
                    f"Host: {self.http_host}\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(json_body)}\r\n"
                    f"Accept-Encoding: gzip\r\n"
                    f"Connection: keep-alive\r\n\r\n"
                )
                writer.write(request.encode() + json_body)
                await writer.drain()
                status, resp_headers, resp_body = await self._read_http_response(reader)
                for _ in range(5):
                    if status not in (301, 302, 303, 307, 308):
                        break
                    location = resp_headers.get("location")
                    if not location:
                        break
                    parsed = urlparse(location)
                    rpath = parsed.path + ("?" + parsed.query if parsed.query else "")
                    request = (
                        f"GET {rpath} HTTP/1.1\r\n"
                        f"Host: {parsed.netloc}\r\n"
                        f"Accept-Encoding: gzip\r\n"
                        f"Connection: keep-alive\r\n\r\n"
                    )
                    writer.write(request.encode())
                    await writer.drain()
                    status, resp_headers, resp_body = await self._read_http_response(reader)
                await self._release(reader, writer, created)
            except Exception:
                try:
                    writer.close()
                except Exception:
                    pass
                raise
        return self._parse_batch_body(resp_body, payloads)

    def _parse_batch_body(self, resp_body: bytes, payloads: list[dict]) -> list[bytes]:
        text = resp_body.decode(errors="replace").strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r'\{.*\}', text, re.DOTALL)
            data = json.loads(m.group()) if m else None
        if not data:
            raise RuntimeError("Bad batch response")
        if "e" in data:
            raise RuntimeError(f"Batch error: {data['e']}")
        items = data.get("q", [])
        if len(items) != len(payloads):
            raise RuntimeError("Batch size mismatch")
        results = []
        for item in items:
            results.append(self._parse_relay_json(item))
        return results

    async def _read_http_response(self, reader: asyncio.StreamReader):
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = await asyncio.wait_for(reader.read(8192), timeout=8)
            if not chunk:
                break
            raw += chunk
        if b"\r\n\r\n" not in raw:
            return 0, {}, b""
        header_section, body = raw.split(b"\r\n\r\n", 1)
        lines = header_section.split(b"\r\n")
        status_line = lines[0].decode(errors="replace")
        m = re.search(r"\d{3}", status_line)
        status = int(m.group()) if m else 0
        headers = {}
        for line in lines[1:]:
            if b":" in line:
                k, v = line.decode(errors="replace").split(":", 1)
                headers[k.strip().lower()] = v.strip()
        content_length = headers.get("content-length")
        transfer_encoding = headers.get("transfer-encoding", "")
        if "chunked" in transfer_encoding:
            body = await self._read_chunked(reader, body)
        elif content_length:
            remaining = int(content_length) - len(body)
            while remaining > 0:
                chunk = await asyncio.wait_for(reader.read(min(remaining, 65536)), timeout=20)
                if not chunk:
                    break
                body += chunk
                remaining -= len(chunk)
        else:
            while True:
                try:
                    chunk = await asyncio.wait_for(reader.read(65536), timeout=2)
                    if not chunk:
                        break
                    body += chunk
                except asyncio.TimeoutError:
                    break
        if headers.get("content-encoding", "").lower() == "gzip":
            try:
                body = gzip.decompress(body)
            except Exception:
                pass
        return status, headers, body

    async def _read_chunked(self, reader, buf=b""):
        result = b""
        while True:
            while b"\r\n" not in buf:
                data = await asyncio.wait_for(reader.read(8192), timeout=20)
                if not data:
                    return result
                buf += data
            end = buf.find(b"\r\n")
            size_str = buf[:end].decode(errors="replace").strip()
            buf = buf[end + 2:]
            if not size_str:
                continue
            try:
                size = int(size_str, 16)
            except ValueError:
                break
            if size == 0:
                break
            while len(buf) < size + 2:
                data = await asyncio.wait_for(reader.read(65536), timeout=20)
                if not data:
                    result += buf[:size]
                    return result
                buf += data
            result += buf[:size]
            buf = buf[size + 2:]
        return result

    def _parse_relay_response(self, body: bytes) -> bytes:
        text = body.decode(errors="replace").strip()
        if not text:
            return self._error_response(502, "Empty response from relay")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r'\{.*\}', text, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group())
                except json.JSONDecodeError:
                    return self._error_response(502, "Bad JSON")
            else:
                return self._error_response(502, "No JSON")
        return self._parse_relay_json(data)

    def _parse_relay_json(self, data: dict) -> bytes:
        if "e" in data:
            return self._error_response(502, f"Relay error: {data['e']}")
        status = data.get("s", 200)
        resp_headers = data.get("h", {})
        resp_body = base64.b64decode(data.get("b", ""))
        status_text = {200: "OK", 206: "Partial Content", 301: "Moved", 302: "Found", 304: "Not Modified", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 500: "Internal Server Error"}.get(status, "OK")
        result = f"HTTP/1.1 {status} {status_text}\r\n"
        skip = {"transfer-encoding", "connection", "keep-alive", "content-length", "content-encoding"}
        for k, v in resp_headers.items():
            if k.lower() in skip:
                continue
            values = v if isinstance(v, list) else [v]
            if k.lower() == "set-cookie":
                expanded = []
                for item in values:
                    expanded.extend(self._split_set_cookie(str(item)))
                values = expanded
            for val in values:
                result += f"{k}: {val}\r\n"
        result += f"Content-Length: {len(resp_body)}\r\n\r\n"
        return result.encode() + resp_body

    @staticmethod
    def _split_set_cookie(blob: str) -> list[str]:
        if not blob:
            return []
        parts = re.split(r",\s*(?=[A-Za-z0-9!#$%&'*+\-.^_`|~]+=)", blob)
        return [p.strip() for p in parts if p.strip()]

    def _split_raw_response(self, raw: bytes):
        if b"\r\n\r\n" not in raw:
            return 0, {}, raw
        header_section, body = raw.split(b"\r\n\r\n", 1)
        lines = header_section.split(b"\r\n")
        m = re.search(r"\d{3}", lines[0].decode(errors="replace"))
        status = int(m.group()) if m else 0
        headers = {}
        for line in lines[1:]:
            if b":" in line:
                k, v = line.decode(errors="replace").split(":", 1)
                headers[k.strip().lower()] = v.strip()
        return status, headers, body

    def _error_response(self, status: int, message: str) -> bytes:
        body = f"<html><body><h1>{status}</h1><p>{message}</p></body></html>"
        return (f"HTTP/1.1 {status} Error\r\nContent-Type: text/html\r\nContent-Length: {len(body)}\r\n\r\n{body}").encode()
