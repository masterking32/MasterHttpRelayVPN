"""
Transport protocol & connection benchmark suite.

Tests run against Google's edge IP with SNI fronting.  Four suites:

  1. Protocol sequential  — H1.1 / H2 / H3, one request at a time (apples-to-apples latency)
  2. TLS session resumption — cold connect vs warm reconnect using cached session ticket
  3. Concurrency  — H2 multiplex (N streams on 1 conn) vs H1.1 parallel (N separate conns)
  4. IP scan  — probe all candidate Google IPs to find the fastest one on this network

Usage:
    python scripts/benchmark_transport.py                       # reads config.json
    python scripts/benchmark_transport.py --ip 216.239.38.120 --sni www.google.com
    python scripts/benchmark_transport.py --suite protocol      # only run suite 1
    python scripts/benchmark_transport.py --suite resumption
    python scripts/benchmark_transport.py --suite concurrency
    python scripts/benchmark_transport.py --suite ipscan
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import ssl
import statistics
import sys
import time
from pathlib import Path

# ── Optional imports ──────────────────────────────────────────────────────

try:
    import h2.connection
    import h2.config
    import h2.events
    import h2.settings
    H2_AVAILABLE = True
except ImportError:
    H2_AVAILABLE = False

try:
    import certifi
    _CAFILE = certifi.where()
except ImportError:
    _CAFILE = None

try:
    import aioquic.asyncio as quic_asyncio
    import aioquic.h3.connection as h3c
    import aioquic.h3.events as h3e
    import aioquic.quic.configuration as quic_cfg
    import aioquic.quic.events as quic_events
    H3_AVAILABLE = True
except ImportError:
    H3_AVAILABLE = False


# ── TLS context helpers ───────────────────────────────────────────────────

def _make_tls_ctx(alpn: list[str]) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if _CAFILE:
        try:
            ctx.load_verify_locations(cafile=_CAFILE)
        except Exception:
            pass
    ctx.set_alpn_protocols(alpn)
    return ctx


# ── HTTP/1.1 probe ────────────────────────────────────────────────────────

async def _probe_h1(host_ip: str, sni: str, path: str, timeout: float) -> float:
    """Return elapsed seconds for one H1.1 GET. Raises on error."""
    ctx = _make_tls_ctx(["http/1.1"])
    raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    raw.setblocking(False)

    t0 = time.perf_counter()
    loop = asyncio.get_running_loop()
    await asyncio.wait_for(loop.sock_connect(raw, (host_ip, 443)), timeout=timeout)
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(ssl=ctx, server_hostname=sni, sock=raw),
        timeout=timeout,
    )

    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {sni}\r\n"
        "Accept: */*\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode()
    writer.write(req)
    await asyncio.wait_for(writer.drain(), timeout=timeout)

    resp = b""
    while True:
        chunk = await asyncio.wait_for(reader.read(4096), timeout=timeout)
        if not chunk:
            break
        resp += chunk
        if b"\r\n\r\n" in resp:
            break
    writer.close()
    elapsed = time.perf_counter() - t0
    if not resp.startswith(b"HTTP/"):
        raise RuntimeError(f"Unexpected response: {resp[:60]!r}")
    return elapsed


# ── HTTP/2 probe ──────────────────────────────────────────────────────────

async def _probe_h2_fresh(host_ip: str, sni: str, path: str, timeout: float) -> float:
    """One H2 GET on a NEW connection each time (apples-to-apples vs H1)."""
    if not H2_AVAILABLE:
        raise RuntimeError("h2 not installed")

    ctx = _make_tls_ctx(["h2", "http/1.1"])
    raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    raw.setblocking(False)

    t0 = time.perf_counter()
    loop = asyncio.get_running_loop()
    await asyncio.wait_for(loop.sock_connect(raw, (host_ip, 443)), timeout=timeout)
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(ssl=ctx, server_hostname=sni, sock=raw),
        timeout=timeout,
    )

    ssl_obj = writer.get_extra_info("ssl_object")
    negotiated = ssl_obj.selected_alpn_protocol() if ssl_obj else None
    if negotiated != "h2":
        writer.close()
        raise RuntimeError(f"H2 ALPN failed (got {negotiated!r})")

    cfg = h2.config.H2Configuration(client_side=True, header_encoding="utf-8")
    conn = h2.connection.H2Connection(cfg)
    conn.initiate_connection()
    writer.write(conn.data_to_send(65535))
    await writer.drain()

    stream_id = conn.get_next_available_stream_id()
    conn.send_headers(stream_id, [
        (":method", "GET"),
        (":path", path),
        (":scheme", "https"),
        (":authority", sni),
        ("accept", "*/*"),
    ], end_stream=True)
    writer.write(conn.data_to_send(65535))
    await asyncio.wait_for(writer.drain(), timeout=timeout)

    headers_done = False
    while not headers_done:
        raw_data = await asyncio.wait_for(reader.read(65535), timeout=timeout)
        if not raw_data:
            break
        events = conn.receive_data(raw_data)
        writer.write(conn.data_to_send(65535))
        await writer.drain()
        for ev in events:
            if isinstance(ev, (h2.events.ResponseReceived, h2.events.StreamEnded,
                                h2.events.DataReceived)):
                if isinstance(ev, h2.events.ResponseReceived) and ev.stream_id == stream_id:
                    headers_done = True

    writer.close()
    return time.perf_counter() - t0


# ── HTTP/3 (QUIC) probe ───────────────────────────────────────────────────

class _H3ProbeProtocol(quic_asyncio.QuicConnectionProtocol):
    """Minimal aioquic protocol that sends one H3 GET and captures the result."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._h3: h3c.H3Connection | None = None
        self._done: asyncio.Future[float] = asyncio.get_event_loop().create_future()
        self._t0: float = time.perf_counter()
        self._stream_id: int | None = None

    def quic_event_received(self, event):
        if isinstance(event, quic_events.HandshakeCompleted):
            self._h3 = h3c.H3Connection(self._quic, enable_webtransport=False)
        if self._h3 is None:
            return
        for h3ev in self._h3.handle_event(event):
            if isinstance(h3ev, h3e.HeadersReceived):
                if not self._done.done():
                    self._done.set_result(time.perf_counter() - self._t0)
            elif isinstance(h3ev, h3e.DataReceived):
                pass  # don't need body

    def send_request(self, sni: str, path: str):
        self._stream_id = self._quic.get_next_available_stream_id()
        self._h3.send_headers(
            stream_id=self._stream_id,
            headers=[
                (b":method", b"GET"),
                (b":path", path.encode()),
                (b":scheme", b"https"),
                (b":authority", sni.encode()),
                (b"accept", b"*/*"),
            ],
            end_stream=True,
        )
        self.transmit()


