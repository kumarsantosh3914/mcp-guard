import pytest

from mcp_guard.config import ANTHROPIC_OUTBOUND_RANGES, ConfigError, GatewayConfig, ServerSpec, parse_config

TOKEN = "t" * 40


def test_gateway_defaults():
    gw = parse_config({}).gateway
    assert gw == GatewayConfig()
    assert gw.port == 8787
    assert gw.token is None
    assert gw.allowed_ips == ANTHROPIC_OUTBOUND_RANGES
    assert gw.trust_proxy_header is True
    assert gw.servers == {}


def test_full_gateway():
    gw = parse_config({"gateway": {
        "port": 9000,
        "token": TOKEN,
        "allowed_ips": ["127.0.0.1/32", "160.79.104.0/21"],
        "trust_proxy_header": False,
        "servers": {"files": {"command": "npx", "args": ["-y", "srv"], "env": {"A": "1"}}},
    }}).gateway
    assert gw.port == 9000
    assert gw.token == TOKEN
    assert gw.allowed_ips == ("127.0.0.1/32", "160.79.104.0/21")
    assert gw.trust_proxy_header is False
    assert gw.servers == {"files": ServerSpec("npx", ("-y", "srv"), {"A": "1"})}


@pytest.mark.parametrize("gateway", [
    "not a table",
    {"prot": 9000},
    {"port": 80},
    {"port": 70000},
    {"port": "8787"},
    {"token": "short"},
    {"token": "x" * 40 + "!"},
    {"allowed_ips": []},
    {"allowed_ips": ["not an ip"]},
    {"allowed_ips": "127.0.0.1"},
    {"trust_proxy_header": "yes"},
    {"servers": []},
    {"servers": {"bad name": {"command": "x"}}},
    {"servers": {"a": {"args": ["x"]}}},
    {"servers": {"a": {"command": "x", "args": "y"}}},
    {"servers": {"a": {"command": "x", "env": {"A": 1}}}},
    {"servers": {"a": {"command": "x", "cwd": "/"}}},
])
def test_bad_gateway(gateway):
    with pytest.raises(ConfigError):
        parse_config({"gateway": gateway})
