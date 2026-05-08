"""
Adblock hosts list loader.

Downloads and caches domain blocklists at startup, then merges them into the
proxy's block-host rules.  Supports two common list formats:

  • Bare domain per line   — used by PersianBlocker Hosts files
  • Standard hosts format  — "0.0.0.0 domain.com" / "127.0.0.1 domain.com"

Comments (#), wildcards (analytics-*.example.com), and raw IP addresses are
skipped automatically.

Usage from proxy_server.py:
    from core.adblock import load_all, refresh_all

    # Synchronous load at startup (uses disk cache if available):
    domains = load_all(config["adblock_lists"])

    # Async background refresh (re-downloads stale lists):
    await refresh_all(config["adblock_lists"], callback=update_fn)
"""

import asyncio
import hashlib
import ipaddress
import logging
import pathlib
import re
import time
import urllib.request

log = logging.getLogger("Adblock")

# Re-download a list when the cached copy is older than this (seconds).
_DEFAULT_MAX_AGE = 86_400          # 24 hours
_DOWNLOAD_TIMEOUT = 30             # seconds per HTTP request

# Cache sits next to the project root (same dir as main.py / config.json).
# Anchored to this file's location so the cache is always found regardless
# of the working directory the user launches the proxy from.
_CACHE_DIR = pathlib.Path(__file__).parent.parent.parent / "adblock_cache"

# Patterns used during line parsing
_IP_RE = re.compile(
    r"^(?:\d{1,3}\.){3}\d{1,3}$"           # IPv4
    r"|^[0-9a-fA-F:]{2,39}$"               # IPv6 (rough match)
)
_WILDCARD_RE = re.compile(r"[*?]")

# Minimal domain sanity check: must contain at least one dot, only ASCII
# label characters, and no leading/trailing hyphens in any label.
_DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$"
)

_SKIP_NAMES = frozenset({
    "localhost", "local", "broadcasthost",
    "localhost.localdomain", "ip6-localhost",
    "ip6-loopback",
})

# These addresses appear in standard hosts files as the "null" target —
# they are address fields, not domain names.
_HOSTS_PREFIXES = frozenset({"0.0.0.0", "127.0.0.1", "::1", "::0"})


# ── List parsing ──────────────────────────────────────────────────────────────

def parse_hosts_text(text: str) -> list[str]:
    """Parse a hosts-format (or bare-domain-per-line) text.

    Returns a deduplicated list of valid, lowercase domain strings.
    Wildcards, raw IPs, comments, and the reserved names above are dropped.
    """
    seen: set[str] = set()
    domains: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()

        # Skip blank lines and full-line comments
        if not line or line.startswith("#"):
            continue

        # Strip inline comments
        comment_pos = line.find(" #")
        if comment_pos != -1:
            line = line[:comment_pos].strip()

        parts = line.split()

        if len(parts) == 2 and parts[0] in _HOSTS_PREFIXES:
            # Standard hosts format: "0.0.0.0 domain.com"
            domain = parts[1].lower().rstrip(".")
        elif len(parts) == 1:
            # Bare domain format
            domain = parts[0].lower().rstrip(".")
        else:
            # Multiple words with unknown prefix, or empty after stripping — skip
            continue

        # Skip wildcards (analytics-*.example.com) — can't match them safely
        if _WILDCARD_RE.search(domain):
            continue

        # Skip raw IP addresses
        if _IP_RE.match(domain):
            continue
        try:
            ipaddress.ip_address(domain)
            continue
        except ValueError:
            pass

        # Skip reserved names
        if domain in _SKIP_NAMES:
            continue

        # Basic domain structure check: at least one dot, valid label chars
        if not _DOMAIN_RE.match(domain):
            continue

        if domain not in seen:
            seen.add(domain)
            domains.append(domain)

    return domains


# ── Disk cache helpers ────────────────────────────────────────────────────────

def _cache_path(url: str) -> pathlib.Path:
    h = hashlib.sha1(url.encode()).hexdigest()[:16]
    return _CACHE_DIR / f"{h}.txt"


def _cache_is_stale(url: str, max_age: int) -> bool:
    path = _cache_path(url)
    if not path.exists():
        return True
    try:
        return (time.time() - path.stat().st_mtime) > max_age
    except OSError:
        return True


def _read_cache(url: str) -> list[str] | None:
    path = _cache_path(url)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return parse_hosts_text(text)
    except OSError:
        return None


def _write_cache(url: str, text: str) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(url).write_text(text, encoding="utf-8")
    except OSError as exc:
        log.warning("Adblock: cache write failed: %s", exc)


# ── Network fetch ─────────────────────────────────────────────────────────────

def _fetch(url: str) -> str | None:
    """Blocking HTTP GET — intended to run inside asyncio.to_thread()."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "MasterHttpRelayVPN/adblock-updater"},
        )
        with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        log.warning("Adblock: download failed (%s): %s", url, exc)
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def load_all(urls: list[str], max_age: int = _DEFAULT_MAX_AGE) -> list[str]:
    """Synchronously load all lists.  Called once at proxy startup.

    Strategy:
      • If a cached copy exists (even if stale), return it immediately so
        startup is never blocked by network I/O.
      • If there is NO cached copy for a URL, download it now (one-time,
        first-run penalty) so the adblock is active from the first request.

    Stale caches will be refreshed later by ``refresh_all()``.
    """
    all_domains: list[str] = []
    for url in urls:
        url = url.strip()
        if not url:
            continue
        cached = _read_cache(url)
        if cached is not None:
            log.info(
                "Adblock: %d domains loaded from cache (%s)",
                len(cached),
                url.split("/")[-1],
            )
            all_domains.extend(cached)
        else:
            log.info("Adblock: no cache for %s — downloading...", url.split("/")[-1])
            text = _fetch(url)
            if text:
                _write_cache(url, text)
                domains = parse_hosts_text(text)
                log.info(
                    "Adblock: downloaded %d domains from %s",
                    len(domains),
                    url.split("/")[-1],
                )
                all_domains.extend(domains)
            else:
                log.warning("Adblock: could not load %s — adblock disabled for this list", url)
    return all_domains


async def refresh_all(
    urls: list[str],
    max_age: int = _DEFAULT_MAX_AGE,
    callback=None,
) -> list[str]:
    """Async background refresh.  Re-downloads lists whose cache is stale.

    ``callback(domains: list[str])`` is called on the asyncio event loop
    after any list is successfully updated, letting the proxy hot-swap the
    active block set without restarting.
    """
    all_domains: list[str] = []
    changed = False

    for url in urls:
        url = url.strip()
        if not url:
            continue

        if not _cache_is_stale(url, max_age):
            cached = _read_cache(url) or []
            all_domains.extend(cached)
            continue

        log.info("Adblock: refreshing %s ...", url.split("/")[-1])
        text = await asyncio.to_thread(_fetch, url)
        if text:
            await asyncio.to_thread(_write_cache, url, text)
            domains = await asyncio.to_thread(parse_hosts_text, text)
            log.info(
                "Adblock: refreshed %d domains from %s",
                len(domains),
                url.split("/")[-1],
            )
            all_domains.extend(domains)
            changed = True
        else:
            # Keep using stale cache rather than losing protection
            cached = _read_cache(url) or []
            all_domains.extend(cached)

    if changed and callback is not None:
        callback(all_domains)

    return all_domains
