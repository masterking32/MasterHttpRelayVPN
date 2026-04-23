"""
Apps Script relay engine.

Domain fronting via Google Apps Script: POST JSON to script.google.com
(fronted through www.google.com). Apps Script fetches the target URL and
returns the response.

  relay()   — JSON-based HTTP relay through Apps Script
"""

import asyncio
import base64
import gzip
import hashlib
import http
import json
import logging
import re
import socket
import ssl
import time
from dataclasses import dataclass
import urllib.parse
from urllib.parse import urlparse

import codec
from constants import (
    BATCH_MAX,
    BATCH_WINDOW_MACRO,
    BATCH_WINDOW_MICRO,
    CONN_TTL,
    FRONT_SNI_POOL_GOOGLE,
    POOL_MAX,
    POOL_MIN_IDLE,
    RELAY_TIMEOUT,
    SCRIPT_BLACKLIST_TTL,
    SEMAPHORE_MAX,
    STATEFUL_HEADER_NAMES,
    STATIC_EXTS,
    STATS_LOG_INTERVAL,
    STATS_LOG_TOP_N,
    TLS_CONNECT_TIMEOUT,
    WARM_POOL_COUNT,
)

log = logging.getLogger("Fronter")


@dataclass
class HostStat:
    """Per-host traffic accounting — useful for profiling slow / heavy sites."""
    requests: int = 0
    cache_hits: int = 0
    bytes: int = 0
    total_latency_ns: int = 0
    errors: int = 0


def _build_sni_pool(front_domain: str, overrides: list | None) -> list[str]:
    """Build the list of SNIs to rotate through on new outbound TLS handshakes.

    Priority:
      1. Explicit `front_domains` list in config (overrides).
      2. If `front_domain` is a Google property, use FRONT_SNI_POOL_GOOGLE
         (all share the same Google edge IP, so rotation is invisible to
         the relay but breaks DPI's "always www.google.com" heuristic).
      3. Fall back to the single configured `front_domain`.
    """
    if overrides:
        seen: set[str] = set()
        out: list[str] = []
        for item in overrides:
            host = str(item).strip().lower().rstrip(".")
            if host and host not in seen:
                seen.add(host)
                out.append(host)
        if out:
            return out
    fd = (front_domain or "").lower().rstrip(".")
    if fd.endswith(".google.com") or fd == "google.com":
        # Ensure the configured front_domain is first (stable default).
        pool = [fd] + [h for h in FRONT_SNI_POOL_GOOGLE if h != fd]
        return pool
    return [fd] if fd else ["www.google.com"]