async def _h3_inner(host_ip: str, sni: str, path: str, timeout: float) -> float:
    cfg = quic_cfg.QuicConfiguration(
        is_client=True,
        server_name=sni,
        alpn_protocols=h3c.H3_ALPN,
        verify_mode=ssl.CERT_REQUIRED,
    )
    if _CAFILE:
        try:
            cfg.load_verify_locations(_CAFILE)
        except Exception:
            pass

    t0 = time.perf_counter()
    async with quic_asyncio.connect(
        host_ip,
        443,
        configuration=cfg,
        create_protocol=_H3ProbeProtocol,
    ) as proto:
        proto._t0 = t0
        proto.send_request(sni, path)
        return await proto._done


async def _probe_h3(host_ip: str, sni: str, path: str, timeout: float) -> float:
    if not H3_AVAILABLE:
        raise RuntimeError("aioquic not installed")

    # QUIC uses UDP. Wrap the ENTIRE connect+request in wait_for so a
    # network that silently drops UDP packets doesn't stall indefinitely.
    h3_timeout = min(timeout, 5.0)
    try:
        return await asyncio.wait_for(_h3_inner(host_ip, sni, path, h3_timeout), timeout=h3_timeout)
    except asyncio.TimeoutError:
        raise TimeoutError(f"QUIC/UDP timed out after {h3_timeout:.1f}s — UDP likely blocked or no H3 support")
    except Exception as exc:
        raise RuntimeError(f"{type(exc).__name__}: {exc or 'no detail'}")


# ── Runner ────────────────────────────────────────────────────────────────

