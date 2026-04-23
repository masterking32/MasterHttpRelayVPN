"""
LAN utilities for detecting network interfaces and IPv4 addresses.

Provides functionality to enumerate local IPv4 addresses for LAN proxy
sharing. IPv6 is intentionally not reported — this project only exposes
the proxy over IPv4 LANs, which is what every consumer router and
phone/desktop client actually uses.

Implementation notes
--------------------
This module relies only on the Python standard library so it works
out-of-the-box on every supported OS (Windows, Linux, macOS,
Android/Termux, *BSD) without requiring a C compiler or native build
tools (previous versions depended on ``netifaces``, which needs
"Microsoft Visual C++ 14.0 or greater" on Windows and was a frequent
install blocker for users on slow connections).

Strategy (in order):
1. "UDP connect trick" to reliably discover the primary outbound
   IPv4 address on any OS.
2. ``socket.getaddrinfo(hostname, AF_INET)`` to enumerate any additional
   IPv4 addresses bound to the host (covers multi-homed machines).
"""

import ipaddress
import logging
import socket
from typing import Dict, List, Optional, Set

log = logging.getLogger("LAN")


# ---------------------------------------------------------------------------
# Primary-IP discovery (UDP connect trick)
# ---------------------------------------------------------------------------
def _primary_ipv4() -> Optional[str]:
    """
    Return the primary local IPv4 the OS would use for outbound traffic.

    Uses a connected UDP socket which does *not* actually send packets —
    the kernel just picks the source address from its routing table.
    Works identically on Windows, Linux, macOS, and Android.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.settimeout(0.5)
        # TEST-NET-1 address, port is arbitrary; no packet is sent for UDP connect().
        s.connect(('192.0.2.1', 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_network_interfaces() -> Dict[str, List[str]]:
    """
    Get network interfaces and their associated non-loopback IPv4 addresses.

    Returns:
        Dict[str, List[str]]: Interface label -> list of IPv4 addresses.
        Labels are best-effort synthetic names such as ``"primary"``
        and ``"host"``.
    """
    interfaces: Dict[str, List[str]] = {}
    seen_ips: Set[str] = set()

    def _add(label: str, ip: Optional[str]) -> None:
        if not ip or ip in seen_ips:
            return
        if ip.startswith('127.'):
            return
        seen_ips.add(ip)
        interfaces.setdefault(label, []).append(ip)

    # 1) Primary outbound IPv4 (most reliable, cross-platform).
    _add('primary', _primary_ipv4())

    # 2) Enumerate via hostname resolution (picks up multi-homed hosts).
    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = ''

    if hostname:
        try:
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                _add('host', info[4][0])
        except (socket.gaierror, OSError):
            pass

    return interfaces


def get_lan_ips(port: int = 8085) -> List[str]:
    """
    Get list of LAN-accessible proxy addresses (IPv4 only).

    Returns a list of IP:port combinations that can be used to access
    the proxy from other devices on the local network.

    Args:
        port: The port the proxy is listening on

    Returns:
        List[str]: List of "IP:port" strings for LAN access
    """
    interfaces = get_network_interfaces()
    lan_addresses: List[str] = []

    for iface_ips in interfaces.values():
        for ip in iface_ips:
            try:
                addr = ipaddress.IPv4Address(ip)
            except (ValueError, ipaddress.AddressValueError):
                continue
            if addr.is_loopback or addr.is_unspecified:
                continue
            if addr.is_private or addr.is_link_local:
                lan_addresses.append(f"{ip}:{port}")

    # Remove duplicates while preserving order.
    seen: Set[str] = set()
    unique_addresses: List[str] = []
    for addr in lan_addresses:
        if addr not in seen:
            seen.add(addr)
            unique_addresses.append(addr)

    return unique_addresses


def log_lan_access(port: int = 8085, socks_port: Optional[int] = None):
    """
    Log the LAN-accessible proxy addresses for user convenience.

    Args:
        port: HTTP proxy port
        socks_port: Optional SOCKS5 proxy port
    """
    lan_http = get_lan_ips(port)
    if lan_http:
        log.info("LAN HTTP proxy   : %s", ", ".join(lan_http))
    else:
        log.warning("No LAN IP addresses detected for HTTP proxy")

    if socks_port:
        lan_socks = get_lan_ips(socks_port)
        if lan_socks:
            log.info("LAN SOCKS5 proxy : %s", ", ".join(lan_socks))
        else:
            log.warning("No LAN IP addresses detected for SOCKS5 proxy")
