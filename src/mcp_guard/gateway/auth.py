"""Checks every gateway request must pass before anything else happens.

1. Secret token in the URL path (compared in constant time). Wrong token: 404, so
   the gateway does not even admit that it exists.
2. Origin header: browsers always send one. A request from some other web page
   (for example DNS rebinding) is refused. Requests without Origin are fine.
3. Client IP must be in the allowed ranges (default: Anthropic's outbound ranges).
   Behind Cloudflare Tunnel every request comes from 127.0.0.1, so the real IP is
   read from the CF-Connecting-IP header, but only when the peer is local.

The IP check alone is not enough, because every Anthropic customer shares those
IPs. The token is the real secret.
"""
from __future__ import annotations

import hmac
import ipaddress
from typing import Iterable, Mapping

LOCAL_PEERS = {"127.0.0.1", "::1"}
PROXY_IP_HEADER = "CF-Connecting-IP"
ALLOWED_ORIGINS = {"https://claude.ai", "https://claude.com"}

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def token_matches(given: str | None, expected: str | None) -> bool:
    if not given or not expected:
        return False
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def origin_allowed(origin: str | None) -> bool:
    return origin is None or origin in ALLOWED_ORIGINS


def client_ip(peer: str | None, headers: Mapping[str, str], trust_proxy_header: bool) -> str | None:
    if trust_proxy_header and peer in LOCAL_PEERS:
        forwarded = headers.get(PROXY_IP_HEADER)
        if forwarded:
            return forwarded.strip()
    return peer


def parse_networks(ranges: Iterable[str]) -> list[Network]:
    return [ipaddress.ip_network(r, strict=False) for r in ranges]


def ip_allowed(ip: str | None, networks: list[Network]) -> bool:
    if not ip:
        return False
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in net for net in networks)
