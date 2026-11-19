from pathlib import Path

import pytest

from mcp_guard.config import Config, ConfigError, load_config, parse_config


def test_defaults():
    c = Config()
    assert c.timeout_seconds == 45.0
    assert c.gated_methods == {"tools/call"}
    assert c.log_arguments == "truncated"
    assert c.audit_log.name == "audit.jsonl"
    assert c.source is None


def test_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml") == Config()


def test_full_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        'timeout_seconds = 30\n'
        'gated_methods = ["resources/read"]\n'
        'log_arguments = "none"\n'
        'audit_log = "~/x/audit.jsonl"\n'
        '[gateway]\nport = 8787\n'
    )
    c = load_config(p)
    assert c.timeout_seconds == 30.0
    assert c.gated_methods == {"tools/call", "resources/read"}
    assert c.log_arguments == "none"
    assert c.audit_log == Path.home() / "x" / "audit.jsonl"
    assert c.source == p


def test_tools_call_can_never_be_removed():
    assert parse_config({"gated_methods": []}).gated_methods == {"tools/call"}
    assert parse_config({"gated_methods": ["prompts/get"]}).gated_methods == {"tools/call", "prompts/get"}


@pytest.mark.parametrize("data", [
    {"timout_seconds": 30},              # typo
    {"timeout_seconds": 0},
    {"timeout_seconds": 56},             # Desktop would give up first
    {"timeout_seconds": "45"},
    {"timeout_seconds": True},
    {"gated_methods": "tools/call"},
    {"gated_methods": [1]},
    {"gated_methods": [""]},
    {"log_arguments": "everything"},
    {"audit_log": ""},
    {"audit_log": 5},
])
def test_bad_values(data):
    with pytest.raises(ConfigError):
        parse_config(data)


def test_bad_toml(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("timeout_seconds = = 3\n")
    with pytest.raises(ConfigError) as err:
        load_config(p)
    assert str(p) in str(err.value)
