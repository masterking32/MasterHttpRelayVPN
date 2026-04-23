"""
LAN utilities for detecting network interfaces and IP addresses.

Provides functionality to enumerate local network interfaces and their
associated IP addresses for LAN proxy sharing.

Implementation notes
--------------------
This module intentionally relies only on the Python standard library so
that it works out-of-the-box on every supported OS (Windows, Linux,
macOS, Android/Termux, *BSD) without requiring a C compiler or native
build tools (previous versions depended on ``netifaces``, which needs
"Microsoft Visual C++ 14.0 or greater" on Windows and was a frequent
install blocker for users on slow connections).

Strategy (in order):
1. "UDP connect trick" to reliably discover the primary outbound
   IPv4 and IPv6 addresses on any OS.
2. ``socket.getaddrinfo(hostname, ...)`` to enumerate additional
   addresses bound to the host (covers multi-homed machines).

These two steps together cover every real-world LAN scenario on
Windows, Linux, macOS, Android/Termux and *BSD. (We intentionally do
*not* try to map per-interface names to IPs via the stdlib — that is
not portable and, on Windows, triggers 30 s DNS timeouts.)
"""

import ipaddress
import logging
import socket
from typing import Dict, List, Optional, Set

log = logging.getLogger("LAN")


# ---------------------------------------------------------------------------
# Primary-IP discovery (UDP connect trick)
# ---------------------------------------------------------------------------
def _primary_ip(family: int, probe_addr: str) -> Optional[str]:
    """
    Return the primary local IP the OS would use to reach ``probe_addr``.

    Uses a connected UDP socket which does *not* actually send packets —
    the kernel just picks the source address from its routing table.
    Works identically on Windows, Linux, macOS, and Android.
    """
    s = socket.socket(family, socket.SOCK_DGRAM)
    try:
        s.settimeout(0.5)
        # Port 80 is arbitrary; no packet is sent for UDP connect().
        s.connect((probe_addr, 80))
        ip = s.getsockname()[0]
        if family == socket.AF_INET6:
            ip = ip.split('%', 1)[0]  # strip zone id
        return ip
    except OSError:
        return None
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_network_interfaces() -> Dict[str, List[str]]:
    """
    Get network interfaces and their associated non-loopback IP addresses.

    Returns:
        Dict[str, List[str]]: Interface label -> list of IP addresses.
        Interface names are best-effort; on some platforms we fall back
        to synthetic labels such as ``"primary"`` / ``"primary_ipv6"``.
    """
    interfaces: Dict[str, List[str]] = {}
    seen_ips: Set[str] = set()

    def _add(label: str, ip: Optional[str]) -> None:
        if not ip or ip in seen_ips:
            return
        if ip.startswith('127.') or ip == '::1':
            return
        seen_ips.add(ip)
        interfaces.setdefault(label, []).append(ip)

    # 1) Primary outbound IPs (most reliable, cross-platform).
    _add('primary', _primary_ip(socket.AF_INET, '192.0.2.1'))        # TEST-NET-1
    _add('primary_ipv6', _primary_ip(socket.AF_INET6, '2001:db8::1'))  # doc prefix

    # 2) Enumerate via hostname resolution (picks up multi-homed hosts).
    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = ''

    for family, label in ((socket.AF_INET, 'host'), (socket.AF_INET6, 'host_ipv6')):
        try:
            for info in socket.getaddrinfo(hostname, None, family):
                ip = info[4][0]
                if family == socket.AF_INET6:
                    ip = ip.split('%', 1)[0]
                _add(label, ip)
        except (socket.gaierror, OSError):
            continue

    return interfaces


def get_lan_ips(port: int = 8085) -> List[str]:
    """
    Get list of LAN-accessible proxy addresses.

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
                addr = ipaddress.ip_address(ip)
            except ValueError:
                continue
            if addr.is_loopback or addr.is_unspecified:
                continue
            # Include private, link-local, and unique-local (IPv6 fc00::/7) ranges.
            if addr.is_private or addr.is_link_local:
                bracket = f"[{ip}]" if isinstance(addr, ipaddress.IPv6Address) else ip
                lan_addresses.append(f"{bracket}:{port}")

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
