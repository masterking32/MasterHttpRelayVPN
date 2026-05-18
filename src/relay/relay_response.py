"""
Apps Script relay response parsing.

Pure functions for decoding the JSON envelope returned by Code.gs and
reconstructing a standard HTTP response that the proxy can forward to
the client browser.

Public API
----------
parse_relay_response(body, max_body_bytes) -> bytes
    Top-level entry point: bytes → raw HTTP response bytes.

split_raw_response(raw) -> (status, headers, body)
    Parse a raw HTTP byte string into its parts.

error_response(status, message) -> bytes
    Build a minimal HTML error response.

classify_relay_error(raw) -> str
    Map a raw Apps Script error string to a human-readable explanation.
"""

import base64
import gzip
import html
import json
import logging
import re

from core import codec

log = logging.getLogger("Fronter")

__all__ = [
    "classify_relay_error",
    "classify_relay_envelope",
    "error_response",
    "split_raw_response",
    "split_set_cookie",
    "parse_relay_json",
    "extract_apps_script_user_html",
    "load_relay_json",
    "parse_relay_response",
]


# ── Apps Script error pattern tables ─────────────────────────────────────────
# Matched against the lower-cased ``e`` field returned by Code.gs.
# Sources:
#   • https://developers.google.com/apps-script/guides/support/troubleshooting
#   • https://developers.google.com/apps-script/guides/services/quotas

# "Service invoked too many times for one day: urlfetch."
# "Bandwidth quota exceeded"
_QUOTA_PATTERNS = (
    "service invoked too many times",
    "invoked too many times",
    "bandwidth quota exceeded",
    "too much upload bandwidth",
    "too much traffic",
    "urlfetch",   # appears at end of the daily-quota message in all locales
    "quota",
    "exceeded",
    "daily",
    "rate limit",
)

# "Authorization is required to perform that action."
# "unauthorized"  (our own Code.gs response)
_AUTH_PATTERNS = (
    "authorization is required",
    "unauthorized",
    "not authorized",
    "permission denied",
    "access denied",
)

# "Error occurred due to a missing library version or a deployment version.
#  Error code Not_Found"
# "script id not found" / wrong Deployment ID
_DEPLOY_PATTERNS = (
    "error code not_found",
    "not_found",
    "deployment",
    "script id",
    "scriptid",
    "no script",
)

# "Server not available." / "Server error occurred, please try again."
_TRANSIENT_PATTERNS = (
    "server not available",
    "server error occurred",
    "please try again",
    "temporarily unavailable",
)

# "UrlFetch calls to <URL> are not permitted by your admin"
# "<Class> / Apiary.<Service> is disabled. Please contact your administrator"
_ADMIN_PATTERNS = (
    "not permitted by your admin",
    "contact your administrator",
    "disabled. please contact",
    "domain policy has disabled",
    "administrator to enable",
)

# Exit node / network errors
_EXIT_NODE_PATTERNS = (
    "dns",
    "connection refused",
    "connection reset",
    "unable to connect",
    "timeout",
    "exit node",
    "invalid url",
    "url not valid",
)

# ── Error classifier ──────────────────────────────────────────────────────────

