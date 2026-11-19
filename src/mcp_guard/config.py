"""User settings from config.toml.

A missing file means defaults. A broken file is an error: the daemon will not
start, so every proxy blocks (fail closed). Unknown keys are errors too, so a
typo like `timout_seconds` is never silently ignored.
"""
from __future__ import annotations

import ipaddress
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_ENV = "MCP_GUARD_CONFIG"
ALWAYS_GATED = "tools/call"
LOG_ARGUMENT_MODES = ("none", "truncated", "full")
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 55.0  # Claude Desktop gives up after about 60 seconds
KNOWN_KEYS = {"timeout_seconds", "gated_methods", "log_arguments", "audit_log", "gateway"}

# Gateway
DEFAULT_GATEWAY_PORT = 8787
# Anthropic's published outbound ranges for MCP tool calls (checked 2026-11-19).
ANTHROPIC_OUTBOUND_RANGES = ("160.79.104.0/21", "153.61.208.0/20")
GATEWAY_KEYS = {"port", "token", "allowed_ips", "trust_proxy_header", "servers"}
SERVER_KEYS = {"command", "args", "env"}
SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,256}$")


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
class ServerSpec:
    """A local stdio MCP server that the gateway starts for each session."""

    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GatewayConfig:
    port: int = DEFAULT_GATEWAY_PORT
    token: str | None = None
    allowed_ips: tuple[str, ...] = ANTHROPIC_OUTBOUND_RANGES
    trust_proxy_header: bool = True  # read the real client IP from the tunnel's CF-Connecting-IP header
    servers: dict[str, ServerSpec] = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    timeout_seconds: float = 45.0
    gated_methods: frozenset[str] = frozenset({ALWAYS_GATED})
    log_arguments: str = "truncated"
    audit_log: Path = field(default_factory=default_audit_log_path)
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
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


def _check_unknown(data: dict[str, Any], known: set[str], where: str) -> None:
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError("%s: unknown setting(s): %s" % (where, ", ".join(unknown)))


def parse_config(data: dict[str, Any], source: Path | None = None) -> Config:
    where = str(source) if source else "config"
    _check_unknown(data, KNOWN_KEYS, where)
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

    if "gateway" in data:
        values["gateway"] = parse_gateway(data["gateway"], where)

    return Config(source=source, **values)


def parse_gateway(data: Any, where: str) -> GatewayConfig:
    if not isinstance(data, dict):
        raise ConfigError("%s: [gateway] must be a table" % where)
    where = where + " [gateway]"
    _check_unknown(data, GATEWAY_KEYS, where)
    values: dict[str, Any] = {}

    if "port" in data:
        port = data["port"]
        if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
            raise ConfigError("%s: port must be a number from 1024 to 65535" % where)
        values["port"] = port

    if "token" in data:
        token = data["token"]
        if not isinstance(token, str) or not TOKEN_RE.match(token):
            raise ConfigError("%s: token must be 32 to 256 characters of A-Z, a-z, 0-9, '-' or '_'" % where)
        values["token"] = token

    if "allowed_ips" in data:
        ips = data["allowed_ips"]
        if not isinstance(ips, list) or not ips or not all(isinstance(i, str) for i in ips):
            raise ConfigError("%s: allowed_ips must be a non-empty list of IP ranges" % where)
        for item in ips:
            try:
                ipaddress.ip_network(item, strict=False)
            except ValueError as exc:
                raise ConfigError("%s: bad IP range %r" % (where, item)) from exc
        values["allowed_ips"] = tuple(ips)

    if "trust_proxy_header" in data:
        if not isinstance(data["trust_proxy_header"], bool):
            raise ConfigError("%s: trust_proxy_header must be true or false" % where)
        values["trust_proxy_header"] = data["trust_proxy_header"]

    if "servers" in data:
        servers = data["servers"]
        if not isinstance(servers, dict):
            raise ConfigError("%s: servers must be a table" % where)
        values["servers"] = {name: _parse_server(name, spec, where) for name, spec in servers.items()}

    return GatewayConfig(**values)


def _parse_server(name: str, spec: Any, where: str) -> ServerSpec:
    here = "%s server %r" % (where, name)
    if not SERVER_NAME_RE.match(name):
        raise ConfigError("%s: server names may only use A-Z, a-z, 0-9, '-' and '_'" % here)
    if not isinstance(spec, dict):
        raise ConfigError("%s: must be a table" % here)
    _check_unknown(spec, SERVER_KEYS, here)
    command = spec.get("command")
    if not isinstance(command, str) or not command:
        raise ConfigError("%s: command is required" % here)
    args = spec.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ConfigError("%s: args must be a list of strings" % here)
    env = spec.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise ConfigError("%s: env must be a table of strings" % here)
    return ServerSpec(command, tuple(args), dict(env))
