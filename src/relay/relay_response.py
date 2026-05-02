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
import codecs
import json
import logging
import re

log = logging.getLogger("Fronter")

__all__ = [
    "classify_relay_error",
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


# ── Error classifier ──────────────────────────────────────────────────────────

def classify_relay_error(raw: str) -> str:
    """Return a human-readable explanation for a known Apps Script error.

    Covers every error category documented at:
    developers.google.com/apps-script/guides/support/troubleshooting
    """
    lower = raw.lower()

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

    # Unknown — strip the leading 'Exception: ' / 'Error: ' prefix that
    # Apps Script always prepends, so the message is shorter and cleaner.
    cleaned = re.sub(r'^(Exception|Error):\s*', '', raw, flags=re.IGNORECASE).strip()
    return f"Relay error from Apps Script: {cleaned or raw}"


# ── Low-level HTTP helpers ────────────────────────────────────────────────────

def error_response(status: int, message: str) -> bytes:
    """Build a minimal HTML error response."""
    body = f"<html><body><h1>{status}</h1><p>{message}</p></body></html>"
    return (
        f"HTTP/1.1 {status} Error\r\n"
        f"Content-Type: text/html\r\n"
        f"Content-Length: {len(body)}\r\n"
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
    if len(resp_body) > max_body_bytes:
        return error_response(
            502,
            f"Relay response exceeds cap ({max_body_bytes} bytes). "
            "Increase max_response_body_bytes if your system has enough RAM.",
        )

    status_text = {
        200: "OK", 206: "Partial Content",
        301: "Moved", 302: "Found", 304: "Not Modified",
        400: "Bad Request", 403: "Forbidden", 404: "Not Found",
        500: "Internal Server Error",
    }.get(status, "OK")
    result = f"HTTP/1.1 {status} {status_text}\r\n"

    skip = {"transfer-encoding", "connection", "keep-alive",
            "content-length", "content-encoding"}
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
    """Extract embedded user HTML from an Apps Script HTML-page response."""
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
        decoded = codecs.decode(encoded, "unicode_escape")
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
        return error_response(502, f"No JSON: {text[:200]}")

    return parse_relay_json(data, max_body_bytes)
