"""
Automatic ad/tracker domain blocker.

Downloads and caches blocklists from well-known sources (StevenBlack hosts,
AdGuard DNS filter). Parsed results are stored locally so subsequent startups
are instant; the cache refreshes automatically after update_interval_hours.
"""

import asyncio
import json
import logging
import re
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("AdBlock")

# ── Known blocklist sources ───────────────────────────────────────────────────

SOURCES: dict[str, str] = {
    "stevenblack": (
        "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts"
    ),
    "adguard": (
        "https://adguardteam.github.io/AdGuardSDNSFilter/Filters/filter.txt"
    ),
}

DEFAULT_SOURCES       = ["stevenblack", "adguard"]
DEFAULT_UPDATE_HOURS  = 168   # 1 week
DOWNLOAD_TIMEOUT_SEC  = 45

# Hostnames that appear in hosts files but are not real block targets
_HOSTS_WHITELIST = frozenset({
    "0.0.0.0", "127.0.0.1", "::1",
    "localhost", "localhost.localdomain",
    "local", "broadcasthost",
    "ip6-localhost", "ip6-loopback",
    "ip6-localnet", "ip6-mcastprefix",
    "ip6-allnodes", "ip6-allrouters", "ip6-allhosts",
})

_ADGUARD_RULE = re.compile(r"^\|\|([a-zA-Z0-9._-]+)\^")


# ── Parsers ───────────────────────────────────────────────────────────────────

def _parse_hosts_format(text: str) -> set[str]:
    """Parse StevenBlack-style hosts file: '0.0.0.0 hostname'."""
    hosts: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in ("#", "!"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0] in ("0.0.0.0", "127.0.0.1"):
            h = parts[1].lower().rstrip(".")
            if h and h not in _HOSTS_WHITELIST:
                hosts.add(h)
    return hosts


def _parse_adguard_format(text: str) -> set[str]:
    """Parse AdGuard DNS filter format: '||hostname^'."""
    hosts: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in ("!", "#", "["):
            continue
        m = _ADGUARD_RULE.match(line)
        if m:
            h = m.group(1).lower().rstrip(".")
            if h:
                hosts.add(h)
    return hosts


def _parse(source_name: str, text: str) -> set[str]:
    if source_name == "adguard":
        return _parse_adguard_format(text)
    return _parse_hosts_format(text)


# ── Downloader (blocking — runs in thread executor) ───────────────────────────

def _download(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; adblock-updater)"},
    )
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_SEC) as resp:
        return resp.read().decode("utf-8", errors="ignore")


# ── Main class ────────────────────────────────────────────────────────────────

class AdBlocker:
    """Downloads, parses, and caches ad/tracker blocklists."""

    def __init__(
        self,
        cache_dir: str,
        sources: list[str],
        update_hours: int,
    ) -> None:
        self._cache_dir      = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._sources        = [s for s in sources if s in SOURCES]
        self._update_seconds = update_hours * 3600

        unknown = [s for s in sources if s not in SOURCES]
        if unknown:
            log.warning("AdBlock: unknown sources ignored: %s", unknown)

    async def load(self, force: bool = False) -> set[str]:
        """Return merged set of blocked hostnames from all configured sources.

        Pass force=True to bypass the cache and re-download every source.
        """
        loop      = asyncio.get_running_loop()
        all_hosts: set[str] = set()

        for source in self._sources:
            url   = SOURCES[source]
            hosts = await loop.run_in_executor(
                None, self._load_source, source, url, force
            )
            log.info("AdBlock [%-12s]: %d domains", source, len(hosts))
            all_hosts |= hosts

        log.info("AdBlock total: %d unique blocked domains", len(all_hosts))
        return all_hosts

    # ── Internal ──────────────────────────────────────────────────────────────

    def _cache_path(self, name: str) -> Path:
        return self._cache_dir / f"{name}.json"

    def _load_source(self, name: str, url: str, force: bool = False) -> set[str]:
        cache_file = self._cache_path(name)

        bak_file = cache_file.with_suffix(".json.bak")
        if force and cache_file.exists():
            # Rename to .bak so we can still fall back if the download fails
            cache_file.replace(bak_file)
            log.info("AdBlock [%s]: cache cleared (forced refresh)", name)

        # ── Try valid cache first ─────────────────────────────────────────────
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                age  = time.time() - float(data.get("ts", 0))
                if age < self._update_seconds:
                    log.info(
                        "AdBlock [%s]: cache hit (%.0fh old, refresh in %.0fh)",
                        name, age / 3600, (self._update_seconds - age) / 3600,
                    )
                    return set(data["hosts"])
            except Exception as exc:
                log.debug("AdBlock [%s]: cache read error: %s", name, exc)

        # ── Download fresh copy ───────────────────────────────────────────────
        log.info("AdBlock [%s]: downloading %s …", name, url)
        try:
            text  = _download(url)
            hosts = list(_parse(name, text))
            cache_file.write_text(
                json.dumps({"ts": time.time(), "hosts": hosts}, separators=(",", ":")),
                encoding="utf-8",
            )
            log.info("AdBlock [%s]: saved %d domains to cache", name, len(hosts))
            return set(hosts)

        except Exception as exc:
            log.warning("AdBlock [%s]: download failed: %s", name, exc)

            # ── Stale cache fallback (original or .bak from force-refresh) ──────
            for fallback in (cache_file, bak_file):
                if fallback.exists():
                    try:
                        data = json.loads(fallback.read_text(encoding="utf-8"))
                        log.warning(
                            "AdBlock [%s]: using stale cache as fallback (%s)",
                            name, fallback.name,
                        )
                        return set(data["hosts"])
                    except Exception:
                        pass

            return set()
