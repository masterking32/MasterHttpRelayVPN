#!/usr/bin/env python3
"""
MasterHttpRelayVPN — VPS Exit Node Server  (Linux only)

A lightweight HTTP relay server you can run on your own Linux VPS.
It receives relay requests forwarded by Apps Script (on behalf of
MasterHttpRelayVPN) and makes the actual outbound HTTP/HTTPS connections
using your VPS's IP address.

Traffic path with this server:
  Browser → Local Proxy → Apps Script (Google) → THIS SERVER → Target website

Usage:
  python3 vps_exit_node.py --psk YOUR_STRONG_SECRET [--host 0.0.0.0] [--port 8181]

Or use the environment variable instead of --psk:
  export EXIT_NODE_PSK=YOUR_STRONG_SECRET
  python3 vps_exit_node.py

For easy installation on a fresh Linux VPS, use the provided installer:
  bash setup_vps_exit_node.sh

For production use, run behind a reverse proxy (nginx / Caddy) that
handles TLS so the endpoint is reachable over HTTPS.

NOTE: This script is designed for Linux only.  It will refuse to start
on Windows or macOS.
"""

import argparse
import base64
import http.server
import json
import logging
import os
import re
import socketserver
import sys
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("exit-node")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Headers that must never be forwarded to the upstream target because they
# are connection-local, injected by the relay chain, or could leak caller
# information.
_STRIP_HEADERS = frozenset(
    [
        "host",
        "connection",
        "content-length",
        "transfer-encoding",
        "keep-alive",
        "te",
        "trailer",
        "upgrade",
        "proxy-connection",
        "proxy-authorization",
        "proxy-authenticate",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-forwarded-port",
        "x-real-ip",
        "forwarded",
        "via",
    ]
)

# Maximum request body accepted from the relay chain (32 MiB).
_MAX_REQUEST_BODY = 32 * 1024 * 1024

# Maximum response body forwarded back (64 MiB).
_MAX_RESPONSE_BODY = 64 * 1024 * 1024

# Outbound request timeout in seconds.
_OUTBOUND_TIMEOUT = 30

# Pre-shared key loaded at startup.
_PSK: str = ""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_headers(raw: object) -> dict[str, str]:
    """Return a clean header dict, dropping hop-by-hop and proxy headers."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not k or not isinstance(k, str):
            continue
        if k.lower() in _STRIP_HEADERS:
            continue
        out[k] = str(v) if v is not None else ""
    return out


def _safe_url(url: str) -> bool:
    """Return True only for plain http:// or https:// URLs (no localhost / LAN)."""
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return False
    # Block requests to loopback / private addresses to prevent SSRF.
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    host = host.lower().rstrip(".")
    # Reject empty, numeric localhost, and obviously private hostnames.
    _PRIVATE = re.compile(
        r"^("
        r"localhost"
        r"|127\.\d+\.\d+\.\d+"
        r"|::1"
        r"|0\.0\.0\.0"
        r"|10\.\d+\.\d+\.\d+"
        r"|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+"
        r"|192\.168\.\d+\.\d+"
        r"|169\.254\.\d+\.\d+"
        r"|fc[0-9a-f]{2}:.*"
        r"|fd[0-9a-f]{2}:.*"
        r")$"
    )
    if _PRIVATE.match(host):
        return False
    return True


def _relay_request(
    url: str, method: str, headers: dict[str, str], body: bytes
) -> dict:
    """Perform the outbound HTTP/HTTPS request and return a relay-JSON dict."""
    request = urllib.request.Request(url, method=method, headers=headers)
    if body:
        request.data = body

    try:
        with urllib.request.urlopen(request, timeout=_OUTBOUND_TIMEOUT) as resp:
            data = resp.read(_MAX_RESPONSE_BODY)
            resp_headers: dict[str, str] = {}
            for k, v in resp.headers.items():
                resp_headers[k] = v
            return {
                "s": resp.status,
                "h": resp_headers,
                "b": base64.b64encode(data).decode(),
            }
    except urllib.error.HTTPError as exc:
        data = exc.read(_MAX_RESPONSE_BODY) if exc.fp else b""
        resp_headers = {}
        if exc.headers:
            for k, v in exc.headers.items():
                resp_headers[k] = v
        return {
            "s": exc.code,
            "h": resp_headers,
            "b": base64.b64encode(data).decode(),
        }


