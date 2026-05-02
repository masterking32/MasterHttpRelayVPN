"""
HTTP/1.1 response reader for keep-alive connections.

Reads exactly one HTTP response from an asyncio StreamReader, handling
chunked transfer-encoding, Content-Length framing, and streaming bodies.
Auto-decompresses the response body according to the Content-Encoding
header (gzip, deflate, brotli, zstd).

Usage::

    status, headers, body = await read_http_response(reader, max_bytes=50_000_000)
"""

from __future__ import annotations

import asyncio
import re

from core import codec

__all__ = ["read_http_response"]


async def read_http_response(
    reader: asyncio.StreamReader,
    *,
    max_bytes: int,
) -> tuple[int, dict[str, str], bytes]:
    """Read one HTTP/1.1 response. Keep-alive safe (no read-until-EOF).

    Args:
        reader:    An ``asyncio.StreamReader`` positioned at the start of
                   an HTTP response.
        max_bytes: Hard cap on the decompressed body size.  Raises
                   ``RuntimeError`` if exceeded.

    Returns:
        A ``(status_code, headers, body)`` triple.  ``status_code`` is 0
        and the other fields are empty/empty if the response is malformed.
    """
    # ── Read until header boundary ────────────────────────────────
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

    status_line = lines[0].decode(errors="replace")
    m = re.search(r"\d{3}", status_line)
    status = int(m.group()) if m else 0

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if b":" in line:
            k, v = line.decode(errors="replace").split(":", 1)
            headers[k.strip().lower()] = v.strip()

    # ── Body framing ──────────────────────────────────────────────
    content_length = headers.get("content-length")
    transfer_encoding = headers.get("transfer-encoding", "")

    if "chunked" in transfer_encoding:
        body = await _read_chunked(reader, body, max_bytes=max_bytes)
    elif content_length:
        total = int(content_length)
        if total > max_bytes:
            raise RuntimeError(
                "Relay response exceeds configured size cap "
                f"({total} > {max_bytes} bytes)"
            )
        remaining = total - len(body)
        while remaining > 0:
            chunk = await asyncio.wait_for(
                reader.read(min(remaining, 65536)), timeout=20
            )
            if not chunk:
                break
            body += chunk
            if len(body) > max_bytes:
                raise RuntimeError(
                    "Relay response exceeded configured size cap while reading body"
                )
            remaining -= len(chunk)
    else:
        # No framing — short timeout read (keep-alive safe)
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(65536), timeout=2)
                if not chunk:
                    break
                body += chunk
                if len(body) > max_bytes:
                    raise RuntimeError(
                        "Relay response exceeded configured size cap while streaming"
                    )
            except asyncio.TimeoutError:
                break

    # ── Auto-decompress ───────────────────────────────────────────
    enc = headers.get("content-encoding", "")
    if enc:
        body = codec.decode(body, enc)
        if len(body) > max_bytes:
            raise RuntimeError(
                "Decoded relay response exceeded configured size cap"
            )

    return status, headers, body


async def _read_chunked(
    reader: asyncio.StreamReader,
    buf: bytes = b"",
    *,
    max_bytes: int,
) -> bytes:
    """Incrementally read a chunked-transfer-encoded body."""
    result = b""
    while True:
        while b"\r\n" not in buf:
            data = await asyncio.wait_for(reader.read(8192), timeout=20)
            if not data:
                return result
            buf += data

        end = buf.find(b"\r\n")
        size_str = buf[:end].decode(errors="replace").strip()
        buf = buf[end + 2:]

        if not size_str:
            continue
        try:
            size = int(size_str, 16)
        except ValueError:
            break
        if size == 0:
            break
        if size > max_bytes or len(result) + size > max_bytes:
            raise RuntimeError(
                "Chunked relay response exceeded configured size cap "
                f"({max_bytes} bytes)"
            )

        while len(buf) < size + 2:
            data = await asyncio.wait_for(reader.read(65536), timeout=20)
            if not data:
                result += buf[:size]
                return result
            buf += data

        result += buf[:size]
        buf = buf[size + 2:]

    return result
