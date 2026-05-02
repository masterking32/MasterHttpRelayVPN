"""
Proxy helper utilities: header parsing, host rule matching, response caching,
CORS injection, and response logging.

Extracted from proxy_server.py to separate pure helper logic from the
ProxyServer connection handler.
"""

import ipaddress
import logging
import re
import time
from urllib.parse import urlparse
from core.constants import (
    CACHE_TTL_MAX,
    CACHE_TTL_STATIC_LONG,
    CACHE_TTL_STATIC_MED,
    STATIC_EXTS,
)

__all__ = [
    "is_ip_literal",
    "parse_content_length",
    "has_unsupported_transfer_encoding",
    "load_host_rules",
    "host_matches_rules",
    "header_value",
    "should_trace_host",
    "log_response_summary",
    "ResponseCache",
    "cors_preflight_response",
    "inject_cors_headers",
]


def is_ip_literal(host: str) -> bool:
    """True for IPv4/IPv6 literals (strips brackets around IPv6)."""
    normalized = host.strip("[]")
    try:
        ipaddress.ip_address(normalized)
        return True
    except ValueError:
        return False


def parse_content_length(header_block: bytes) -> int:
    """Return Content-Length or 0. Matches only the exact header name."""
    for raw_line in header_block.split(b"\r\n"):
        name, sep, value = raw_line.partition(b":")
        if not sep:
            continue
        if name.strip().lower() == b"content-length":
            try:
                return int(value.strip())
            except ValueError:
                return 0
    return 0


def has_unsupported_transfer_encoding(header_block: bytes) -> bool:
    """True when the request uses Transfer-Encoding, which we don't stream."""
    for raw_line in header_block.split(b"\r\n"):
        name, sep, value = raw_line.partition(b":")
        if not sep:
            continue
        if name.strip().lower() != b"transfer-encoding":
            continue
        encodings = [
            token.strip().lower()
            for token in value.decode(errors="replace").split(",")
            if token.strip()
        ]
        return any(token != "identity" for token in encodings)
    return False


def load_host_rules(raw) -> tuple[set[str], tuple[str, ...]]:
    """Accept a list of host strings; return (exact_set, suffix_tuple)."""
    exact: set[str] = set()
    suffixes: list[str] = []
    for item in raw or []:
        host = str(item).strip().lower().rstrip(".")
        if not host:
            continue
        if host.startswith("."):
            suffixes.append(host)
        else:
            exact.add(host)
    return exact, tuple(suffixes)


def host_matches_rules(host: str, rules: tuple[set[str], tuple[str, ...]]) -> bool:
    exact, suffixes = rules
    normalized = host.lower().rstrip(".")
    if normalized in exact:
        return True
    return any(normalized.endswith(suffix) for suffix in suffixes)


def header_value(headers: dict | None, name: str) -> str:
    if not headers:
        return ""
    for key, value in headers.items():
        if key.lower() == name:
            return str(value)
    return ""


def should_trace_host(host: str, trace_suffixes: tuple[str, ...]) -> bool:
    normalized = host.lower().rstrip(".")
    return any(
        token == normalized or token in normalized or normalized.endswith("." + token)
        for token in trace_suffixes
    )


def log_response_summary(
    *,
    logger: logging.Logger,
    split_raw_response,
    trace_suffixes: tuple[str, ...],
    url: str,
    response: bytes,
) -> None:
    status, headers, body = split_raw_response(response)
    host = (urlparse(url).hostname or "").lower()

    if status < 300 and not should_trace_host(host, trace_suffixes):
        return

    location = headers.get("location", "") or "-"
    server = headers.get("server", "") or "-"
    cf_ray = headers.get("cf-ray", "") or "-"
    content_type = headers.get("content-type", "") or "-"
    body_len = len(body)

    body_hint = "-"
    rate_limited = False

    if ("text" in content_type.lower() or "json" in content_type.lower()) and body:
        sample = body[:1200].decode(errors="replace").lower()
        if "<title>" in sample and "</title>" in sample:
            title = sample.split("<title>", 1)[1].split("</title>", 1)[0]
            body_hint = title.strip()[:120] or "-"
        elif "captcha" in sample:
            body_hint = "captcha"
        elif "turnstile" in sample:
            body_hint = "turnstile"
        elif "loading" in sample:
            body_hint = "loading"

        rate_limit_markers = (
            "too many",
            "rate limit",
            "quota",
            "quota exceeded",
            "request limit",
            "دفعات زیاد",
            "بیش از حد",
            "سرویس در طول یک روز",
        )
        if any(marker in sample for marker in rate_limit_markers):
            rate_limited = True
            body_hint = "quota_exceeded"

    log_msg = (
        "RESP <- %s status=%s type=%s len=%s server=%s location=%s cf-ray=%s hint=%s"
    )
    log_args = (
        host or url[:60],
        status,
        content_type,
        body_len,
        server,
        location,
        cf_ray,
        body_hint,
    )

    if rate_limited:
        logger.warning("RATE LIMIT detected! " + log_msg, *log_args)
    else:
        logger.info(log_msg, *log_args)


