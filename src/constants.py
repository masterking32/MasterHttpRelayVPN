"""
Central location for tunable constants used across the project.

Values here are chosen for safe defaults; individual entries may be
overridden from `config.json` where noted.
"""

from __future__ import annotations

# ── Version ───────────────────────────────────────────────────────────────
__version__ = "1.1.0"


# ── Size caps ─────────────────────────────────────────────────────────────
MAX_REQUEST_BODY_BYTES  = 100 * 1024 * 1024   # 100 MB  — inbound browser body
MAX_RESPONSE_BODY_BYTES = 200 * 1024 * 1024   # 200 MB  — chunked response cap
MAX_HEADER_BYTES        = 64 * 1024           # 64 KB


# ── Timeouts (seconds) ────────────────────────────────────────────────────
CLIENT_IDLE_TIMEOUT     = 120
RELAY_TIMEOUT           = 25
TLS_CONNECT_TIMEOUT     = 15
TCP_CONNECT_TIMEOUT     = 10

# ── Google IP Scanner settings ──────────────────────────────────────────────
GOOGLE_SCANNER_TIMEOUT      = 4       # Timeout per IP probe (seconds)
GOOGLE_SCANNER_CONCURRENCY  = 8       # Parallel probes
# Candidate Google frontend IPs for scanning (multiple ASNs and regions)
CANDIDATE_IPS: tuple[str, ...] = (
    "216.239.32.120",
    "216.239.34.120",
    "216.239.36.120",
    "216.239.38.120",
    "142.250.80.142",
    "142.250.80.138",
    "142.250.179.110",
    "142.250.185.110",
    "142.250.184.206",
    "142.250.190.238",
    "142.250.191.78",
    "172.217.1.206",
    "172.217.14.206",
    "172.217.16.142",
    "172.217.22.174",
    "172.217.164.110",
    "172.217.168.206",
    "172.217.169.206",
    "34.107.221.82",
    "142.251.32.110",
    "142.251.33.110",
    "142.251.46.206",
    "142.251.46.238",
    "142.250.80.170",
    "142.250.72.206",
    "142.250.64.206",
    "142.250.72.110",
)

# ── Response cache ────────────────────────────────────────────────────────
CACHE_MAX_MB            = 50
CACHE_TTL_STATIC_LONG   = 3600   # images / fonts
CACHE_TTL_STATIC_MED    = 1800   # css / js
CACHE_TTL_MAX           = 86400  # hard cap on any explicit max-age


# ── Connection pool (HTTP/1.1 to Apps Script) ─────────────────────────────
POOL_MAX                = 50
POOL_MIN_IDLE           = 15
CONN_TTL                = 45.0
SEMAPHORE_MAX           = 50
WARM_POOL_COUNT         = 30


# ── Batch windows ─────────────────────────────────────────────────────────
BATCH_WINDOW_MICRO      = 0.005   # 5 ms
BATCH_WINDOW_MACRO      = 0.050   # 50 ms
BATCH_MAX               = 50


# ── Fan-out relay (parallel Apps Script instances) ────────────────────────
# How long to ignore a script ID after it fails or is unreasonably slow.
SCRIPT_BLACKLIST_TTL    = 600.0   # 10 minutes


# ── SNI rotation pool ─────────────────────────────────────────────────────
# Google-owned SNIs that share the same edge IPs as www.google.com.
# When `front_domain` is a Google property, we rotate through this pool on
# each new outbound TLS handshake so DPI systems don't see a constant
# "always www.google.com" pattern from the client.
# Looks like that only mail and google.com not have a shaped DPI, the rest are 16kb shape blocked.
# from my own benchmarks . Google and mail have 658 kb ps but the rest have 16 kb ps.
FRONT_SNI_POOL_GOOGLE: tuple[str, ...] = (
    "mail.google.com",
    "accounts.google.com",
    "www.google.com",
    # "drive.google.com",
    # "docs.google.com",
    # "calendar.google.com",
    # "maps.google.com",
    # "chat.google.com",
    # "translate.google.com",
    # "play.google.com",
    # "lens.google.com",
    # "scholar.google.com",
    # "chromewebstore.google.com",
)