class DomainFronter:
    _STATIC_EXTS = STATIC_EXTS

    def __init__(self, config: dict):
        self.connect_host = config.get("google_ip", "216.239.38.120")
        self.sni_host = config.get("front_domain", "www.google.com")
        # SNI rotation pool — rotated per new outbound TLS connection so
        # DPI systems can't fingerprint traffic as "always one SNI".
        self._sni_hosts = _build_sni_pool(
            self.sni_host, config.get("front_domains"),
        )
        self._sni_idx = 0
        self.http_host = "script.google.com"
        # Multi-script round-robin for higher throughput
        script = config.get("script_ids") or config.get("script_id")
        self._script_ids = script if isinstance(script, list) else [script]
        self._script_idx = 0
        self.script_id = self._script_ids[0]  # backward compat / logging
        self._dev_available = False  # True if /dev endpoint works (no redirect, ~400ms faster)

        # Fan-out parallel relay: fire N Apps Script instances concurrently,
        # keep the first successful response, cancel the rest. Script IDs
        # that fail or time out get blacklisted for SCRIPT_BLACKLIST_TTL so
        # a single slow container stops poisoning tail latency.
        try:
            self._parallel_relay = int(config.get("parallel_relay", 1))
        except (TypeError, ValueError):
            self._parallel_relay = 1
        self._parallel_relay = max(1, min(self._parallel_relay,
                                          len(self._script_ids)))
        self._sid_blacklist: dict[str, float] = {}
        self._blacklist_ttl = SCRIPT_BLACKLIST_TTL

        # Per-host stats (requests, cache hits, bytes, cumulative latency).
        self._per_site: dict[str, HostStat] = {}
        self._stats_task: asyncio.Task | None = None

        self.auth_key = config.get("auth_key", "")
        self.verify_ssl = config.get("verify_ssl", True)

        # Connection pool
        self._pool: list[tuple[asyncio.StreamReader, asyncio.StreamWriter, float]] =[]
        self._pool_lock = asyncio.Lock()
        self._pool_max = POOL_MAX
        self._conn_ttl = CONN_TTL
        self._semaphore = asyncio.Semaphore(SEMAPHORE_MAX)
        self._warmed = False
        self._refilling = False
        self._pool_min_idle = POOL_MIN_IDLE
        self._maintenance_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._warm_task: asyncio.Task | None = None
        self._bg_tasks: set[asyncio.Task] = set()

        # Batch collector
        self._batch_lock = asyncio.Lock()
        self._batch_pending: list[tuple[dict, asyncio.Future]] =[]
        self._batch_task: asyncio.Task | None = None
        self._batch_window_micro = BATCH_WINDOW_MICRO
        self._batch_window_macro = BATCH_WINDOW_MACRO
        self._batch_max = BATCH_MAX
        self._batch_enabled = True

        # Request coalescing
        self._coalesce: dict[str, list[asyncio.Future]] = {}

        # HTTP/2 multiplexing
        self._h2 = None
        try:
            from h2_transport import H2Transport, H2_AVAILABLE
            if H2_AVAILABLE:
                self._h2 = H2Transport(
                    self.connect_host, self.sni_host, self.verify_ssl,
                    sni_hosts=self._sni_hosts,
                )
                log.info("HTTP/2 multiplexing available — "
                         "all requests will share one connection")
        except ImportError:
            pass

        if len(self._sni_hosts) > 1:
            log.info("SNI rotation pool (%d): %s",
                     len(self._sni_hosts), ", ".join(self._sni_hosts))
        if self._parallel_relay > 1:
            log.info("Fan-out relay: %d parallel Apps Script instances per request",
                     self._parallel_relay)

        # Capability log for content encodings.
        log.info("Response codecs: %s", codec.supported_encodings())

    def _ssl_ctx(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if not self.verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def _open(self):
        """Open a TLS connection to the CDN.

        - TCP_NODELAY is set on the underlying socket so small H2/H1 writes
          aren't held back by Nagle's algorithm (up to ~40 ms per batch).
        - The *server_hostname* parameter sets the **TLS SNI** extension;
          we rotate across `self._sni_hosts` so DPI can't fingerprint
          "always www.google.com" from the client side.
        """
        loop = asyncio.get_event_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setblocking(False)
        try:
            await loop.sock_connect(sock, (self.connect_host, 443))
            return await asyncio.open_connection(
                sock=sock,
                ssl=self._ssl_ctx(),
                server_hostname=self._next_sni(),
            )
        except Exception:
            try:
                sock.close()
            except Exception:
                pass
            raise

    def _next_sni(self) -> str:
        """Round-robin the next SNI from the rotation pool."""
        sni = self._sni_hosts[self._sni_idx % len(self._sni_hosts)]
        self._sni_idx += 1
        return sni

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
        reader, writer = await asyncio.wait_for(
            self._open(), timeout=TLS_CONNECT_TIMEOUT
        )
        # Pool was empty — trigger aggressive background refill
        if not self._refilling:
            self._refilling = True
            self._spawn(self._refill_pool())
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
        """Round-robin across script IDs for load distribution.

        Skips script IDs currently in the short-term blacklist (failing
        or slow) unless *all* are blacklisted, in which case we fall back
        to plain round-robin so traffic can still flow.
        """
        n = len(self._script_ids)
        for _ in range(n):
            sid = self._script_ids[self._script_idx % n]
            self._script_idx += 1
            if not self._is_sid_blacklisted(sid):
                return sid
        # All blacklisted — clear expired entries and fall back.
        self._prune_blacklist(force=True)
        sid = self._script_ids[self._script_idx % n]
        self._script_idx += 1
        return sid

    def _is_sid_blacklisted(self, sid: str) -> bool:
        until = self._sid_blacklist.get(sid, 0.0)
        if until and until > time.time():
            return True
        if until:
            self._sid_blacklist.pop(sid, None)
        return False

    def _blacklist_sid(self, sid: str, reason: str = "") -> None:
        """Blacklist a script ID for SCRIPT_BLACKLIST_TTL seconds."""
        if len(self._script_ids) <= 1:
            return  # Nothing to fall back to — blacklist would be pointless.
        self._sid_blacklist[sid] = time.time() + self._blacklist_ttl
        log.warning("Blacklisted script %s for %ds%s",
                    sid[-8:] if len(sid) > 8 else sid,
                    int(self._blacklist_ttl),
                    f" ({reason})" if reason else "")

    def _prune_blacklist(self, force: bool = False) -> None:
        now = time.time()
        for sid, until in list(self._sid_blacklist.items()):
            if force or until <= now:
                self._sid_blacklist.pop(sid, None)

    def _pick_fanout_sids(self, key: str | None) -> list[str]:
        """Pick up to `parallel_relay` distinct non-blacklisted script IDs.

        The first ID is the stable per-host choice (same as single-shot
        routing); the rest are filled from the remaining pool. This keeps
        session-sensitive hosts pinned to one script while still racing
        extras for lower tail latency.
        """
        if self._parallel_relay <= 1 or len(self._script_ids) <= 1:
            return [self._script_id_for_key(key)]
        primary = self._script_id_for_key(key)
        picked = [primary]
        others = [s for s in self._script_ids
                  if s != primary and not self._is_sid_blacklisted(s)]
        # Round-robin-ish selection from `others`
        for sid in others:
            if len(picked) >= self._parallel_relay:
                break
            picked.append(sid)
        return picked

    @staticmethod
    def _host_key(url_or_host: str | None) -> str:
        """Return a stable routing key for a URL or host string."""
        if not url_or_host:
            return ""
        parsed = urlparse(url_or_host if "://" in url_or_host else f"https://{url_or_host}")
        host = parsed.hostname or url_or_host
        return host.lower().rstrip(".")

    # ── Per-host stats ────────────────────────────────────────────

    def _record_site(self, url: str, bytes_: int, latency_ns: int,
                     errored: bool) -> None:
        host = self._host_key(url)
        if not host:
            return
        stat = self._per_site.get(host)
        if stat is None:
            stat = HostStat()
            self._per_site[host] = stat
        stat.requests += 1
        stat.bytes += max(0, int(bytes_))
        stat.total_latency_ns += max(0, int(latency_ns))
        if errored:
            stat.errors += 1

    def stats_snapshot(self) -> dict:
        """Return a point-in-time snapshot of traffic + script health."""
        per_site = []
        for host, s in self._per_site.items():
            avg_ms = (s.total_latency_ns / s.requests / 1e6) if s.requests else 0.0
            per_site.append({
                "host": host,
                "requests": s.requests,
                "errors": s.errors,
                "bytes": s.bytes,
                "avg_ms": round(avg_ms, 1),
            })
        per_site.sort(key=lambda x: x["bytes"], reverse=True)
        now = time.time()
        blacklisted = [
            {"sid": sid[-12:] if len(sid) > 12 else sid,
             "expires_in_s": int(max(0, until - now))}
            for sid, until in self._sid_blacklist.items() if until > now
        ]
        return {
            "per_site": per_site,
            "blacklisted_scripts": blacklisted,
            "sni_rotation": list(self._sni_hosts),
            "parallel_relay": self._parallel_relay,
        }

    async def _stats_logger(self):
        """Periodically log top hosts by bytes. DEBUG-level, low overhead."""
        interval = STATS_LOG_INTERVAL
        top_n = STATS_LOG_TOP_N
        while True:
            try:
                await asyncio.sleep(interval)
                if not log.isEnabledFor(logging.DEBUG) or not self._per_site:
                    continue
                snap = self.stats_snapshot()
                top = snap["per_site"][:top_n]
                log.debug("── Per-host stats (top %d by bytes) ──", len(top))
                for row in top:
                    log.debug(
                        "  %-40s %5d req  %2d err  %8d KB  avg %7.1f ms",
                        row["host"][:40], row["requests"], row["errors"],
                        row["bytes"] // 1024, row["avg_ms"],
                    )
                if snap["blacklisted_scripts"]:
                    log.debug("  blacklisted scripts: %s",
                              ", ".join(f"{b['sid']} ({b['expires_in_s']}s)"
                                        for b in snap["blacklisted_scripts"]))
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.debug("Stats logger error: %s", e)

    def _script_id_for_key(self, key: str | None = None) -> str:
        """Pick a stable Apps Script ID for a host or fallback to round-robin.

        When multiple deployments are configured, using a stable mapping per
        host reduces IP/session churn for sites that are sensitive to endpoint
        changes. If no key is available, we keep the older round-robin fallback
        so warmup/keepalive traffic still distributes normally.

        Blacklisted IDs are skipped by probing forward in the list until a
        healthy one is found; if none, the stable pick is returned anyway.
        """
        if len(self._script_ids) == 1:
            return self._script_ids[0]
        if not key:
            return self._next_script_id()
        digest = hashlib.sha1(key.encode("utf-8")).digest()
        base = int.from_bytes(digest[:4], "big") % len(self._script_ids)
        n = len(self._script_ids)
        for offset in range(n):
            sid = self._script_ids[(base + offset) % n]
            if not self._is_sid_blacklisted(sid):
                return sid
        return self._script_ids[base]

    def _exec_path(self, url_or_host: str | None = None) -> str:
        """Get the Apps Script endpoint path (/dev or /exec)."""
        sid = self._script_id_for_key(self._host_key(url_or_host))
        return self._exec_path_for_sid(sid)

    def _exec_path_for_sid(self, sid: str) -> str:
        """Build the /macros/s/<sid>/(dev|exec) path for a specific script ID."""
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
                    alive =[]
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
        self._warm_task = self._spawn(self._do_warm())
        # Start continuous pool maintenance
        if self._maintenance_task is None:
            self._maintenance_task = self._spawn(self._pool_maintenance())
        # Periodic per-host stats logger (opt-in via log level)
        if self._stats_task is None:
            self._stats_task = self._spawn(self._stats_logger())
        # Start H2 connection (runs alongside H1 pool)
        if self._h2:
            self._spawn(self._h2_connect_and_warm())

    def _spawn(self, coro) -> asyncio.Task:
        """Create a task and keep a strong reference for clean cancellation."""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    async def close(self):
        """Cancel background tasks and close all pooled / H2 connections."""
        for task in list(self._bg_tasks):
            task.cancel()
        if self._bg_tasks:
            self._spawn(self._prewarm_script())
            if self._keepalive_task is None or self._keepalive_task.done():
                self._keepalive_task = self._spawn

        await self._flush_pool()

        if self._h2:
            try:
                await self._h2.close()
            except Exception as exc:
                log.debug("h2 close: %s", exc)

    async def _h2_connect(self):
        try:
            await self._h2.ensure_connected()
            log.info("H2 multiplexing active — one conn handles all requests")
        except Exception as e:
            log.warning("H2 connect failed (%s), using H1 pool fallback", e)

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
            t0 = time.perf_counter()
            status, _, body = await asyncio.wait_for(
                self._h2.request(method="POST", path=dev_path, host=self.http_host, headers=hdrs, body=payload),
                timeout=15,
            )
            dt = (time.perf_counter() - t0) * 1000
            data = json.loads(body.decode(errors="replace"))
            if "s" in data:
                self._dev_available = True
                log.info("/dev fast path active (%.0fms, no redirect)", dt)
                return
        except Exception as e:
            log.debug("/dev test failed: %s", e)

        try:
            exec_path = f"/macros/s/{sid}/exec"
            t0 = time.perf_counter()
            await asyncio.wait_for(
                self._h2.request(method="POST", path=exec_path, host=self.http_host, headers=hdrs, body=payload),
                timeout=15,
            )
            dt = (time.perf_counter() - t0) * 1000
            log.info("Apps Script pre-warmed in %.0fms", dt)
        except Exception as e:
            log.debug("Pre-warm failed: %s", e)

    async def _keepalive_loop(self):
        while True:
            try:
                await asyncio.sleep(240)  # 4 minutes — saves ~90 quota hits/day vs 180s
                                          # Google's container timeout is ~5 min idle
                if not self._h2 or not self._h2.is_connected:
                    try:
                        await self._h2.reconnect()
                    except Exception:
                        continue

                await self._h2.ping()
                payload = {"m": "HEAD", "u": "http://example.com/", "k": self.auth_key}
                path = self._exec_path("example.com")
                t0 = time.perf_counter()
                await asyncio.wait_for(
                    self._h2.request(method="POST", path=path, host=self.http_host, headers={"content-type": "application/json"}, body=json.dumps(payload).encode()),
                    timeout=20,
                )
                dt = (time.perf_counter() - t0) * 1000
                log.debug("Keepalive ping: %.0fms", dt)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.debug("Keepalive failed: %s", e)

    async def _do_warm(self):
        """Open WARM_POOL_COUNTnnections in parallel — failures are fine."""
        count = 30
        coros =[self._add_conn_to_pool() for _ in range(count)]
        results = await asyncio.gather(*coros, return_exceptions=True)
        opened = sum(1 for r in results if not isinstance(r, Exception))
        log.info("Pre-warmed %d/%d TLS connections", opened, count)

    def _auth_header(self) -> str:
        return f"X-Auth-Key: {self.auth_key}\r\n" if self.auth_key else ""

    # ── Apps Script relay (apps_script mode) ──────────────────────

    def _try_split_long_param(self, url: str) -> list[str] | None:
        """Find the param with the most comma-separated values and split.
        CRITICAL FIX: Never split JSON strings (starts with { or[).
        Uses a 1950 character limit.
        """
        parsed = urllib.parse.urlparse(url)
        if not parsed.query:
            return None

        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)

        valid_keys = []
        for k, v in params.items():
            if not v or "," not in v[0]:
                continue
            val = v[0].strip()
            # Do not split JSON objects or arrays!
            if val.startswith("{") or val.startswith("["):
                continue
            valid_keys.append(k)

        if not valid_keys:
            return None

        split_key = max(valid_keys, key=lambda k: len(params[k][0].split(",")))
        all_values = params[split_key][0].split(",")
        
        if len(all_values) <= 1:
            return None

        fixed_params = {k: v for k, v in params.items() if k != split_key}
        base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        fixed_qs = urllib.parse.urlencode(fixed_params, doseq=True)
        sep = "&" if fixed_qs else ""
        base_len = (len(base_url) + 1 + len(fixed_qs) + len(sep) + len(split_key) + 1)

        urls: list[str] =[]
        current_group: list[str] =[]
        current_len = base_len

        for val in all_values:
            encoded_val = urllib.parse.quote(val, safe="")
            segment_len = len(encoded_val) + 1
            if current_len + segment_len > 1950 and current_group:
                joined = urllib.parse.quote(",".join(current_group), safe=",")
                urls.append(f"{base_url}?{fixed_qs}{sep}{split_key}={joined}")
                current_group = [val]
                current_len = base_len + len(encoded_val)
            else:
                current_group.append(val)
                current_len += segment_len

        if current_group:
            joined = urllib.parse.quote(",".join(current_group), safe=",")
            urls.append(f"{base_url}?{fixed_qs}{sep}{split_key}={joined}")

        return urls if len(urls) > 1 else None

    async def _fetch_and_stitch(self, urls: list[str], headers: dict, req_origin: str = "*") -> bytes:
        """Fetch multiple URLs in parallel and safely merge their bodies."""
        async def fetch_one(u: str) -> bytes:
            payload = self._build_payload("GET", u, headers, b"")
            return await self._batch_submit(payload)

        responses = await asyncio.gather(*[fetch_one(u) for u in urls], return_exceptions=True)

        content_type = b"application/octet-stream"
        body_parts: list[bytes] =[]
        separator = b""
        is_json = False

        for i, resp in enumerate(responses):
            if isinstance(resp, Exception):
                continue
            status, hdrs, chunk_body = self._split_raw_response(resp)
            if status != 200:
                continue
            
            if not body_parts:
                ct = hdrs.get("content-type", "application/octet-stream").lower()
                content_type = ct.encode()
                if "javascript" in ct:
                    separator = b"\n;\n"
                elif "css" in ct:
                    separator = b"\n"
                elif "json" in ct:
                    is_json = True
                    
            body_parts.append(chunk_body)

        if not body_parts:
            return self._error_response(502, "All split requests failed", req_origin)

        # Merge JSON objects/arrays safely
        if is_json:
            try:
                merged_list =[]
                merged_dict = {}
                is_array = False
                for chunk in body_parts:
                    if not chunk.strip():
                        continue
                    data = json.loads(chunk)
                    if isinstance(data, list):
                        is_array = True
                        merged_list.extend(data)
                    elif isinstance(data, dict):
                        merged_dict.update(data)
                final_body = json.dumps(merged_list if is_array else merged_dict).encode()
            except Exception as e:
                log.error("Failed to merge JSON chunks: %s", e)
                final_body = b"".join(body_parts)
        else:
            final_body = separator.join(body_parts)

        return (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: " + content_type + b"\r\n"
            b"Content-Length: " + str(len(final_body)).encode() + b"\r\n"
            b"Cache-Control: public, max-age=31536000\r\n"
            b"Access-Control-Allow-Origin: " + req_origin.encode() + b"\r\n"
            b"Access-Control-Allow-Credentials: true\r\n"
            b"\r\n"
        ) + final_body

    def _convert_long_get_to_post(self, method: str, url: str, headers: dict, body: bytes) -> tuple[str, str, dict, bytes]:
        """Fallback for GET URLs exceeding 1950 chars."""
        if method != "GET" or len(url) <= 1950 or body:
            return method, url, headers, body

        parsed = urllib.parse.urlparse(url)
        if not parsed.query:
            return method, url, headers, body

        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        is_json_api = any(v[0].strip().startswith(("{", "[")) for v in params.values() if v)
        base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        new_headers = dict(headers) if headers else {}

        if is_json_api:
            body_dict: dict = {}
            for k, v in params.items():
                raw_val = v[0] if len(v) == 1 else v
                try:
                    body_dict[k] = json.loads(raw_val)
                except (json.JSONDecodeError, TypeError):
                    body_dict[k] = raw_val
            new_headers["Content-Type"] = "application/json"
            new_body = json.dumps(body_dict).encode()
        else:
            new_headers["Content-Type"] = "application/x-www-form-urlencoded"
            new_body = parsed.query.encode()

        return "POST", base_url, new_headers, new_body

    # ── Apps Script relay ─────────────────────────────────────────

    @staticmethod
    def _extract_origin(headers: dict | None) -> str:
        if not headers:
            return "*"
        for k, v in headers.items():
            if k.lower() == "origin":
                return v
        return "*"

    async def relay(self, method: str, url: str, headers: dict, body: bytes = b"") -> bytes:
        req_origin = self._extract_origin(headers)

        if method == "OPTIONS":
            req_cors_headers = "*"
            if headers:
                for k, v in headers.items():
                    if k.lower() == "access-control-request-headers":
                        req_cors_headers = v
                        break

            return (
                f"HTTP/1.1 204 No Content\r\n"
                f"Access-Control-Allow-Origin: {req_origin}\r\n"
                f"Access-Control-Allow-Credentials: true\r\n"
                f"Access-Control-Allow-Methods: GET, POST, PUT, DELETE, PATCH, OPTIONS\r\n"
                f"Access-Control-Allow-Headers: {req_cors_headers}\r\n"
                f"Access-Control-Max-Age: 86400\r\n"
                f"\r\n"
            ).encode()

        if method == "HEAD":
            status = "204 No Content" if "generate_204" in url else "200 OK"
            return (
                f"HTTP/1.1 {status}\r\n"
                f"Access-Control-Allow-Origin: {req_origin}\r\n"
                f"Access-Control-Allow-Credentials: true\r\n"
                f"Access-Control-Allow-Headers: *\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            ).encode()

        if not self._warmed:
            await self._warm_pool()

        if method == "GET" and not body and len(url) > 1950:
            split_urls = self._try_split_long_param(url)
            if split_urls:
                return await self._fetch_and_stitch(split_urls, headers, req_origin)
            method, url, headers, body = self._convert_long_get_to_post(method, url, headers, body)

        payload = self._build_payload(method, url, headers, body)
        has_range = headers and any(k.lower() == "range" for k in headers)

        t0 = time.perf_counter()
        errored = False
        result: bytes = b""
        try:
            # Stateful/browser-navigation requests should preserve exact ordering
            # and header context; batching/coalescing is reserved for static fetches.
            if self._is_stateful_request(method, url, headers, body):
                result = await self._relay_with_retry(payload)
                return result

            # Coalesce concurrent GETs for the same URL.
            # CRITICAL: do NOT coalesce when a Range header is present —
            # parallel range downloads MUST each hit the server independently.
            if method == "GET" and not body and not has_range:
                result = await self._coalesced_submit(url, payload)
                return result

            result = await self._batch_submit(payload)
            return result
        except Exception:
            errored = True
            raise
        finally:
            latency_ns = int((time.perf_counter() - t0) * 1e9)
            self._record_site(url, len(result), latency_ns, errored)

    async def _coalesced_submit(self, url: str, payload: dict) -> bytes:
        """Dedup concurrent requests for the same URL (no Range header).

        Uses `_batch_lock` to atomically check-and-append, preventing a
        race where the owning task's `finally` pops the entry between
        the check and append by a second task.
        """
        loop = asyncio.get_event_loop()
        async with self._batch_lock:
            waiters = self._coalesce.get(url)
            if waiters is not None:
                future = loop.create_future()
                waiters.append(future)
                log.debug("Coalesced request: %s", url[:60])
                waiting = True
            else:
                self._coalesce[url] = []
                waiting = False

        if waiting:
            return await future

        try:
            result = await self._batch_submit(payload)
        except Exception as e:
            async with self._batch_lock:
                waiters = self._coalesce.pop(url, [])
            for f in waiters:
                if not f.done():
                    f.set_exception(e)
            raise

        async with self._batch_lock:
            waiters = self._coalesce.pop(url, [])
        for f in waiters:
            if not f.done():
                f.set_result(result)
        return result

    async def relay_parallel(self, method: str, url: str, headers: dict, body: bytes = b"", chunk_size: int = 256 * 1024, max_parallel: int = 16) -> bytes:
        if method != "GET" or body:
            return await self.relay(method, url, headers, body)

        range_headers = dict(headers) if headers else {}
        range_headers["Range"] = f"bytes=0-{chunk_size - 1}"
        first_resp = await self.relay("GET", url, range_headers, b"")

        status, resp_hdrs, resp_body = self._split_raw_response(first_resp)

        # No range support → return the single response as-is (status 200
        # from the origin). The client sent a plain GET, so 200 is what it
        # expects.
        if status != 206:
            return first_resp

        content_range = resp_hdrs.get("content-range", "")
        m = re.search(r"/(\d+)", content_range)
        if not m:
            # Can't parse — downgrade to 200 so the client (which sent a
            # plain GET) doesn't get confused by 206 + Content-Range.
            return self._rewrite_206_to_200(first_resp)
        total_size = int(m.group(1))

        # Small file: probe already fetched it all. MUST rewrite to 200
        # because the client never sent a Range header — a stray 206 here
        # breaks fetch()/XHR on sites like x.com and Cloudflare challenges.
        if total_size <= chunk_size or len(resp_body) >= total_size:
            return self._rewrite_206_to_200(first_resp)

        ranges =[]
        start = len(resp_body)
        while start < total_size:
            end = min(start + chunk_size - 1, total_size - 1)
            ranges.append((start, end))
            start = end + 1

        sem = asyncio.Semaphore(max_parallel)

        async def fetch_range(s, e, max_tries: int = 3):
            async with sem:
                rh = dict(headers) if headers else {}
                rh["Range"] = f"bytes={s}-{e}"
                expected = e - s + 1
                last_err = None
                for attempt in range(max_tries):
                    try:
                        raw = await self.relay("GET", url, rh, b"")
                        _, _, chunk_body = self._split_raw_response(raw)
                        if len(chunk_body) == expected:
                            return chunk_body
                        last_err = f"short chunk {len(chunk_body)}/{expected} B"
                    except Exception as ex:
                        last_err = repr(ex)
                    await asyncio.sleep(0.3 * (attempt + 1))
                raise RuntimeError(f"chunk {s}-{e} failed: {last_err}")

        chunk_results = await asyncio.gather(*[fetch_range(s, e) for s, e in ranges], return_exceptions=True)

        parts =[resp_body]
        for i, r in enumerate(chunk_results):
            if isinstance(r, Exception):
                req_origin = self._extract_origin(headers)
                return self._error_response(502, f"Parallel download failed: {r}", req_origin)
            parts.append(r)

        full_body = b"".join(parts)

        skip = {"transfer-encoding", "connection", "keep-alive", "content-length", "content-encoding", "content-range"}
        result = "HTTP/1.1 200 OK\r\n"
        for k, v in resp_hdrs.items():
            if k.lower() not in skip:
                result += f"{k}: {v}\r\n"
        result += f"Content-Length: {len(full_body)}\r\n\r\n"
        return result.encode() + full_body

    @staticmethod
    def _rewrite_206_to_200(raw: bytes) -> bytes:
        """Rewrite a 206 Partial Content response to 200 OK.

        Used when we probed with a synthetic Range header but the client
        never asked for one. Handing a 206 back to the browser for a plain
        GET breaks XHR/fetch on sites like x.com and Cloudflare challenges
        (they see it as an aborted/partial response). We drop the
        Content-Range header and set Content-Length to the body size.
        """
        sep = b"\r\n\r\n"
        if sep not in raw:
            return raw
        header_section, body = raw.split(sep, 1)
        lines = header_section.decode(errors="replace").split("\r\n")
        if not lines:
            return raw
        # Replace status line
        first = lines[0]
        if " 206" in first:
            lines[0] = first.replace(" 206 Partial Content", " 200 OK")\
                             .replace(" 206", " 200 OK")
        # Drop Content-Range and recalculate Content-Length
        filtered = [lines[0]]
        for ln in lines[1:]:
            low = ln.lower()
            if low.startswith("content-range:"):
                continue
            if low.startswith("content-length:"):
                continue
            filtered.append(ln)
        filtered.append(f"Content-Length: {len(body)}")
        return ("\r\n".join(filtered) + "\r\n\r\n").encode() + body

    def _build_payload(self, method, url, headers, body):
        """Build the JSON relay payload dict."""
        payload = {
            "m": method,
            "u": url,
            # Let the browser/app see origin redirects and cookies directly.
            "r": False,
        }
        if headers:
            # Strip Accept-Encoding: Apps Script auto-decompresses gzip
            # but NOT brotli/zstd — forwarding "br" causes garbled responses.
            filt = {k: v for k, v in headers.items()
                    if k.lower() != "accept-encoding"}
            filt["accept-encoding"] = "identity"
            payload["h"] = filt if filt else headers
        if body:
            payload["b"] = base64.b64encode(body).decode()
            ct = (headers or {}).get("Content-Type") or (headers or {}).get("content-type")
            if ct:
                payload["ct"] = ct
        return payload

    @classmethod
    def _is_static_asset_url(cls, url: str) -> bool:
        path = urlparse(url).path.lower()
        return any(path.endswith(ext) for ext in cls._STATIC_EXTS)

    @staticmethod
    def _header_value(headers: dict | None, name: str) -> str:
        if not headers:
            return ""
        for key, value in headers.items():
            if key.lower() == name:
                return str(value)
        return ""

    @classmethod
    def _is_stateful_request(cls, method: str, url: str,
                             headers: dict | None, body: bytes) -> bool:
        method = method.upper()
        if method not in {"GET", "HEAD"} or body:
            return True

        if headers:
            for name in STATEFUL_HEADER_NAMES:
                if cls._header_value(headers, name):
                    return True

            accept = cls._header_value(headers, "accept").lower()
            if "text/html" in accept or "application/json" in accept:
                return True

            fetch_mode = cls._header_value(headers, "sec-fetch-mode").lower()
            if fetch_mode in {"navigate", "cors"}:
                return True

        return not cls._is_static_asset_url(url)

    # ── Batch collector ───────────────────────────────────────────

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
                self._spawn(self._batch_send(batch))
            elif self._batch_task is None or self._batch_task.done():
                # First request in a new batch window — start timer
                self._batch_task = self._spawn(self._batch_timer())

        return await future

    async def _batch_timer(self):
        await asyncio.sleep(self._batch_window_micro)
        async with self._batch_lock:
            if len(self._batch_pending) <= 1:
                if self._batch_pending:
                    batch = self._batch_pending[:]
                    self._batch_pending.clear()
                    self._batch_task = None
                    self._spawn(self._batch_send(batch))
                return

        await asyncio.sleep(self._batch_window_macro - self._batch_window_micro)
        async with self._batch_lock:
            if self._batch_pending:
                batch = self._batch_pending[:]
                self._batch_pending.clear()
                self._batch_task = None
                self._spawn(self._batch_send(batch))

    async def _batch_send(self, batch: list):
        if len(batch) == 1:
            payload, future = batch[0]
            try:
                result = await self._relay_with_retry(payload)
                if not future.done():
                    future.set_result(result)
            except Exception as e:
                if not future.done():
                    req_origin = self._extract_origin(payload.get("h", {}))
                    future.set_result(self._error_response(502, str(e), req_origin))
        else:
            try:
                results = await self._relay_batch([p for p, _ in batch])
                for (_, future), result in zip(batch, results):
                    if not future.done():
                        future.set_result(result)
            except Exception as e:
                self._batch_enabled = False
                await asyncio.gather(*[self._relay_fallback(p, f) for p, f in batch])

    async def _relay_fallback(self, payload: dict, future: asyncio.Future):
        try:
            result = await self._relay_with_retry(payload)
            if not future.done():
                future.set_result(result)
        except Exception as e:
            if not future.done():
                req_origin = self._extract_origin(payload.get("h", {}))
                future.set_result(self._error_response(502, str(e), req_origin))

    # ── Core relay ────────────────────────────────────────────────

    async def _relay_with_retry(self, payload: dict) -> bytes:
        """Single relay with one retry on failure. Uses H2 if available."""
        # Fan-out: race N Apps Script instances when enabled and H2 is up.
        # Cuts tail latency when one container is slow/cold. Only kicks in
        # if multiple script IDs are configured and the H2 transport is live.
        if (self._parallel_relay > 1
                and len(self._script_ids) > 1
                and self._h2 and self._h2.is_connected):
            try:
                return await asyncio.wait_for(
                    self._relay_fanout(payload), timeout=RELAY_TIMEOUT,
                )
            except Exception as e:
                log.debug("Fan-out relay failed (%s), falling back", e)
                # fall through to single-path logic below

        # Try HTTP/2 first — much faster (multiplexed, no pool checkout)
        if self._h2 and self._h2.is_connected:
            for attempt in range(2):
                try:
                    return await asyncio.wait_for(
                        self._relay_single_h2(payload), timeout=RELAY_TIMEOUT
                    )
                except Exception as e:
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
                    return await asyncio.wait_for(
                        self._relay_single(payload), timeout=RELAY_TIMEOUT
                    )
                except Exception as e:
                    if attempt == 0:
                        await self._flush_pool()
                    else:
                        raise

    async def _relay_fanout(self, payload: dict) -> bytes:
        """Fire the same relay against N distinct script IDs in parallel.

        Returns the first successful response; cancels the rest as soon as
        one finishes. Any script that raises or loses the race AND later
        fails individually is blacklisted for SCRIPT_BLACKLIST_TTL.
        """
        host_key = self._host_key(payload.get("u"))
        sids = self._pick_fanout_sids(host_key)
        if len(sids) <= 1:
            # Nothing to race against (e.g. all others blacklisted)
            return await self._relay_single_h2_with_sid(payload, sids[0])

        tasks = {
            asyncio.create_task(
                self._relay_single_h2_with_sid(payload, sid)
            ): sid
            for sid in sids
        }
        winner_result: bytes | None = None
        winner_exc: BaseException | None = None
        pending = set(tasks.keys())
        try:
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED,
                )
                for t in done:
                    sid = tasks[t]
                    exc = t.exception()
                    if exc is None:
                        winner_result = t.result()
                        return winner_result
                    # This racer failed — blacklist and keep waiting for others
                    self._blacklist_sid(sid, reason=type(exc).__name__)
                    winner_exc = exc
            # All racers failed
            if winner_exc is not None:
                raise winner_exc
            raise RuntimeError("fan-out relay: all racers failed")
        finally:
            for t in pending:
                t.cancel()
            # Drain cancelled tasks so they don't log warnings
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def _relay_single_h2(self, payload: dict) -> bytes:
        full_payload = dict(payload)
        full_payload["k"] = self.auth_key
        json_body = json.dumps(full_payload).encode()
        req_origin = self._extract_origin(payload.get("h", {}))

        path = self._exec_path(payload.get("u"))

        status, headers, body = await self._h2.request(
            method="POST", path=path, host=self.http_host,
            headers={"content-type": "application/json"},
            body=json_body,
        )

        return self._parse_relay_response(body, req_origin)

    async def _relay_single_h2_with_sid(self, payload: dict,
                                        sid: str) -> bytes:
        """Execute an H2 relay pinned to a specific Apps Script deployment.

        Used by `_relay_fanout` to race multiple script IDs in parallel.
        Mirrors `_relay_single_h2` but ignores the stable-hash routing.
        """
        full_payload = dict(payload)
        full_payload["k"] = self.auth_key
        json_body = json.dumps(full_payload).encode()

        path = self._exec_path_for_sid(sid)

        status, headers, body = await self._h2.request(
            method="POST", path=path, host=self.http_host,
            headers={"content-type": "application/json"},
            body=json_body,
        )

        return self._parse_relay_response(body)

    async def _relay_single(self, payload: dict) -> bytes:
        full_payload = dict(payload)
        full_payload["k"] = self.auth_key
        json_body = json.dumps(full_payload).encode()
        req_origin = self._extract_origin(payload.get("h", {}))

        path = self._exec_path(payload.get("u"))
        reader, writer, created = await self._acquire()

        try:
            request = (
                f"POST {path} HTTP/1.1\r\n"
                f"Host: {self.http_host}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(json_body)}\r\n"
                f"Accept-Encoding: gzip\r\n"
                f"Connection: keep-alive\r\n"
                f"\r\n"
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
                if status in (307, 308):
                    redirect_method = "POST"
                    redirect_body = json_body
                else:
                    redirect_method = "GET"
                    redirect_body = b""
                request_lines = [
                    f"{redirect_method} {rpath} HTTP/1.1",
                    f"Host: {parsed.netloc}",
                    "Accept-Encoding: gzip",
                    "Connection: keep-alive",
                ]
                if redirect_body:
                    request_lines.append(f"Content-Length: {len(redirect_body)}")
                request = "\r\n".join(request_lines) + "\r\n\r\n"
                writer.write(request.encode() + redirect_body)
                await writer.drain()
                status, resp_headers, resp_body = await self._read_http_response(reader)

            await self._release(reader, writer, created)
            return self._parse_relay_response(resp_body, req_origin)

        except Exception:
            try:
                writer.close()
            except Exception:
                pass
            raise

    async def _relay_batch(self, payloads: list[dict]) -> list[bytes]:
        batch_payload = {"k": self.auth_key, "q": payloads}
        json_body = json.dumps(batch_payload).encode()
        path = self._exec_path(payloads[0].get("u") if payloads else None)

        if self._h2 and self._h2.is_connected:
            try:
                status, headers, body = await asyncio.wait_for(
                    self._h2.request(
                        method="POST", path=path, host=self.http_host,
                        headers={"content-type": "application/json"},
                        body=json_body,
                    ),
                    timeout=30,
                )
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
                    f"Connection: keep-alive\r\n"
                    f"\r\n"
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
                    if status in (307, 308):
                        redirect_method = "POST"
                        redirect_body = json_body
                    else:
                        redirect_method = "GET"
                        redirect_body = b""
                    request_lines = [
                        f"{redirect_method} {rpath} HTTP/1.1",
                        f"Host: {parsed.netloc}",
                        "Accept-Encoding: gzip",
                        "Connection: keep-alive",
                    ]
                    if redirect_body:
                        request_lines.append(f"Content-Length: {len(redirect_body)}")
                    request = "\r\n".join(request_lines) + "\r\n\r\n"
                    writer.write(request.encode() + redirect_body)
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
            try:
                data = json.loads(m.group()) if m else None
            except json.JSONDecodeError:
                data = None
        if not data:
            raise RuntimeError(f"Bad batch response: {text[:200]}")
        if "e" in data:
            raise RuntimeError(f"Batch error: {data['e']}")
            
        items = data.get("q",[])
        if len(items) != len(payloads):
            raise RuntimeError(f"Batch size mismatch: {len(items)} vs {len(payloads)}")
            
        results =[]
        for item, payload in zip(items, payloads):
            req_origin = self._extract_origin(payload.get("h", {}))
            results.append(self._parse_relay_json(item, req_origin))
        return results

    # ── HTTP response reading ─────────────────────────────────────

    async def _read_http_response(self, reader: asyncio.StreamReader):
        raw = b""
        while b"\r\n\r\n" not in raw:
            if len(raw) > 65536:  # 64 KB header size limit
                return 0, {}, b""
            chunk = await asyncio.wait_for(reader.read(8192), timeout=8)
            if not chunk:
                break
            raw += chunk

        if b"\r\n\r\n" not in raw:
            return 0, {}, b""

        header_section, body = raw.split(b"\r\n\r\n", 1)
        lines = header_section.split(b"\r\n")

        m = re.search(r"\d{3}", lines[0].decode(errors="replace"))
        status = int(m.group()) if m else 0

        headers = {}
        for line in lines[1:]:
            if b":" in line:
                k, v = line.decode(errors="replace").split(":", 1)
                headers[k.strip().lower()] = v.strip()

        transfer_encoding = headers.get("transfer-encoding", "")
        content_length = headers.get("content-length")

        if "chunked" in transfer_encoding:
            body = await self._read_chunked(reader, body)
        elif content_length:
            target = int(content_length)
            if len(body) > target:
                body = body[:target]
            else:
                remaining = target - len(body)
                while remaining > 0:
                    chunk = await asyncio.wait_for(
                        reader.read(min(remaining, 65536)), timeout=20
                    )
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

        # Auto-decompress (gzip/deflate/br/zstd) from Google frontend
        enc = headers.get("content-encoding", "")
        if enc:
            body = codec.decode(body, enc)

        return status, headers, body

    async def _read_chunked(self, reader: asyncio.StreamReader, buf: bytes = b"") -> bytes:
        result = b""
        _MAX_BODY = 200 * 1024 * 1024  # 200 MB total body cap
        while True:
            while b"\r\n" not in buf:
                data = await asyncio.wait_for(reader.read(8192), timeout=20)
                if not data:
                    return result
                buf += data

            end = buf.find(b"\r\n")
            size_str = buf[:end].split(b";", 1)[0].decode(errors="replace").strip()
            buf = buf[end + 2:]

            if not size_str:
                continue
            try:
                size = int(size_str, 16)
            except ValueError:
                break
                
            if size == 0:
                while b"\r\n\r\n" not in buf and buf != b"\r\n":
                    try:
                        data = await asyncio.wait_for(reader.read(8192), timeout=2)
                        if not data:
                            break
                        buf += data
                    except asyncio.TimeoutError:
                        break
                break
            if size > _MAX_BODY or len(result) + size > _MAX_BODY:
                log.warning("Chunked body exceeds %d MB cap — truncating", _MAX_BODY // (1024 * 1024))
                break

            while len(buf) < size:
                data = await asyncio.wait_for(reader.read(65536), timeout=20)
                if not data:
                    result += buf[:size]
                    return result
                buf += data

            result += buf[:size]
            buf = buf[size:]

            while len(buf) < 2:
                data = await asyncio.wait_for(reader.read(8192), timeout=20)
                if not data:
                    return result
                buf += data
            buf = buf[2:]

        return result

    # ── Response parsing ──────────────────────────────────────────

    def _parse_relay_response(self, body: bytes, req_origin: str = "*") -> bytes:
        text = body.decode(errors="replace").strip()
        if not text:
            return self._error_response(502, "Empty response from relay", req_origin)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group())
                except json.JSONDecodeError:
                    return self._error_response(502, f"Bad JSON: {text[:200]}", req_origin)
            else:
                return self._error_response(502, f"No JSON: {text[:200]}", req_origin)
        return self._parse_relay_json(data, req_origin)

    def _parse_relay_json(self, data: dict, req_origin: str = "*") -> bytes:
        if "e" in data:
            return self._error_response(502, f"Relay error: {data['e']}", req_origin)

        status = data.get("s", 200)
        resp_headers = data.get("h", {})
        resp_body = base64.b64decode(data.get("b", ""))

        try:
            status_text = http.HTTPStatus(status).phrase
        except ValueError:
            status_text = "Unknown"

        skip = {
            "transfer-encoding", "connection", "keep-alive",
            "content-length", "content-encoding",
            "access-control-allow-origin", "access-control-allow-credentials",
        }
        result = f"HTTP/1.1 {status} {status_text}\r\n"
        for k, v in resp_headers.items():
            if k.lower() in skip:
                continue
            # Apps Script returns multi-valued headers (e.g. Set-Cookie) as a
            # JavaScript array. Emit each value as its own header line.
            # A single string that holds multiple Set-Cookie values joined
            # with ", " also needs to be split, otherwise the browser sees
            # one malformed cookie and sites like x.com fail.
            values = v if isinstance(v, list) else [v]
            if k.lower() == "set-cookie":
                expanded = []
                for item in values:
                    expanded.extend(self._split_set_cookie(str(item)))
                values = expanded
            for val in values:
                result += f"{k}: {val}\r\n"

        result += f"Access-Control-Allow-Origin: {req_origin}\r\n"
        result += f"Access-Control-Allow-Credentials: true\r\n"
        result += f"Content-Length: {len(resp_body)}\r\n"
        result += "\r\n"
        return result.encode() + resp_body

    @staticmethod
    def _split_set_cookie(blob: str) -> list[str]:
        """Split a Set-Cookie string that may contain multiple cookies.

        Apps Script sometimes joins multiple Set-Cookie values with ", ",
        which collides with the comma that legitimately appears inside the
        `Expires` attribute (e.g. "Expires=Wed, 21 Oct 2026 ..."). We split
        only on commas that are immediately followed by a cookie name=value
        pair (token '=' ...), leaving date commas intact.
        """
        if not blob:
            return []
        # Split on ", " but only when the following text looks like the start
        # of a new cookie (a token followed by '=').
        parts = re.split(r",\s*(?=[A-Za-z0-9!#$%&'*+\-.^_`|~]+=)", blob)
        return [p.strip() for p in parts if p.strip()]

    def _split_raw_response(self, raw: bytes):
        """Split a raw HTTP response into (status, headers_dict, body)."""
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

    def _error_response(self, status: int, message: str, req_origin: str = "*") -> bytes:
        body = f"<html><body><h1>{status}</h1><p>{message}</p></body></html>"
        try:
            status_text = http.HTTPStatus(status).phrase
        except ValueError:
            status_text = "Error"
        return (
            f"HTTP/1.1 {status} {status_text}\r\n"
            f"Content-Type: text/html\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Access-Control-Allow-Origin: {req_origin}\r\n"
            f"Access-Control-Allow-Credentials: true\r\n"
            f"\r\n"
            f"{body}"
        ).encode()