async def _run_protocol(
    name: str,
    probe,
    host_ip: str,
    sni: str,
    path: str,
    n: int,
    timeout: float,
) -> dict:
    times: list[float] = []
    errors = 0
    for i in range(n):
        try:
            t = await probe(host_ip, sni, path, timeout)
            times.append(t)
        except Exception as exc:
            errors += 1
            desc = str(exc) or type(exc).__name__
            print(f"  [{name}] request {i+1}/{n} FAILED: {desc}")
            # If the first 3 all failed, give up early to avoid wasting time.
            if errors >= 3 and not times:
                print(f"  [{name}] 3 consecutive failures with no success — aborting protocol test")
                break
        await asyncio.sleep(0.05)  # tiny gap between probes

    return {"name": name, "times": times, "errors": errors, "n": n}


def _print_result(r: dict):
    name = r["name"]
    times = r["times"]
    errors = r["errors"]
    n = r["n"]
    ok = len(times)

    if not times:
        print(f"  {name:10s}  NO SUCCESSFUL REQUESTS  (errors={errors}/{n})")
        return

    mn  = min(times) * 1000
    mx  = max(times) * 1000
    avg = statistics.mean(times) * 1000
    med = statistics.median(times) * 1000
    p95 = sorted(times)[int(len(times) * 0.95)] * 1000

    print(
        f"  {name:10s}  "
        f"ok={ok}/{n}  "
        f"min={mn:6.1f}ms  "
        f"avg={avg:6.1f}ms  "
        f"med={med:6.1f}ms  "
        f"p95={p95:6.1f}ms  "
        f"max={mx:6.1f}ms  "
        f"errors={errors}"
    )


async def main(host_ip: str, sni: str, path: str, n: int, timeout: float,
               suite: str = "all"):
    print(f"\nBenchmark target  →  {host_ip}:443  SNI={sni}  path={path}")
    print("=" * 80)

    run_all  = suite == "all"

    # ── Suite 1: Protocol sequential ──────────────────────────────────────
    if run_all or suite == "protocol":
        print("\n── Suite 1: Protocol sequential latency ──────────────────────────────")
        print(f"   {n} sequential requests per protocol\n")

        protocols: list[tuple[str, object]] = [("HTTP/1.1", _probe_h1)]
        if H2_AVAILABLE:
            protocols.append(("HTTP/2", _probe_h2_fresh))
        else:
            print("  [HTTP/2]  skipped — pip install h2")
        if H3_AVAILABLE:
            protocols.append(("HTTP/3", _probe_h3))
        else:
            print("  [HTTP/3]  skipped — pip install aioquic")

        results = []
        for name, probe in protocols:
            print(f"  Running {name}...")
            r = await _run_protocol(name, probe, host_ip, sni, path, n, timeout)
            results.append(r)

        print()
        for r in results:
            _print_result(r)

        valid = [r for r in results if r["times"]]
        if len(valid) > 1:
            best = min(valid, key=lambda r: statistics.median(r["times"]))
            print(f"\n  Best median: {best['name']}")
            h1r = next((r for r in valid if r["name"] == "HTTP/1.1"), None)
            h2r = next((r for r in valid if r["name"] == "HTTP/2"), None)
            h3r = next((r for r in valid if r["name"] == "HTTP/3"), None)
            if h2r and h1r:
                g = (statistics.median(h1r["times"]) - statistics.median(h2r["times"])) \
                    / statistics.median(h1r["times"]) * 100
                print(f"  H2 vs H1.1: {g:+.1f}%")
            if h3r and h2r:
                g = (statistics.median(h2r["times"]) - statistics.median(h3r["times"])) \
                    / statistics.median(h2r["times"]) * 100
                print(f"  H3 vs H2:   {g:+.1f}%")

    # ── Suite 2: TLS session resumption ───────────────────────────────────
    if run_all or suite == "resumption":
        print("\n── Suite 2: TLS session resumption ───────────────────────────────────")
        print("   Measures cost of cold TLS handshake vs warm reconnect with session ticket\n")
        await _suite_resumption(host_ip, sni, path, timeout, rounds=8)

    # ── Suite 3: Concurrency ──────────────────────────────────────────────
    if run_all or suite == "concurrency":
        print("\n── Suite 3: Concurrency — H2 multiplex vs H1.1 parallel ─────────────")
        print(f"   {n} concurrent requests fired simultaneously\n")
        await _suite_concurrency(host_ip, sni, path, timeout, n=n)

    # ── Suite 4: IP scan ──────────────────────────────────────────────────
    if run_all or suite == "ipscan":
        print("\n── Suite 4: Google edge IP latency scan ──────────────────────────────")
        print("   H1.1 probe to all candidate IPs — find the fastest one on this network\n")
        await _suite_ipscan(sni, path, timeout)

    print("\n" + "=" * 80)
    print("Done.")