# ── Per-host stats ────────────────────────────────────────────────────────
STATS_LOG_INTERVAL      = 300.0   # seconds — how often to log per-host totals
STATS_LOG_TOP_N         = 10      # how many hosts to include in the log


# ── Direct Google tunnel allow / exclude ──────────────────────────────────
# Google web-apps whose real origin must go through the Apps Script relay
# because direct SNI tunneling to them does not work reliably behind DPI.
GOOGLE_DIRECT_EXACT_EXCLUDE = frozenset({
    "gemini.google.com",
    "aistudio.google.com",
    "notebooklm.google.com",
    "labs.google.com",
    "meet.google.com",
    "accounts.google.com",
    "ogs.google.com",
    "mail.google.com",
    "calendar.google.com",
    "drive.google.com",
    "docs.google.com",
    "chat.google.com",
    "photos.google.com",
    "maps.google.com",
    "myaccount.google.com",
    "contacts.google.com",
    "classroom.google.com",
    "keep.google.com",
    "play.google.com",
    "translate.google.com",
    "assistant.google.com",
    "lens.google.com",
})
GOOGLE_DIRECT_SUFFIX_EXCLUDE: tuple[str, ...] = (
    ".meet.google.com",
)
# Hosts that are known to work better when tunneled directly.
GOOGLE_DIRECT_ALLOW_EXACT = frozenset({
    "www.google.com",
    "google.com",
    "safebrowsing.google.com",
})
GOOGLE_DIRECT_ALLOW_SUFFIXES: tuple[str, ...] = ()


# ── Google-owned domain detection ─────────────────────────────────────────
GOOGLE_OWNED_SUFFIXES: tuple[str, ...] = (
    ".google.com", ".google.co",
    ".googleapis.com", ".gstatic.com",
    ".googleusercontent.com",
)
GOOGLE_OWNED_EXACT = frozenset({
    "google.com", "gstatic.com", "googleapis.com",
})


# ── SNI-rewrite suffixes ──────────────────────────────────────────────────
# Google-owned properties whose real SNI is DPI-blocked but are served by
# the same edge IP as `front_domain`. Routed through the configured
# `google_ip` with SNI rewritten.
SNI_REWRITE_SUFFIXES: tuple[str, ...] = (
    "youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "ytimg.com",
    "ggpht.com",
    "gvt1.com",
    "gvt2.com",
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "google-analytics.com",
    "googletagmanager.com",
    "googletagservices.com",
    "fonts.googleapis.com",
    "script.google.com",
)


# ── Response-logging trace hosts ──────────────────────────────────────────
TRACE_HOST_SUFFIXES: tuple[str, ...] = (
    "chatgpt.com",
    "openai.com",
    "gemini.google.com",
    "google.com",
    "cloudflare.com",
    "challenges.cloudflare.com",
    "turnstile",
)


# ── File-extension heuristics ─────────────────────────────────────────────
STATIC_EXTS: tuple[str, ...] = (
    ".css", ".js", ".mjs", ".woff", ".woff2", ".ttf", ".eot",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
    ".mp3", ".mp4", ".webm", ".wasm", ".avif",
)
LARGE_FILE_EXTS = frozenset({
    ".bin",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".exe", ".msi", ".dmg", ".deb", ".rpm", ".apk",
    ".iso", ".img",
    ".mp4", ".mkv", ".avi", ".mov", ".webm",
    ".mp3", ".flac", ".wav", ".aac",
    ".pdf", ".doc", ".docx", ".ppt", ".pptx",
    ".wasm",
})


# ── Stateful-request hints ────────────────────────────────────────────────
STATEFUL_HEADER_NAMES: tuple[str, ...] = (
    "cookie", "authorization", "proxy-authorization",
    "origin", "referer", "if-none-match", "if-modified-since",
    "cache-control", "pragma",
)
UNCACHEABLE_HEADER_NAMES: tuple[str, ...] = (
    "cookie", "authorization", "proxy-authorization", "range",
    "if-none-match", "if-modified-since", "cache-control", "pragma",
)