def classify_relay_error(raw: str) -> str:
    """Return a human-readable explanation for a known Apps Script error.

    Covers every error category documented at:
    developers.google.com/apps-script/guides/support/troubleshooting
    """
    lower = raw.lower()

    # Relay loop detected by Code.gs or a Cloudflare Worker exit node.
    if "loop detected" in lower or lower == "loop_detected":
        return (
            "Relay loop detected. "
            "Your exit node URL is misconfigured — it points back to a "
            "Google Apps Script deployment or to the Cloudflare Worker itself. "
            "Set 'exit_node_url' in config.json to the actual exit node address "
            "(Cloudflare Worker, Deno Deploy, or VPS), not to a GAS script URL."
        )

    if any(p in lower for p in _QUOTA_PATTERNS):
        return (
            "Apps Script quota exhausted. "
            "Either the 20,000 URL-fetch calls/day limit or the 100 MB/day "
            "bandwidth limit has been reached. "
            "Wait up to 24 hours for the quota to reset, or create a second "
            "Google account, deploy a fresh Apps Script there, and add its "
            "script_id to config.json."
        )

    if any(p in lower for p in _AUTH_PATTERNS):
        return (
            "Apps Script rejected the request (auth/permission error). "
            "Check: (1) AUTH_KEY in Code.gs matches 'auth_key' in config.json, "
            "(2) the deployment is set to 'Execute as: Me / Anyone can access', "
            "(3) you are using the Deployment ID (not the Script ID), "
            "(4) the owning Google account has authorised the script by running "
            "it manually at least once."
        )

    if any(p in lower for p in _DEPLOY_PATTERNS):
        return (
            "Apps Script deployment not found. "
            "Verify 'script_id' in config.json is the Deployment ID "
            "(not the Script ID), the deployment is active/not archived, "
            "and you re-created the deployment after editing Code.gs."
        )

    if any(p in lower for p in _TRANSIENT_PATTERNS):
        return (
            "Google Apps Script server is temporarily unavailable. "
            "This is a transient Google-side error — wait a moment and retry. "
            f"(raw: {raw})"
        )

    if any(p in lower for p in _ADMIN_PATTERNS):
        return (
            "Apps Script is blocked by a Google Workspace admin policy. "
            "Either the target URL is not on the admin's UrlFetch allowlist, "
            "or a Google service used by the script has been disabled by the "
            "domain administrator. Contact your Google Workspace admin. "
            f"(raw: {raw})"
        )

    if any(p in lower for p in _EXIT_NODE_PATTERNS):
        # Exit node errors
        if "dns" in lower:
            return (
                "DNS error in exit node. "
                "The exit node URL in config.json might be misspelled or unreachable. "
                "Check: (1) exit_node.url is spelled correctly, "
                "(2) the domain can be resolved, "
                "(3) your network can reach that exit node, "
                "(4) try disabling the exit node temporarily: set exit_node.enabled to false"
            )
        else:
            return (
                f"Network error from exit node: {raw} "
                "Check your exit_node configuration in config.json or try disabling it."
            )

    # Unknown — strip the leading 'Exception: ' / 'Error: ' prefix that
    # Apps Script always prepends, so the message is shorter and cleaner.
    cleaned = re.sub(r'^(Exception|Error):\s*', '', raw, flags=re.IGNORECASE).strip()
    return f"Relay error: {cleaned or raw}"


def classify_relay_envelope(body: bytes) -> tuple[str | None, str]:
    """Classify a raw Apps Script response body into a permanent-failure category.

    Returns ``(category, raw)`` where ``category`` is one of
    ``"quota" | "auth" | "deploy" | "admin"`` for permanent / quota-class
    failures that should disable the originating script ID, or ``None``
    for healthy bodies, transient envelopes, and unrecognised content.

    ``raw`` is the original ``data["e"]`` string (empty when the body
    has no envelope error).

    The classifier is pure: it never raises, never logs, and never
    performs IO. Bad input (empty bytes, non-UTF-8, non-JSON, dict with
    no ``"e"`` key) deterministically returns ``(None, "")``.

    Match priority is quota > auth > deploy > admin > transient/other.
    Transient envelopes (e.g. "Server not available", "please try
    again") and any other unrecognised envelope errors fall through to
    ``(None, raw)`` because the deployment itself is still working —
    requirement 3.4 says they must keep the existing 502 surface.
    """
    try:
        text = body.decode(errors="replace").strip()
        if not text:
            return None, ""
        data = load_relay_json(text)
        if data is None or "e" not in data:
            return None, ""
        raw = str(data["e"])
    except (TypeError, ValueError, AttributeError):
        return None, ""

    lower = raw.lower()

    if any(p in lower for p in _QUOTA_PATTERNS):
        return "quota", raw
    if any(p in lower for p in _AUTH_PATTERNS):
        return "auth", raw
    if any(p in lower for p in _DEPLOY_PATTERNS):
        return "deploy", raw
    if any(p in lower for p in _ADMIN_PATTERNS):
        return "admin", raw

    # Transient (`_TRANSIENT_PATTERNS`) and any other unknown envelope
    # error — the deployment is still working, so do NOT blacklist it.
    return None, raw


# ── Low-level HTTP helpers ────────────────────────────────────────────────────