class ResponseCache:
    """Simple LRU response cache for relayable static responses."""

    def __init__(self, max_mb: int = 50):
        self._store: dict[str, tuple[bytes, float]] = {}
        self._size = 0
        self._max = max_mb * 1024 * 1024
        self.hits = 0
        self.misses = 0

    def get(self, url: str) -> bytes | None:
        entry = self._store.get(url)
        if not entry:
            self.misses += 1
            return None
        raw, expires = entry
        if time.time() > expires:
            self._size -= len(raw)
            del self._store[url]
            self.misses += 1
            return None
        self.hits += 1
        return raw

    def put(self, url: str, raw_response: bytes, ttl: int = 300):
        size = len(raw_response)
        if size > self._max // 4 or size == 0:
            return
        while self._size + size > self._max and self._store:
            oldest = next(iter(self._store))
            self._size -= len(self._store[oldest][0])
            del self._store[oldest]
        if url in self._store:
            self._size -= len(self._store[url][0])
        self._store[url] = (raw_response, time.time() + ttl)
        self._size += size

    @staticmethod
    def parse_ttl(raw_response: bytes, url: str) -> int:
        """Determine cache TTL from response headers and URL."""
        hdr_end = raw_response.find(b"\r\n\r\n")
        if hdr_end < 0:
            return 0
        hdr = raw_response[:hdr_end].decode(errors="replace").lower()

        if b"HTTP/1.1 200" not in raw_response[:20]:
            return 0
        if "no-store" in hdr or "private" in hdr or "set-cookie:" in hdr:
            return 0

        max_age_match = re.search(r"max-age=(\d+)", hdr)
        if max_age_match:
            return min(int(max_age_match.group(1)), CACHE_TTL_MAX)

        path = url.split("?")[0].lower()
        for ext in STATIC_EXTS:
            if path.endswith(ext):
                return CACHE_TTL_STATIC_LONG

        content_type_match = re.search(r"content-type:\s*([^\r\n]+)", hdr)
        content_type = content_type_match.group(1) if content_type_match else ""
        if "image/" in content_type or "font/" in content_type:
            return CACHE_TTL_STATIC_LONG
        if "text/css" in content_type or "javascript" in content_type:
            return CACHE_TTL_STATIC_MED
        if "text/html" in content_type or "application/json" in content_type:
            return 0

        return 0


# ── CORS helpers ──────────────────────────────────────────────────────────────

def cors_preflight_response(origin: str, acr_method: str, acr_headers: str) -> bytes:
    """Build a 204 response that satisfies a CORS preflight locally.

    Apps Script's UrlFetchApp does not support OPTIONS, so preflights must
    be answered here rather than forwarded to the relay.
    """
    allow_origin = origin or "*"
    allow_methods = (
        f"{acr_method}, GET, POST, PUT, DELETE, PATCH, OPTIONS"
        if acr_method else
        "GET, POST, PUT, DELETE, PATCH, OPTIONS"
    )
    allow_headers = acr_headers or "*"
    return (
        "HTTP/1.1 204 No Content\r\n"
        f"Access-Control-Allow-Origin: {allow_origin}\r\n"
        f"Access-Control-Allow-Methods: {allow_methods}\r\n"
        f"Access-Control-Allow-Headers: {allow_headers}\r\n"
        "Access-Control-Allow-Credentials: true\r\n"
        "Access-Control-Max-Age: 86400\r\n"
        "Vary: Origin\r\n"
        "Content-Length: 0\r\n"
        "\r\n"
    ).encode()


def inject_cors_headers(response: bytes, origin: str) -> bytes:
    """Strip existing Access-Control-* headers and inject permissive ones.

    Keeps the body untouched; only rewrites the header block. Using the
    exact browser-supplied Origin (rather than "*") is required when the
    request is credentialed (cookies, Authorization).
    """
    sep = b"\r\n\r\n"
    if sep not in response:
        return response
    header_section, body = response.split(sep, 1)
    lines = header_section.decode(errors="replace").split("\r\n")
    lines = [ln for ln in lines if not ln.lower().startswith("access-control-")]
    allow_origin = origin or "*"
    lines += [
        f"Access-Control-Allow-Origin: {allow_origin}",
        "Access-Control-Allow-Credentials: true",
        "Access-Control-Allow-Methods: GET, POST, PUT, DELETE, PATCH, OPTIONS",
        "Access-Control-Allow-Headers: *",
        "Access-Control-Expose-Headers: *",
        "Vary: Origin",
    ]
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body
