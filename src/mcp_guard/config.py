"""User settings from config.toml.

A missing file means defaults. A broken file is an error: the daemon will not
start, so every proxy blocks (fail closed). Unknown keys are errors too, so a
typo like `timout_seconds` is never silently ignored.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_ENV = "MCP_GUARD_CONFIG"
ALWAYS_GATED = "tools/call"
LOG_ARGUMENT_MODES = ("none", "truncated", "full")
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 55.0  # Claude Desktop gives up after about 60 seconds
KNOWN_KEYS = {"timeout_seconds", "gated_methods", "log_arguments", "audit_log"}
RESERVED_TABLES = {"gateway"}  # used by the HTTP gateway in a later step


class ConfigError(ValueError):
    pass


def default_config_path() -> Path:
    env = os.environ.get(CONFIG_ENV)
    if env:
        return Path(env)
    return Path.home() / "Library" / "Application Support" / "mcp-guard" / "config.toml"


def default_audit_log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "mcp-guard" / "audit.jsonl"


@dataclass(frozen=True)
class Config:
    timeout_seconds: float = 45.0
    gated_methods: frozenset[str] = frozenset({ALWAYS_GATED})
    log_arguments: str = "truncated"
    audit_log: Path = field(default_factory=default_audit_log_path)
    source: Path | None = None  # the file it came from, or None for defaults


def load_config(path: Path | None = None) -> Config:
    path = path or default_config_path()
    if not path.exists():
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError("%s: %s" % (path, exc)) from exc
    return parse_config(data, path)


def parse_config(data: dict[str, Any], source: Path | None = None) -> Config:
    where = str(source) if source else "config"
    unknown = sorted(set(data) - KNOWN_KEYS - RESERVED_TABLES)
    if unknown:
        raise ConfigError("%s: unknown setting(s): %s" % (where, ", ".join(unknown)))

    values: dict[str, Any] = {}

    if "timeout_seconds" in data:
        t = data["timeout_seconds"]
        if (not isinstance(t, (int, float)) or isinstance(t, bool)
                or not MIN_TIMEOUT_SECONDS <= t <= MAX_TIMEOUT_SECONDS):
            raise ConfigError("%s: timeout_seconds must be a number from %g to %g"
                              % (where, MIN_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS))
        values["timeout_seconds"] = float(t)

    if "gated_methods" in data:
        methods = data["gated_methods"]
        if not isinstance(methods, list) or not all(isinstance(m, str) and m for m in methods):
            raise ConfigError("%s: gated_methods must be a list of method names" % where)
        # tools/call can never be turned off. Config may only add methods.
        values["gated_methods"] = frozenset(methods) | {ALWAYS_GATED}

    if "log_arguments" in data:
        mode = data["log_arguments"]
        if mode not in LOG_ARGUMENT_MODES:
            raise ConfigError("%s: log_arguments must be one of: %s" % (where, ", ".join(LOG_ARGUMENT_MODES)))
        values["log_arguments"] = mode

    if "audit_log" in data:
        p = data["audit_log"]
        if not isinstance(p, str) or not p:
            raise ConfigError("%s: audit_log must be a file path" % where)
        values["audit_log"] = Path(p).expanduser()

    return Config(source=source, **values)
