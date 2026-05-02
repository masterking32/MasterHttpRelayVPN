"""
Content-Encoding decoders: gzip (stdlib), brotli (optional), zstd (optional).

`decode(body, encoding)` returns the decoded bytes, or the original bytes
on any error.  Use `supported_encodings()` to build an Accept-Encoding value.
"""

from __future__ import annotations

import gzip
import logging
import zlib

log = logging.getLogger("Codec")

try:
    import brotli  # type: ignore
    _HAS_BR = True
except ImportError:  # pragma: no cover
    brotli = None    # type: ignore
    _HAS_BR = False

try:
    import zstandard as _zstd  # type: ignore
    _HAS_ZSTD = True
    _ZSTD_DCTX = _zstd.ZstdDecompressor()
except ImportError:  # pragma: no cover
    _zstd = None     # type: ignore
    _HAS_ZSTD = False
    _ZSTD_DCTX = None


__all__ = ["supported_encodings", "has_brotli", "has_zstd", "decode"]


def supported_encodings() -> str:
    """Value for Accept-Encoding that this relay can actually decode."""
    codecs = ["gzip", "deflate"]
    if _HAS_BR:
        codecs.append("br")
    if _HAS_ZSTD:
        codecs.append("zstd")
    return ", ".join(codecs)


def has_brotli() -> bool:
    return _HAS_BR


def has_zstd() -> bool:
    return _HAS_ZSTD


def decode(body: bytes, encoding: str) -> bytes:
    """Decode *body* according to Content-Encoding.

    Returns the original bytes if the encoding is empty, unknown, or
    decompression fails (so the caller can safely pass through).
    """
    if not body:
        return body
    enc = (encoding or "").strip().lower()
    if not enc or enc == "identity":
        return body

    # Multi-coding (rare): "gzip, br" means brotli(gzip(data))
    if "," in enc:
        for layer in reversed([s.strip() for s in enc.split(",") if s.strip()]):
            body = decode(body, layer)
        return body

    try:
        if enc == "gzip":
            return gzip.decompress(body)
        if enc == "deflate":
            try:
                return zlib.decompress(body)
            except zlib.error:
                # Some servers send raw deflate without zlib wrapper.
                return zlib.decompress(body, -zlib.MAX_WBITS)
        if enc == "br":
            if not _HAS_BR:
                log.debug("brotli not installed — body passed through")
                return body
            return brotli.decompress(body)
        if enc == "zstd":
            if not _HAS_ZSTD:
                log.debug("zstandard not installed — body passed through")
                return body
            return _ZSTD_DCTX.decompress(body)
    except Exception as exc:
        log.debug("decompress (%s) failed: %s — returning raw", enc, exc)
        return body

    return body