# ── Suite 2: TLS session resumption ──────────────────────────────────────

async def _tls_connect_time(host_ip: str, sni: str, timeout: float,
                             ctx: ssl.SSLContext | None = None) -> tuple[float, ssl.SSLContext]:
    """Connect with TLS and return (elapsed, ctx). ctx is reused for warm tests."""
    if ctx is None:
        ctx = _make_tls_ctx(["h2", "http/1.1"])

    raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    raw.setblocking(False)

    loop = asyncio.get_running_loop()
    t0 = time.perf_counter()
    await asyncio.wait_for(loop.sock_connect(raw, (host_ip, 443)), timeout=timeout)
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(ssl=ctx, server_hostname=sni, sock=raw),
        timeout=timeout,
    )
    elapsed = time.perf_counter() - t0
    # Send minimal request so the server doesn't RST the idle connection
    writer.write(f"GET /generate_204 HTTP/1.1\r\nHost: {sni}\r\nConnection: close\r\n\r\n".encode())
    await asyncio.wait_for(writer.drain(), timeout=timeout)
    try:
        await asyncio.wait_for(reader.read(256), timeout=timeout)
    except Exception:
        pass
    writer.close()
    return elapsed, ctx


async def _suite_resumption(host_ip: str, sni: str, path: str,
                              timeout: float, rounds: int):
    cold_times: list[float] = []
    warm_times: list[float] = []

    # cold: fresh SSLContext each time — no session ticket reuse
    print("  Cold connects (new TLS context each time)...")
    for _ in range(rounds):
        try:
            t, _ = await _tls_connect_time(host_ip, sni, timeout, ctx=None)
            cold_times.append(t * 1000)
        except Exception as exc:
            print(f"    FAILED: {exc}")
        await asyncio.sleep(0.1)

    # warm: reuse same SSLContext — OpenSSL caches and reuses TLS 1.3 session ticket
    print("  Warm reconnects (same TLS context, session ticket reuse)...")
    warm_ctx = _make_tls_ctx(["h2", "http/1.1"])
    for _ in range(rounds):
        try:
            t, warm_ctx = await _tls_connect_time(host_ip, sni, timeout, ctx=warm_ctx)
            warm_times.append(t * 1000)
        except Exception as exc:
            print(f"    FAILED: {exc}")
        await asyncio.sleep(0.1)

    def _fmt(times: list[float]) -> str:
        if not times:
            return "no data"
        return (f"min={min(times):.1f}ms  avg={statistics.mean(times):.1f}ms  "
                f"med={statistics.median(times):.1f}ms  max={max(times):.1f}ms")

    print(f"\n  Cold  ({len(cold_times)}/{rounds} ok): {_fmt(cold_times)}")
    print(f"  Warm  ({len(warm_times)}/{rounds} ok): {_fmt(warm_times)}")

    if cold_times and warm_times:
        saving = statistics.median(cold_times) - statistics.median(warm_times)
        pct = saving / statistics.median(cold_times) * 100
        if saving > 5:
            print(f"\n  Session ticket saves ~{saving:.1f}ms ({pct:.1f}%) per reconnect")
            print("  → The H2 transport already reuses one long-lived connection, so this")
            print("    saving only applies when the connection drops and must reconnect.")
        else:
            print(f"\n  Resumption saving: {saving:.1f}ms ({pct:.1f}%) — negligible on this network")
            print("  → Google may be issuing short-lived tickets, or RTT already dominates.")


# ── Suite 3: Concurrency ──────────────────────────────────────────────────

