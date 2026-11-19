"""The owner-run helper scripts must produce files that mcp-guard accepts."""
import importlib.util
import tomllib
from pathlib import Path

from mcp_guard.config import parse_config

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gateway_config_is_valid():
    script = load("dev_gateway_config")
    token = "a" * 43
    cfg = parse_config(tomllib.loads(script.build(token, local=False)))
    assert cfg.gateway.token == token
    assert cfg.gateway.port == 8787
    assert "127.0.0.1/32" not in cfg.gateway.allowed_ips
    assert set(cfg.gateway.servers) == {"fake"}
    assert cfg.gateway.servers["fake"].args[0].endswith("fake_mcp_server.py")


def test_gateway_config_local_allows_loopback():
    script = load("dev_gateway_config")
    cfg = parse_config(tomllib.loads(script.build("b" * 43, local=True)))
    assert "127.0.0.1/32" in cfg.gateway.allowed_ips


def test_gateway_config_extra_ips():
    script = load("dev_gateway_config")
    cfg = parse_config(tomllib.loads(script.build("c" * 43, local=False, extra_ips=["2401:db8::/64"])))
    assert "2401:db8::/64" in cfg.gateway.allowed_ips


def test_gateway_config_args():
    script = load("dev_gateway_config")
    assert script.parse_args([]) == (False, False, [], None, False)
    assert script.parse_args(["--local", "--keep-token", "--allow-ip", "1.2.3.4/32"]) == (
        True, True, ["1.2.3.4/32"], None, False)
    assert script.parse_args(["--fake-protocol", "2025-06-18"]) == (False, False, [], "2025-06-18", False)
    assert script.parse_args(["--stateless"]) == (False, False, [], None, True)
    assert script.parse_args(["--allow-ip"]) is None
    assert script.parse_args(["--what"]) is None


def test_gateway_config_stateless():
    script = load("dev_gateway_config")
    assert parse_config(tomllib.loads(script.build("f" * 43, local=False, stateless=True))).gateway.stateless
    assert not parse_config(tomllib.loads(script.build("f" * 43, local=False))).gateway.stateless


def test_gateway_config_fake_protocol():
    script = load("dev_gateway_config")
    cfg = parse_config(tomllib.loads(script.build("e" * 43, local=False, fake_protocol="2025-06-18")))
    assert cfg.gateway.servers["fake"].args[1:] == ("--protocol-version", "2025-06-18")


def test_existing_token(tmp_path):
    script = load("dev_gateway_config")
    p = tmp_path / "config.toml"
    p.write_text('[gateway]\ntoken = "%s"\n' % ("d" * 43))
    assert script.existing_token(p) == "d" * 43
    assert script.existing_token(tmp_path / "nope.toml") is None
