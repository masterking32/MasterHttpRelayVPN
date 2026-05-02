"""
SOCKS5 protocol negotiation helpers.

Implements the RFC 1928 handshake for CONNECT (TCP BIND) requests.
Only no-authentication (method 0x00) and CONNECT (cmd 0x01) are supported,
which covers all standard proxy use cases (HTTPS, HTTP, arbitrary TCP).

Usage::

    host, port = await negotiate_socks5(reader, writer)
    # host/port are None if negotiation failed (caller should close)
"""

from __future__ import annotations

import asyncio
import socket

__all__ = ["negotiate_socks5"]


async def negotiate_socks5(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> tuple[str, int] | None:
    """Perform a SOCKS5 handshake and return the requested (host, port).

    Sends protocol-level replies directly to *writer*.  Returns ``None``
    and leaves the connection in a closed state if negotiation fails at
    any step (unsupported version, method, command, or address type).

    Raises:
        asyncio.IncompleteReadError: if the client closes the connection
            mid-handshake.
        asyncio.TimeoutError: propagated from the individual ``wait_for``
            calls so the caller can log it separately.
    """
    # ── Auth negotiation ──────────────────────────────────────────
    header = await asyncio.wait_for(reader.readexactly(2), timeout=15)
    ver, nmethods = header[0], header[1]
    if ver != 5:
        return None

    methods = await asyncio.wait_for(reader.readexactly(nmethods), timeout=10)
    if 0x00 not in methods:
        # No acceptable method — reject
        writer.write(b"\x05\xff")
        await writer.drain()
        return None

    # Accept: no authentication required
    writer.write(b"\x05\x00")
    await writer.drain()

    # ── Request ───────────────────────────────────────────────────
    req = await asyncio.wait_for(reader.readexactly(4), timeout=15)
    ver, cmd, _rsv, atyp = req
    if ver != 5 or cmd != 0x01:
        # Only CONNECT (0x01) is supported
        writer.write(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
        await writer.drain()
        return None

    # ── Address parsing ───────────────────────────────────────────
    if atyp == 0x01:  # IPv4
        raw = await asyncio.wait_for(reader.readexactly(4), timeout=10)
        host = socket.inet_ntoa(raw)
    elif atyp == 0x03:  # Domain name
        ln = (await asyncio.wait_for(reader.readexactly(1), timeout=10))[0]
        host = (
            await asyncio.wait_for(reader.readexactly(ln), timeout=10)
        ).decode(errors="replace")
    elif atyp == 0x04:  # IPv6
        raw = await asyncio.wait_for(reader.readexactly(16), timeout=10)
        host = socket.inet_ntop(socket.AF_INET6, raw)
    else:
        writer.write(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
        await writer.drain()
        return None

    port_raw = await asyncio.wait_for(reader.readexactly(2), timeout=10)
    port = int.from_bytes(port_raw, "big")

    # ── Success reply ─────────────────────────────────────────────
    writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
    await writer.drain()

    return host, port
