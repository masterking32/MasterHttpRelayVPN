"""
Persistent smart-routing decision cache.

Tracks per-host routing decisions (direct / relay) with TTLs and
persists them to disk so routing knowledge survives restarts.

Flow for an unknown domain at port 443/80:
  1. Try a fast TCP probe (PROBE_TIMEOUT seconds).
  2. Reachable  → mark "direct", serve via direct tunnel.
  3. Unreachable → mark "relay",  serve via relay.
  4. After RELAY_TTL the entry expires; a background probe fires on
     the next request so the live request is never stalled.
"""

import asyncio
import json
import logging
import socket
import time
from pathlib import Path
from typing import Literal

log = logging.getLogger("RouteCache")

RouteDecision = Literal["direct", "relay", "unknown"]

# ── Tunables ──────────────────────────────────────────────────────────────────
DIRECT_TTL    = 7_200   # 2 h  — how long to trust a working direct route
RELAY_TTL     = 1_800   # 30 m — how long before re-probing a blocked host
PROBE_TIMEOUT = 3.0     # s    — TCP connect timeout for probes
SAVE_INTERVAL = 60      # s    — background auto-save interval
MAX_ENTRIES   = 10_000  # hard cap to prevent unbounded memory growth


class RouteCache:
    """Thread-safe (asyncio) route decision cache with disk persistence."""

    def __init__(self, cache_file: str) -> None:
        self._file    = Path(cache_file)
        # {host: {"route": "direct"|"relay", "until": float}}
        self._cache: dict[str, dict] = {}
        self._probing: set[str] = set()   # hosts with an in-flight background probe
        self._dirty   = False
        self._load()

    # ── Public read/write API ─────────────────────────────────────────────────

    def get(self, host: str) -> RouteDecision:
        """Return the cached decision for *host*, or ``'unknown'``.

        Expired *relay* entries intentionally return ``'relay'`` instead of
        ``'unknown'``.  This lets _smart_tunnel detect expiry via
        is_relay_expired() and fire a background probe without stalling the
        live request.  Expired *direct* entries are removed immediately and
        return ``'unknown'`` so the next request re-probes synchronously
        (direct working again is the happy path — one stall is acceptable).
        """
        h     = _norm(host)
        entry = self._cache.get(h)
        if not entry:
            return "unknown"
        if entry["until"] < time.time():
            if entry["route"] == "relay":
                # Keep in cache so is_relay_expired() can detect this and
                # schedule a background probe rather than stalling the caller.
                return "relay"
            del self._cache[h]
            self._dirty = True
            return "unknown"
        return entry["route"]  # type: ignore[return-value]

    def set_direct(self, host: str) -> None:
        self._set(_norm(host), "direct", DIRECT_TTL)
        log.debug("RouteCache: %s → direct (TTL %ds)", host, DIRECT_TTL)

    def set_relay(self, host: str) -> None:
        self._set(_norm(host), "relay", RELAY_TTL)
        log.debug("RouteCache: %s → relay (TTL %ds)", host, RELAY_TTL)

    def downgrade(self, host: str) -> None:
        """Direct route failed at runtime — switch immediately to relay."""
        log.info("RouteCache: %s direct→relay (runtime failure)", host)
        self.set_relay(host)

    def is_relay_expired(self, host: str) -> bool:
        """True when a relay entry's TTL has lapsed (time to re-probe)."""
        h     = _norm(host)
        entry = self._cache.get(h)
        if not entry or entry["route"] != "relay":
            return False
        return entry["until"] < time.time()

    def is_probing(self, host: str) -> bool:
        return _norm(host) in self._probing

    # ── Background probe ─────────────────────────────────────────────────────

    def schedule_probe(self, host: str, port: int) -> None:
        """Fire a background TCP probe without blocking the caller."""
        h = _norm(host)
        if h in self._probing:
            return
        asyncio.ensure_future(self._probe_task(host, port))

    async def _probe_task(self, host: str, port: int) -> None:
        h = _norm(host)
        self._probing.add(h)
        try:
            loop = asyncio.get_running_loop()
            await asyncio.wait_for(
                loop.run_in_executor(None, _tcp_probe, host, port),
                timeout=PROBE_TIMEOUT + 1,
            )
            log.info("RouteCache probe: %s:%d reachable → upgrading to direct", host, port)
            self.set_direct(host)
        except Exception:
            log.debug("RouteCache probe: %s:%d still unreachable → relay TTL refreshed", host, port)
            self.set_relay(host)   # refresh TTL so we don't probe every request
        finally:
            self._probing.discard(h)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def run_autosave(self) -> None:
        """Coroutine that saves the cache to disk every SAVE_INTERVAL seconds."""
        while True:
            await asyncio.sleep(SAVE_INTERVAL)
            if self._dirty:
                self._save()

    def save(self) -> None:
        """Flush to disk immediately (call on shutdown)."""
        if self._dirty:
            self._save()

    def stats(self) -> dict:
        now = time.time()
        direct = sum(1 for e in self._cache.values() if e["route"] == "direct" and e["until"] > now)
        relay  = sum(1 for e in self._cache.values() if e["route"] == "relay"  and e["until"] > now)
        return {"direct": direct, "relay": relay, "probing": len(self._probing)}

    # ── Internals ─────────────────────────────────────────────────────────────

    def _set(self, h: str, route: str, ttl: int) -> None:
        if h not in self._cache and len(self._cache) >= MAX_ENTRIES:
            # Evict the entry with the shortest remaining TTL
            oldest = min(self._cache, key=lambda k: self._cache[k]["until"])
            del self._cache[oldest]
        self._cache[h] = {"route": route, "until": time.time() + ttl}
        self._dirty = True

    def _load(self) -> None:
        if not self._file.exists():
            return
        try:
            data  = json.loads(self._file.read_text(encoding="utf-8"))
            now   = time.time()
            count = 0
            for host, entry in data.items():
                if isinstance(entry, dict) and entry.get("until", 0) > now:
                    self._cache[host] = entry
                    count += 1
            if count:
                s = self.stats()
                log.info(
                    "RouteCache: loaded %d entries (%d direct, %d relay) from %s",
                    count, s["direct"], s["relay"], self._file,
                )
        except Exception as exc:
            log.warning("RouteCache: could not load %s: %s", self._file, exc)

    def _save(self) -> None:
        try:
            # Prune expired entries before saving
            now      = time.time()
            pruned   = {h: e for h, e in self._cache.items() if e["until"] > now}
            self._cache = pruned
            self._file.write_text(
                json.dumps(pruned, separators=(",", ":")),
                encoding="utf-8",
            )
            self._dirty = False
            log.debug("RouteCache: saved %d entries to %s", len(pruned), self._file)
        except Exception as exc:
            log.warning("RouteCache: failed to save: %s", exc)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm(host: str) -> str:
    return host.lower().rstrip(".")


def _tcp_probe(host: str, port: int) -> None:
    """Blocking TCP connect — runs inside a thread executor."""
    s = socket.create_connection((host, port), timeout=PROBE_TIMEOUT)
    s.close()
