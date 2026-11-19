"""Wrap and unwrap the MCP servers in Claude Desktop's config.

Install turns each local server

    "files": {"command": "npx", "args": ["-y", "server-files"], "env": {...}}

into

    "files": {"command": "/path/to/mcp-guard",
              "args": ["proxy", "--name", "files", "--", "npx", "-y", "server-files"],
              "env": {...}}

Uninstall reverses it, using the command after "--". No extra state file is needed.

Safety:
- Only "mcpServers" is changed. Every other setting stays as it is.
- A timestamped backup is written before every change. Nothing is ever deleted.
- The new file is written to a temp file first, then swapped in.
- Entries that point to mcp-guard with custom options are left alone.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DESKTOP_CONFIG_ENV = "MCP_GUARD_DESKTOP_CONFIG"
GUARD_NAME = "mcp-guard"


class InstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class Change:
    name: str
    action: str  # "wrap", "unwrap", or "skip"
    reason: str


def default_desktop_config_path() -> Path:
    env = os.environ.get(DESKTOP_CONFIG_ENV)
    if env:
        return Path(env)
    return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"


def mcp_guard_command() -> str:
    """Absolute path of the mcp-guard command to put in Desktop's config."""
    candidate = Path(sys.executable).with_name(GUARD_NAME)  # same virtual env as this Python
    if candidate.exists():
        return str(candidate)
    found = shutil.which(GUARD_NAME)
    if found:
        return str(Path(found).absolute())
    raise InstallError("cannot find the mcp-guard command. Install the package first (pip install -e .).")


def _points_to_guard(entry: dict[str, Any]) -> bool:
    command = entry.get("command")
    return isinstance(command, str) and Path(command).name == GUARD_NAME


def is_wrapped(entry: Any) -> bool:
    """Any entry that runs `mcp-guard proxy ... -- server`."""
    if not isinstance(entry, dict) or not _points_to_guard(entry):
        return False
    args = entry.get("args")
    return isinstance(args, list) and args[:1] == ["proxy"] and "--" in args


def is_installed_wrap(name: str, entry: Any) -> bool:
    """An entry made by `mcp-guard install` (no custom options), so uninstall may undo it."""
    if not is_wrapped(entry):
        return False
    args = entry["args"]
    return args[:4] == ["proxy", "--name", name, "--"] and len(args) > 4


def wrap_entry(name: str, entry: dict[str, Any], guard_command: str) -> dict[str, Any]:
    new = dict(entry)
    new["command"] = guard_command
    new["args"] = ["proxy", "--name", name, "--", entry["command"], *(entry.get("args") or [])]
    return new


def unwrap_entry(entry: dict[str, Any]) -> dict[str, Any]:
    args = entry["args"]
    rest = args[args.index("--") + 1:]
    if not rest:
        raise InstallError("wrapped entry has no server command after '--'")
    new = dict(entry)
    new["command"] = rest[0]
    if len(rest) > 1:
        new["args"] = rest[1:]
    else:
        new.pop("args", None)
    return new


def _servers(cfg: dict[str, Any]) -> dict[str, Any]:
    servers = cfg.get("mcpServers")
    return servers if isinstance(servers, dict) else {}


def plan_install(cfg: dict[str, Any], guard_command: str) -> tuple[dict[str, Any], list[Change]]:
    new_servers: dict[str, Any] = {}
    changes: list[Change] = []
    for name, entry in _servers(cfg).items():
        if not isinstance(entry, dict) or not isinstance(entry.get("command"), str):
            changes.append(Change(name, "skip", "not a local command server"))
            new_servers[name] = entry
        elif is_wrapped(entry):
            changes.append(Change(name, "skip", "already behind mcp-guard"))
            new_servers[name] = entry
        elif _points_to_guard(entry):
            changes.append(Change(name, "skip", "runs mcp-guard with custom options"))
            new_servers[name] = entry
        else:
            changes.append(Change(name, "wrap", "will be protected by mcp-guard"))
            new_servers[name] = wrap_entry(name, entry, guard_command)
    new_cfg = dict(cfg)
    if new_servers or "mcpServers" in cfg:
        new_cfg["mcpServers"] = new_servers
    return new_cfg, changes


def plan_uninstall(cfg: dict[str, Any]) -> tuple[dict[str, Any], list[Change]]:
    new_servers: dict[str, Any] = {}
    changes: list[Change] = []
    for name, entry in _servers(cfg).items():
        if is_installed_wrap(name, entry):
            changes.append(Change(name, "unwrap", "mcp-guard will be removed from this server"))
            new_servers[name] = unwrap_entry(entry)
        elif is_wrapped(entry):
            changes.append(Change(name, "skip", "custom mcp-guard entry, not made by install"))
            new_servers[name] = entry
        else:
            changes.append(Change(name, "skip", "not behind mcp-guard"))
            new_servers[name] = entry
    new_cfg = dict(cfg)
    if "mcpServers" in cfg:
        new_cfg["mcpServers"] = new_servers
    return new_cfg, changes


def read_desktop_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallError("cannot read %s: %s. Nothing was changed." % (path, exc)) from exc
    if not isinstance(data, dict):
        raise InstallError("%s is not a JSON object. Nothing was changed." % path)
    return data


def _backup_path(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name("%s.backup-%s%s" % (path.stem, stamp, path.suffix))
    n = 1
    while candidate.exists():
        candidate = path.with_name("%s.backup-%s-%d%s" % (path.stem, stamp, n, path.suffix))
        n += 1
    return candidate


def write_desktop_config(path: Path, cfg: dict[str, Any]) -> Path | None:
    """Back up the old file (if any), then write the new one. Returns the backup path."""
    backup = None
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = _backup_path(path)
        shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    if backup is not None:
        shutil.copymode(backup, tmp)
    tmp.replace(path)
    return backup
