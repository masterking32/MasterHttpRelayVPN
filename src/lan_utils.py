"""
LAN utilities for detecting network interfaces and IP addresses.

Provides functionality to enumerate local network interfaces and their
associated IP addresses for LAN proxy sharing.
"""

import ipaddress
import logging
import socket
from typing import Dict, List, Optional

log = logging.getLogger("LAN")


def get_network_interfaces() -> Dict[str, List[str]]:
    """
    Get all network interfaces and their associated IP addresses.

    Returns a dictionary mapping interface names to lists of IP addresses
    (both IPv4 and IPv6). Only includes interfaces with valid IP addresses
    that are not loopback.

    Returns:
        Dict[str, List[str]]: Interface name -> list of IP addresses
    """
    interfaces = {}

    try:
        import netifaces
        for iface in netifaces.interfaces():
            addrs = netifaces.ifaddresses(iface)
            ips = []
            # IPv4 addresses
            if netifaces.AF_INET in addrs:
                for addr in addrs[netifaces.AF_INET]:
                    ip = addr.get('addr')
                    if ip and not ip.startswith('127.'):
                        ips.append(ip)
            # IPv6 addresses (without scope)
            if netifaces.AF_INET6 in addrs:
                for addr in addrs[netifaces.AF_INET6]:
                    ip = addr.get('addr')
                    if ip and not ip.startswith('::1') and not '%' in ip:
                        # Remove scope if present
                        ips.append(ip.split('%')[0])
            if ips:
                interfaces[iface] = ips
    except ImportError:
        # Fallback to socket method for basic detection
        log.debug("netifaces not available, using socket fallback")
        interfaces = _get_interfaces_socket_fallback()

    return interfaces


def _get_interfaces_socket_fallback() -> Dict[str, List[str]]:
    """
    Fallback method to get network interfaces using socket.

    This is less comprehensive than netifaces but works without extra dependencies.
    """
    interfaces = {}

    try:
        # Get hostname and try to resolve to IPs
        hostname = socket.gethostname()
        try:
            # Get IPv4 addresses
            ipv4_info = socket.getaddrinfo(hostname, None, socket.AF_INET)
            ipv4_addrs = [info[4][0] for info in ipv4_info if not info[4][0].startswith('127.')]
            if ipv4_addrs:
                interfaces['primary'] = list(set(ipv4_addrs))  # Remove duplicates
        except socket.gaierror:
            pass

        try:
            # Get IPv6 addresses
            ipv6_info = socket.getaddrinfo(hostname, None, socket.AF_INET6)
            ipv6_addrs = []
            for info in ipv6_info:
                ip = info[4][0]
                if not ip.startswith('::1') and not '%' in ip:
                    ipv6_addrs.append(ip.split('%')[0])
            if ipv6_addrs:
                interfaces['primary_ipv6'] = list(set(ipv6_addrs))
        except socket.gaierror:
            pass

    except Exception as e:
        log.debug("Socket fallback failed: %s", e)

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
    lan_addresses = []

    for iface_ips in interfaces.values():
        for ip in iface_ips:
            try:
                # Validate IP and check if it's a private address
                addr = ipaddress.ip_address(ip)
                if addr.is_private or addr.is_link_local:
                    lan_addresses.append(f"{ip}:{port}")
            except ValueError:
                continue

    # Remove duplicates while preserving order
    seen = set()
    unique_addresses = []
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
