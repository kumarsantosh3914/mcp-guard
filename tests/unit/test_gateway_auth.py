import pytest

from mcp_guard.config import ANTHROPIC_OUTBOUND_RANGES
from mcp_guard.gateway import auth

NETS = auth.parse_networks(ANTHROPIC_OUTBOUND_RANGES)


def test_token():
    assert auth.token_matches("a" * 40, "a" * 40)
    assert not auth.token_matches("a" * 40, "b" * 40)
    assert not auth.token_matches(None, "a" * 40)
    assert not auth.token_matches("a" * 40, None)
    assert not auth.token_matches("", "")


@pytest.mark.parametrize("origin, ok", [
    (None, True),
    ("https://claude.ai", True),
    ("https://claude.com", True),
    ("https://evil.example", False),
    ("http://claude.ai", False),
    ("null", False),
])
def test_origin(origin, ok):
    assert auth.origin_allowed(origin) is ok


def test_client_ip_uses_tunnel_header_only_from_local_peer():
    h = {"CF-Connecting-IP": "160.79.104.10"}
    assert auth.client_ip("127.0.0.1", h, True) == "160.79.104.10"
    assert auth.client_ip("::1", h, True) == "160.79.104.10"
    assert auth.client_ip("8.8.8.8", h, True) == "8.8.8.8"  # header from a remote peer is ignored
    assert auth.client_ip("127.0.0.1", h, False) == "127.0.0.1"
    assert auth.client_ip("127.0.0.1", {}, True) == "127.0.0.1"


@pytest.mark.parametrize("ip, ok", [
    ("160.79.104.1", True),
    ("160.79.111.254", True),
    ("153.61.208.5", True),
    ("153.61.223.255", True),
    ("160.79.112.1", False),
    ("127.0.0.1", False),
    ("8.8.8.8", False),
    ("2607:6bc0::1", False),  # inbound range, not outbound
    ("not an ip", False),
    (None, False),
])
def test_ip_allowed(ip, ok):
    assert auth.ip_allowed(ip, NETS) is ok
