from __future__ import annotations

import ipaddress


def validate_public_ip(ip: str) -> str:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError as exc:
        raise ValueError(f"invalid ip: {ip}") from exc

    if (
        addr.is_private
        or addr.is_multicast
        or addr.is_loopback
        or addr.is_reserved
        or addr.is_unspecified
        or addr.is_link_local
    ):
        raise ValueError(f"non-public ip rejected: {ip}")
    return ip
