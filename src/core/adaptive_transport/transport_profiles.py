from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TransportProfile:
    name: str
    protocol: str
    fallback: tuple[str, ...]
    browser_fingerprint: str


PROFILES = {
    "vless_reality": TransportProfile("vless_reality", "tcp+tls", ("h2", "h3", "udp_over_tcp"), "chrome_124"),
    "http2_fallback": TransportProfile("http2_fallback", "h2", ("h1",), "firefox_125"),
    "http3_fallback": TransportProfile("http3_fallback", "h3", ("h2",), "chrome_124"),
    "udp_over_tcp": TransportProfile("udp_over_tcp", "tcp", ("h2",), "safari_17"),
    "quic": TransportProfile("quic", "udp", ("h3", "h2"), "chrome_124"),
}