def _build_502_html(message: str) -> str:
    """Build HTML page for 502 errors with troubleshooting guide."""
    safe_message = html.escape(message, quote=True)
    # JSON-encode the message so it can be safely embedded as a JS string literal
    js_message = json.dumps(message)

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>502 — Relay Error</title>
    <style>
        *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f0c29;
            background: linear-gradient(160deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 24px;
            color: #e2e8f0;
        }}
        .card {{
            max-width: 560px;
            width: 100%;
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 16px;
            backdrop-filter: blur(12px);
            overflow: hidden;
            box-shadow: 0 25px 60px rgba(0,0,0,0.5);
        }}
        .resource-bar {{
            padding: 14px 18px;
            border-bottom: 1px solid rgba(255,255,255,0.08);
            background: linear-gradient(120deg, rgba(56,189,248,0.12), rgba(99,102,241,0.12));
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 8px;
        }}
        .resource-title {{
            font-size: 0.82em;
            letter-spacing: 0.08em;
            color: #93c5fd;
            margin-right: 6px;
            font-weight: 700;
        }}
        .resource-link {{
            text-decoration: none;
            color: #dbeafe;
            font-size: 0.8em;
            background: rgba(15,23,42,0.42);
            border: 1px solid rgba(147,197,253,0.35);
            border-radius: 999px;
            padding: 5px 10px;
            transition: all 0.18s ease;
            white-space: nowrap;
        }}
        .resource-link:hover {{
            background: rgba(15,23,42,0.65);
            border-color: rgba(147,197,253,0.7);
            color: #eff6ff;
            transform: translateY(-1px);
        }}
        .card-header {{
            padding: 32px 32px 24px;
            border-bottom: 1px solid rgba(255,255,255,0.07);
            display: flex;
            align-items: center;
            gap: 16px;
        }}
        .badge {{
            background: rgba(239,68,68,0.15);
            border: 1px solid rgba(239,68,68,0.35);
            border-radius: 10px;
            padding: 10px 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }}
        .badge svg {{ display: block; }}
        .card-header h1 {{ font-size: 1.7em; font-weight: 700; color: #f1f5f9; }}
        .card-header p {{ font-size: 0.85em; color: #94a3b8; margin-top: 3px; }}
        .card-body {{ padding: 24px 32px; display: flex; flex-direction: column; gap: 18px; }}
        .error-box {{
            background: rgba(239,68,68,0.08);
            border: 1px solid rgba(239,68,68,0.25);
            border-radius: 10px;
            padding: 16px 18px;
        }}
        .error-box .label {{
            font-size: 0.72em;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: #f87171;
            margin-bottom: 8px;
        }}
        .error-box div#error-text,
        .error-box p {{
            font-size: 0.9em;
            line-height: 1.65;
            color: #fca5a5;
            word-break: break-word;
        }}
        .error-box div#error-text p {{ margin-bottom: 6px; }}
        .error-box div#error-text ol {{
            padding-left: 18px;
            display: flex;
            flex-direction: column;
            gap: 4px;
            margin-top: 6px;
        }}
        .hints-box {{
            background: rgba(59,130,246,0.08);
            border: 1px solid rgba(59,130,246,0.22);
            border-radius: 10px;
            padding: 16px 18px;
        }}
        .hints-box .label {{
            font-size: 0.72em;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: #60a5fa;
            margin-bottom: 12px;
        }}
        .hints-box ol {{
            padding-left: 18px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        .hints-box li {{ font-size: 0.875em; line-height: 1.55; color: #bfdbfe; }}
        .hints-box li strong {{ color: #93c5fd; font-weight: 600; }}
        .tips-box {{
            background: rgba(245,158,11,0.07);
            border: 1px solid rgba(245,158,11,0.22);
            border-radius: 10px;
            padding: 16px 18px;
        }}
        .tips-box .label {{
            font-size: 0.72em;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: #fbbf24;
            margin-bottom: 12px;
        }}
        .tips-box ul {{
            padding-left: 18px;
            display: flex;
            flex-direction: column;
            gap: 7px;
        }}
        .tips-box li {{ font-size: 0.875em; line-height: 1.55; color: #fde68a; }}
        .card-footer {{
            padding: 16px 32px;
            border-top: 1px solid rgba(255,255,255,0.07);
            font-size: 0.78em;
            color: #64748b;
            text-align: center;
        }}
        .card-footer a {{
            color: #93c5fd;
            text-decoration: none;
            font-weight: 600;
        }}
        .card-footer a:hover {{ color: #bfdbfe; }}
        code {{
            background: rgba(255,255,255,0.08);
            border: 1px solid rgba(255,255,255,0.1);
            padding: 1px 6px;
            border-radius: 4px;
            font-family: 'SFMono-Regular', 'Consolas', 'Monaco', monospace;
            font-size: 0.9em;
            color: #a5b4fc;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="resource-bar">
            <span class="resource-title">MasterHttpRelayVPN</span>
            <a class="resource-link" href="https://github.com/masterking32/MasterHttpRelayVPN" target="_blank" rel="noopener noreferrer">GitHub Repo</a>
            <a class="resource-link" href="https://t.me/MasterDnsVPN" target="_blank" rel="noopener noreferrer">Telegram Channel</a>
            <a class="resource-link" href="https://t.me/MasterDnsVPNGroup" target="_blank" rel="noopener noreferrer">Telegram Group</a>
        </div>
        <div class="card-header">
            <div class="badge"><svg width="28" height="28" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M12 2L2 20h20L12 2z" stroke="#f87171" stroke-width="2" stroke-linejoin="round"/><line x1="12" y1="10" x2="12" y2="14" stroke="#f87171" stroke-width="2" stroke-linecap="round"/><circle cx="12" cy="17" r="1" fill="#f87171"/></svg></div>
            <div>
                <h1>502 Bad Gateway</h1>
                <p>The relay could not complete your request</p>
            </div>
        </div>
        <div class="card-body">
            <div class="error-box">
                <div class="label">Error Details</div>
                <div id="error-text">{safe_message}</div>
            </div>
            <div class="hints-box">
                <div class="label">How to Fix</div>
                <div id="hints-content">
                    <ol>
                        <li><strong>Check Deployment ID</strong> in config.json matches Google Apps Script</li>
                        <li><strong>Verify AUTH_KEY</strong> matches in Code.gs and config.json</li>
                        <li><strong>Create NEW deployment</strong> if Code.gs was edited</li>
                        <li><strong>Confirm permissions:</strong> Execute as Me, Anyone can access</li>
                        <li><strong>Check quota</strong> — 20,000 requests/day limit</li>
                    </ol>
                </div>
            </div>
            <div class="tips-box">
                <div class="label">General Tips</div>
                <ul>
                    <li>Check the proxy terminal for detailed logs</li>
                    <li>Test internet connectivity: open <a href="https://eth0.me" target="_blank" rel="noopener noreferrer"><code>eth0.me</code></a> in your browser</li>
                    <li>Scan for a working Google IP: <code>python main.py --scan</code></li>
                    <li>Deploy multiple Apps Script projects as backup</li>
                </ul>
            </div>
        </div>
        <div class="card-footer">
            Read <a href="https://github.com/masterking32/MasterHttpRelayVPN/blob/python_testing/docs/TROUBLESHOOTING.md" target="_blank" rel="noopener noreferrer">Troubleshooting Guide</a> or run <code>python setup.py</code> for full setup help
        </div>
    </div>

    <script>
        (function () {{
            const rawMsg = {js_message};
            const lower = rawMsg.toLowerCase();
            const container = document.getElementById('hints-content');
            if (!container) return;

            let hints = '';

            if (lower.includes('dns')) {{
                hints = `<ol>
                    <li><strong>Check the exit node URL</strong> spelling in config.json</li>
                    <li><strong>Verify the domain exists</strong> and is publicly reachable</li>
                    <li><strong>Disable exit node temporarily:</strong> set <code>exit_node.enabled</code> to false</li>
                    <li><strong>Common typo:</strong> "workefrs" → "workers"</li>
                    <li><strong>Test in browser:</strong> open the exit node URL directly</li>
                </ol>`;
            }} else if (lower.includes('auth') || lower.includes('unauthorized')) {{
                hints = `<ol>
                    <li><strong>Copy AUTH_KEY</strong> exactly from Code.gs</li>
                    <li><strong>Paste into config.json</strong> — must match character-for-character</li>
                    <li><strong>Create a NEW deployment</strong> after any change</li>
                    <li><strong>Permissions:</strong> Execute as Me, access Anyone</li>
                    <li><strong>Authorize the script</strong> by running it manually once</li>
                </ol>`;
            }} else if (lower.includes('quota') || lower.includes('too many') || lower.includes('service invoked')) {{
                hints = `<ol>
                    <li><strong>Daily limit hit:</strong> 20,000 URL-fetch calls/day</li>
                    <li><strong>Wait for reset</strong> — quotas reset at midnight UTC</li>
                    <li><strong>Add more scripts:</strong> deploy 2–3 separate projects</li>
                    <li><strong>Use script_ids array</strong> in config.json for load balancing</li>
                    <li><strong>Restart proxy</strong> after updating config</li>
                </ol>`;
            }} else if (lower.includes('html') || lower.includes('deployment') || lower.includes('not_found')) {{
                hints = `<ol>
                    <li><strong>Use the Deployment ID</strong>, not the Script ID</li>
                    <li><strong>Check the deployment is active</strong> (not archived)</li>
                    <li><strong>Create a NEW deployment</strong> after editing Code.gs</li>
                    <li><strong>Permissions:</strong> Execute as Me, access Anyone</li>
                    <li><strong>Verify script_id</strong> in config.json is correct</li>
                </ol>`;
            }} else {{
                hints = `<ol>
                    <li><strong>Verify Deployment ID</strong> matches Google Apps Script</li>
                    <li><strong>Check AUTH_KEY</strong> matches in both places</li>
                    <li><strong>Create NEW deployment</strong> if Code.gs was edited</li>
                    <li><strong>Confirm permissions:</strong> Me + Anyone access</li>
                    <li><strong>Check proxy terminal</strong> for more details</li>
                </ol>`;
            }}

            container.innerHTML = hints;

            // Keep error details text-only to avoid any HTML/script injection path.
        }})();
    </script>
</body>
</html>'''


def error_response(status: int, message: str) -> bytes:
    """Build an HTML error response. For 502 errors, includes troubleshooting guide."""
    if status == 502:
        body = _build_502_html(message)
    else:
        body = f"<html><body><h1>{status}</h1><p>{message}</p></body></html>"

    return (
        f"HTTP/1.1 {status} Error\r\n"
        f"Content-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(body.encode())}\r\n"
        f"\r\n"
        f"{body}"
    ).encode()


def split_raw_response(raw: bytes):
    """Split a raw HTTP response into ``(status, headers_dict, body)``."""
    if b"\r\n\r\n" not in raw:
        return 0, {}, raw
    header_section, body = raw.split(b"\r\n\r\n", 1)
    lines = header_section.split(b"\r\n")
    m = re.search(r"\d{3}", lines[0].decode(errors="replace"))
    status = int(m.group()) if m else 0
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if b":" in line:
            k, v = line.decode(errors="replace").split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return status, headers, body


def split_set_cookie(blob: str) -> list[str]:
    """Split a Set-Cookie string that may contain multiple cookies.

    Apps Script sometimes joins multiple Set-Cookie values with ", ",
    which collides with the comma that legitimately appears inside the
    ``Expires`` attribute (e.g. "Expires=Wed, 21 Oct 2026 ..."). We split
    only on commas that are immediately followed by a cookie name=value
    pair, leaving date commas intact.
    """
    if not blob:
        return []
    parts = re.split(r",\s*(?=[A-Za-z0-9!#$%&'*+\-.^_`|~]+=)", blob)
    return [p.strip() for p in parts if p.strip()]


def _target_content_encoding(headers: dict) -> str:
    for key, value in headers.items():
        if str(key).lower() == "content-encoding":
            return str(value).strip().lower()
    return ""


def _looks_like_plain_body(body: bytes) -> bool:
    if not body:
        return True

    sample = body[:512].lstrip()
    lower = sample[:64].lower()
    if lower.startswith((
        b"<!doctype",
        b"<html",
        b"<head",
        b"<body",
        b"<?xml",
        b"{",
        b"[",
        b"function",
        b"var ",
        b"let ",
        b"const ",
        b"/*",
        b"//",
    )):
        return True
    if b"\x00" in sample[:128]:
        return False

    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _decode_target_body(body: bytes, encoding: str) -> tuple[bytes, bool]:
    encodings = []
    for part in (encoding or "").lower().split(","):
        layer = part.strip()
        if not layer or layer == "identity":
            continue
        encodings.append("deflate" if layer == "zlib" else layer)
    if not body or not encodings:
        return body, False

    # Apps Script's UrlFetchApp transparently decompresses gzip/br/deflate
    # responses but preserves the original Content-Encoding header in the
    # forwarded metadata. The body therefore arrives as already-plain bytes
    # while still being labelled (e.g.) "br". Detect that case up front so
    # we don't pay the CPU cost of a guaranteed-failing brotli/zstd decode
    # on every relayed response.
    if _looks_like_plain_body(body):
        return body, True

    decoded = body
    for layer in reversed(encodings):
        before = decoded
        decoded = codec.decode(before, layer)
        if decoded == before:
            if _looks_like_plain_body(body):
                log.debug("dropping stale target content-encoding (%s)", encoding)
                return body, True
            log.debug("preserving target content-encoding (%s)", encoding)
            return body, False
    return decoded, True


# ── JSON → HTTP response ─────────────────────────────────────────────────────

def parse_relay_json(data: dict, max_body_bytes: int) -> bytes:
    """Convert a parsed relay JSON dict to raw HTTP response bytes."""
    if "e" in data:
        raw_err = str(data["e"])
        friendly = classify_relay_error(raw_err)
        log.warning("Apps Script error — %s | raw: %s", friendly.split(".")[0], raw_err)
        return error_response(502, friendly)

    status = data.get("s", 200)
    resp_headers = data.get("h", {})
    resp_body = base64.b64decode(data.get("b", ""))

    # Decompress relay-level gzip applied by Code.gs to shrink bytes-on-wire
    # over DPI-shaped connections.  The "gz" flag is set when Code.gs found
    # that gzip saved space (all text content: JS, CSS, HTML, JSON).
    if data.get("gz"):
        try:
            resp_body = gzip.decompress(resp_body)
        except Exception as _exc:
            log.debug("relay gz decompress failed: %s", _exc)

    # UrlFetchApp and some exit-node hosts can pass through compressed target
    # bodies. Decode only when confirmed; otherwise preserve Content-Encoding so
    # the browser does not receive compressed bytes labeled as plain text.
    target_encoding = _target_content_encoding(resp_headers)
    resp_body, target_body_decoded = _decode_target_body(resp_body, target_encoding)
    if len(resp_body) > max_body_bytes:
        return error_response(
            502,
            f"Relay response exceeds cap ({max_body_bytes} bytes). "
            "Increase MAX_RESPONSE_BODY_BYTES in src/core/constants.py if your system has enough RAM.",
        )

    status_text = {
        200: "OK", 206: "Partial Content",
        301: "Moved", 302: "Found", 304: "Not Modified",
        400: "Bad Request", 403: "Forbidden", 404: "Not Found",
        500: "Internal Server Error",
    }.get(status, "OK")
    result = f"HTTP/1.1 {status} {status_text}\r\n"

    skip = {"transfer-encoding", "connection", "keep-alive", "content-length"}
    if target_body_decoded:
        skip.add("content-encoding")
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
            expanded: list[str] = []
            for item in values:
                expanded.extend(split_set_cookie(str(item)))
            values = expanded
        for val in values:
            result += f"{k}: {val}\r\n"
    result += f"Content-Length: {len(resp_body)}\r\n"
    result += "\r\n"
    return result.encode() + resp_body


def extract_apps_script_user_html(text: str) -> str | None:
    """Extract embedded user HTML from an Apps Script HTML-page response.

    Google's IFRAME_SANDBOX mode returns /exec responses wrapped in an HTML
    page that includes a goog.script.init("...") call. The first argument is
    a JS string literal (\\xNN hex escapes) containing a JSON payload with
    a ``userHtml`` field that holds the actual relay response.
    """
    marker = 'goog.script.init("'
    start = text.find(marker)
    if start == -1:
        return None
    start += len(marker)
    end = text.find('", "", undefined', start)
    if end == -1:
        return None

    encoded = text[start:end]
    try:
        # The JS string uses \xNN hex escapes and \/ for forward-slash.
        # Also unescape \\ → \ (JS double-backslash = literal backslash).
        # Order: hex first, then double-backslash, then \/ so that
        # \\/ (JS for literal-backslash + /) works correctly.
        decoded = re.sub(
            r'\\x([0-9a-fA-F]{2})',
            lambda m: chr(int(m.group(1), 16)),
            encoded,
        )
        decoded = decoded.replace("\\\\", "\\")
        decoded = decoded.replace("\\/", "/")
        payload = json.loads(decoded)
    except Exception:
        return None

    user_html = payload.get("userHtml")
    return user_html if isinstance(user_html, str) else None


def load_relay_json(text: str) -> dict | None:
    """Parse a relay JSON body, handling Apps Script HTML wrappers."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        wrapped = extract_apps_script_user_html(text)
        if wrapped:
            data = load_relay_json(wrapped)
            if data is not None:
                return data

        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None


def parse_relay_response(body: bytes, max_body_bytes: int) -> bytes:
    """Parse a raw Apps Script response body into a raw HTTP response.

    ``body`` is the bytes returned over the TLS connection after stripping
    the outer HTTP/1.1 response headers. The function:

    1. Decodes the JSON envelope produced by Code.gs.
    2. Unpacks the nested status / headers / base64-body fields.
    3. Reconstructs a well-formed HTTP/1.1 response suitable for
       forwarding directly to the browser.
    """
    text = body.decode(errors="replace").strip()
    if not text:
        return error_response(502, "Empty response from relay")

    data = load_relay_json(text)
    if data is None:
        # Provide a better error message based on what the response looks like
        preview = text[:1000]  # Larger preview to catch errors in HTML
        preview_lower = preview.lower()

        # Check for specific error patterns FIRST, regardless of format
        if "dns" in preview_lower:
            # Extract just the DNS error portion from the text
            dns_match = re.search(r'(dns[^<\n]{0,200})', preview, re.IGNORECASE)
            raw_hint = dns_match.group(1).strip() if dns_match else ""
            error_msg = (
                "DNS error in exit node. "
                "The exit node URL in config.json might be misspelled or unreachable. "
                "Check: (1) exit_node_url is correctly set, "
                "(2) the domain can be resolved, "
                "(3) your network can reach that exit node."
                + (f" [{raw_hint}]" if raw_hint else "")
            )
        elif "cloudflare" in preview_lower or "cf_challenge" in preview_lower:
            error_msg = (
                "Cloudflare error from exit node. "
                "The exit node URL (Cloudflare Worker) returned a Cloudflare error page. "
                "Verify: (1) exit_node.url is correctly set in config.json, "
                "(2) the Cloudflare Worker deployment is working, "
                "(3) try disabling exit node: set exit_node.enabled to false"
            )
        elif preview_lower.startswith("<"):
            # HTML response from script.google.com usually indicates that
            # Deployment ID is wrong/archived or the deployment was not updated.
            # Match only Apps Script-specific wrappers to avoid false positives
            # when the destination site itself is RTL or hosted on docs.google.com.
            if any(sig in preview_lower for sig in (
                "web word processing, presentations and spreadsheets",
                "goog.script.init",
                "script.google.com/macros",
                "/macros/s/",
            )):
                error_msg = (
                    "Wrong Apps Script deployment (script_id). "
                    "Google returned a generic HTML page instead of relay JSON. "
                    "Fix: (1) use Web App Deployment ID (not Script ID), "
                    "(2) confirm deployment is active/not archived, "
                    "(3) redeploy after editing Code.gs, "
                    "(4) update script_id in config.json."
                )
            else:
                error_msg = (
                    "Apps Script returned HTML instead of JSON. "
                    "This usually means: (1) wrong Deployment ID, "
                    "(2) the deployment doesn't exist or is archived, "
                    "(3) Code.gs wasn't deployed or has syntax errors. "
                    "Try: Create a NEW deployment and update script_id in config.json"
                )
        elif "exception" in preview_lower or "error" in preview_lower:
            # Looks like an error message - use it directly
            error_msg = f"Relay returned an error: {preview[:200]}"
        else:
            error_msg = f"Invalid JSON response from relay: {preview[:200]}"

        log.warning("JSON parse failed. Response: %s", preview[:200])
        return error_response(502, error_msg)

    return parse_relay_json(data, max_body_bytes)