async def _h2_concurrent(host_ip: str, sni: str, path: str,
                          timeout: float, n: int) -> tuple[float, int]:
    """
    Fire N H2 streams concurrently on ONE persistent connection.
    Returns (wall_time_for_all, successful_count).
    """
    if not H2_AVAILABLE:
        raise RuntimeError("h2 not installed")

    ctx = _make_tls_ctx(["h2", "http/1.1"])
    raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    raw.setblocking(False)
    loop = asyncio.get_running_loop()
    await asyncio.wait_for(loop.sock_connect(raw, (host_ip, 443)), timeout=timeout)
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(ssl=ctx, server_hostname=sni, sock=raw),
        timeout=timeout,
    )
    ssl_obj = writer.get_extra_info("ssl_object")
    if not ssl_obj or ssl_obj.selected_alpn_protocol() != "h2":
        writer.close()
        raise RuntimeError("H2 ALPN not negotiated")

    cfg = h2.config.H2Configuration(client_side=True, header_encoding="utf-8")
    conn = h2.connection.H2Connection(cfg)
    conn.initiate_connection()
    conn.increment_flow_control_window(2 ** 24 - 65535)
    conn.update_settings({
        h2.settings.SettingCodes.INITIAL_WINDOW_SIZE: 8 * 1024 * 1024,
        h2.settings.SettingCodes.ENABLE_PUSH: 0,
    })
    writer.write(conn.data_to_send(65535))
    await writer.drain()

    # Track per-stream completion
    stream_done: dict[int, asyncio.Event] = {}
    stream_ids = []
    for _ in range(n):
        sid = conn.get_next_available_stream_id()
        conn.send_headers(sid, [
            (":method", "GET"), (":path", path),
            (":scheme", "https"), (":authority", sni), ("accept", "*/*"),
        ], end_stream=True)
        stream_ids.append(sid)
        stream_done[sid] = asyncio.Event()

    writer.write(conn.data_to_send(65535))
    await writer.drain()

    t0 = time.perf_counter()
    done_count = 0
    deadline = t0 + timeout

    while done_count < n and time.perf_counter() < deadline:
        try:
            raw_data = await asyncio.wait_for(
                reader.read(65535),
                timeout=max(0.1, deadline - time.perf_counter()),
            )
        except asyncio.TimeoutError:
            break
        if not raw_data:
            break
        events = conn.receive_data(raw_data)
        writer.write(conn.data_to_send(65535))
        await writer.drain()
        for ev in events:
            if isinstance(ev, (h2.events.ResponseReceived, h2.events.StreamEnded)):
                sid = ev.stream_id
                if sid in stream_done and not stream_done[sid].is_set():
                    if isinstance(ev, h2.events.ResponseReceived):
                        stream_done[sid].set()
                        done_count += 1
            elif isinstance(ev, h2.events.DataReceived):
                conn.acknowledge_received_data(ev.flow_controlled_length, ev.stream_id)
                writer.write(conn.data_to_send(65535))
                await writer.drain()

    wall = time.perf_counter() - t0
    writer.close()
    return wall, done_count