# ---------------------------------------------------------------------------
# HTTP request handler
# ---------------------------------------------------------------------------


class _ExitNodeHandler(http.server.BaseHTTPRequestHandler):
    # Suppress the default per-request access log lines; we emit our own.
    def log_message(self, fmt, *args):  # noqa: D102
        pass

    def _send_json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        """Health-check endpoint — returns a friendly JSON status."""
        self._send_json(
            200,
            {
                "ok": True,
                "status": "healthy",
                "message": "VPS exit node is running.",
                "usage": "Send POST with relay payload for actual proxy requests.",
            },
        )

    def do_POST(self):  # noqa: N802
        """Relay endpoint — receives a JSON relay payload, fetches the URL."""
        content_length = int(self.headers.get("Content-Length") or 0)
        if content_length <= 0:
            self._send_json(400, {"e": "empty_body"})
            return
        if content_length > _MAX_REQUEST_BODY:
            self._send_json(413, {"e": "request_too_large"})
            return

        raw = self.rfile.read(content_length)
        try:
            body = json.loads(raw)
        except Exception:
            self._send_json(400, {"e": "bad_json"})
            return

        if not isinstance(body, dict):
            self._send_json(400, {"e": "bad_json"})
            return

        k = str(body.get("k") or "")
        u = str(body.get("u") or "")
        m = str(body.get("m") or "GET").upper()
        h = _sanitize_headers(body.get("h"))
        b64 = body.get("b")

        if not _PSK:
            self._send_json(500, {"e": "server_psk_missing"})
            return

        if k != _PSK:
            log.warning("Rejected unauthorized request from %s", self.client_address[0])
            self._send_json(401, {"e": "unauthorized"})
            return

        if not _safe_url(u):
            self._send_json(400, {"e": "bad_url"})
            return

        payload_bytes = b""
        if isinstance(b64, str) and b64:
            try:
                payload_bytes = base64.b64decode(b64)
            except Exception:
                self._send_json(400, {"e": "bad_base64"})
                return

        log.info("Relaying %s %s", m, u[:100])
        try:
            result = _relay_request(u, m, h, payload_bytes)
        except Exception as exc:
            log.warning("Relay error for %s: %s", u[:80], exc)
            self._send_json(500, {"e": str(exc) or type(exc).__name__})
            return

        log.info("Relay OK %s → HTTP %d (%d B)", u[:80], result["s"], len(result.get("b", "")))
        self._send_json(200, result)


# ---------------------------------------------------------------------------
# Server entry-point
# ---------------------------------------------------------------------------


class _ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """HTTP server that handles each request in a separate thread."""

    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MasterHttpRelayVPN — VPS Exit Node Server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--psk",
        default="",
        metavar="SECRET",
        help="Pre-shared key for authentication (or set EXIT_NODE_PSK env var).",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host/IP to listen on (default: 0.0.0.0).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8181,
        help="TCP port to listen on (default: 8181).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )
    args = parser.parse_args()

    logging.getLogger().setLevel(args.log_level)

    if sys.platform != "linux":
        log.error(
            "This VPS exit node is designed for Linux only. "
            "Current platform: %s", sys.platform
        )
        sys.exit(1)

    global _PSK
    _PSK = (args.psk or os.environ.get("EXIT_NODE_PSK", "")).strip()
    if not _PSK:
        log.error(
            "No PSK configured. Pass --psk YOUR_SECRET or set the "
            "EXIT_NODE_PSK environment variable."
        )
        sys.exit(1)

    server = _ThreadedHTTPServer((args.host, args.port), _ExitNodeHandler)
    log.info(
        "VPS exit node listening on %s:%d  (press Ctrl+C to stop)",
        args.host,
        args.port,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