async def _h1_parallel(host_ip: str, sni: str, path: str,
                        timeout: float, n: int) -> tuple[float, int]:
    """Fire N H1.1 requests in parallel, each on its own TCP+TLS connection."""
    t0 = time.perf_counter()
    tasks = [asyncio.create_task(_probe_h1(host_ip, sni, path, timeout)) for _ in range(n)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    wall = time.perf_counter() - t0
    ok = sum(1 for r in results if isinstance(r, float))
    return wall, ok


async def _suite_concurrency(host_ip: str, sni: str, path: str,
                               timeout: float, n: int):
    concur_levels = sorted({4, 8, min(16, n), min(n, 20)})

    print(f"  {'Level':>5}  {'H2 mux wall':>14}  {'H1.1 parallel wall':>18}  {'speedup':>8}")
    print(f"  {'-----':>5}  {'----------':>14}  {'----------------':>18}  {'-------':>8}")

    for level in concur_levels:
        h2_wall = h2_ok = h1_wall = h1_ok = None
        h2_err = h1_err = None

        if H2_AVAILABLE:
            try:
                h2_wall, h2_ok = await _h2_concurrent(host_ip, sni, path, timeout, level)
            except Exception as exc:
                h2_err = str(exc) or type(exc).__name__

        try:
            h1_wall, h1_ok = await _h1_parallel(host_ip, sni, path, timeout, level)
        except Exception as exc:
            h1_err = str(exc) or type(exc).__name__

        h2_str = f"{h2_wall*1000:6.0f}ms ({h2_ok}/{level})" if h2_wall is not None else f"FAIL: {h2_err}"
        h1_str = f"{h1_wall*1000:6.0f}ms ({h1_ok}/{level})" if h1_wall is not None else f"FAIL: {h1_err}"

        if h2_wall and h1_wall and h1_wall > 0:
            speedup = f"{h1_wall / h2_wall:+.2f}x"
        else:
            speedup = "n/a"

        print(f"  {level:>5}  {h2_str:>14}  {h1_str:>18}  {speedup:>8}")
        await asyncio.sleep(0.2)

    print()
    print("  Interpretation:")
    print("  - H2 mux fires all streams on ONE TLS connection — lower overhead at scale")
    print("  - H1.1 parallel opens N separate connections — higher per-connection TLS cost")
    print("  - Speedup > 1.0x means H2 mux completed all requests in less wall time")


# ── Suite 4: IP scan ──────────────────────────────────────────────────────

_CANDIDATE_IPS = (
    "216.239.32.120", "216.239.34.120", "216.239.36.120", "216.239.38.120",
    "142.250.80.142", "142.250.80.138", "142.250.179.110", "142.250.185.110",
    "142.250.184.206", "142.250.190.238", "142.250.191.78",
    "172.217.1.206",  "172.217.14.206", "172.217.16.142",  "172.217.22.174",
    "172.217.164.110","172.217.168.206","172.217.169.206",
    "34.107.221.82",
    "142.251.32.110", "142.251.33.110", "142.251.46.206",  "142.251.46.238",
    "142.250.80.170", "142.250.72.206", "142.250.64.206",  "142.250.72.110",
)


async def _probe_ip(ip: str, sni: str, path: str, timeout: float) -> tuple[str, float | None, str]:
    """Return (ip, median_ms_or_None, note)."""
    times = []
    for _ in range(3):
        try:
            t = await _probe_h1(ip, sni, path, timeout)
            times.append(t * 1000)
        except Exception:
            pass
        await asyncio.sleep(0.03)
    if not times:
        return ip, None, "unreachable"
    med = statistics.median(times)
    return ip, med, ""


async def _suite_ipscan(sni: str, path: str, timeout: float):
    ip_timeout = min(timeout, 5.0)
    print(f"  Probing {len(_CANDIDATE_IPS)} candidate IPs (3 requests each, {ip_timeout:.0f}s cap)...\n")

    # Run all probes concurrently — they're independent H1.1 connects
    tasks = [asyncio.create_task(_probe_ip(ip, sni, path, ip_timeout))
             for ip in _CANDIDATE_IPS]
    raw_results = await asyncio.gather(*tasks)

    reachable = [(ip, med, note) for ip, med, note in raw_results if med is not None]
    dead      = [(ip, med, note) for ip, med, note in raw_results if med is None]

    reachable.sort(key=lambda x: x[1])

    print(f"  {'IP':>18}  {'median':>9}  note")
    print(f"  {'--':>18}  {'------':>9}  ----")
    for i, (ip, med, _) in enumerate(reachable):
        tag = "  ← fastest" if i == 0 else ("  ← 2nd" if i == 1 else "")
        print(f"  {ip:>18}  {med:7.1f}ms{tag}")

    if dead:
        print(f"\n  Unreachable ({len(dead)}): {', '.join(ip for ip, *_ in dead)}")

    if reachable:
        best_ip, best_med, _ = reachable[0]
        print(f"\n  Fastest IP: {best_ip}  (median {best_med:.1f}ms)")
        print(f'  Set in config.json:  "google_ip": "{best_ip}"')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Transport benchmark suite")
    parser.add_argument("--ip", help="Google edge IP (default: from config.json)")
    parser.add_argument("--sni", default="www.google.com", help="SNI hostname")
    parser.add_argument("--path", default="/generate_204", help="Request path")
    parser.add_argument("--n", type=int, default=15, help="Requests per protocol")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout (s)")
    parser.add_argument(
        "--suite",
        choices=["all", "protocol", "resumption", "concurrency", "ipscan"],
        default="all",
        help="Which benchmark suite to run (default: all)",
    )
    args = parser.parse_args()

    host_ip = args.ip
    if not host_ip:
        cfg_path = Path(__file__).parent.parent / "config.json"
        if cfg_path.exists():
            with open(cfg_path) as f:
                data = json.load(f)
            host_ip = data.get("google_ip", "216.239.38.120")
            print(f"Using google_ip from config.json: {host_ip}")
        else:
            host_ip = "216.239.38.120"
            print(f"config.json not found, using default: {host_ip}")

    asyncio.run(main(
        host_ip=host_ip,
        sni=args.sni,
        path=args.path,
        n=args.n,
        timeout=args.timeout,
        suite=args.suite,
    ))
