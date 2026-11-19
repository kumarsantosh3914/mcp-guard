"""Start the daemon at login with a launchd agent.

- Runs only in the GUI session (the popup needs a screen).
- If the daemon crashes, launchd starts it again.
- If the user quits it from the menu bar (clean exit), it stays stopped until next login.
- Uninstall removes only a plist that mcp-guard made (checked by its Label).
"""
from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path
from typing import Callable

from .desktop_config import InstallError

LABEL = "local.mcp-guard.daemon"
LAUNCHCTL_ENV = "MCP_GUARD_LAUNCHCTL"  # tests point this at a harmless command
AGENTS_DIR_ENV = "MCP_GUARD_LAUNCH_AGENTS_DIR"
LOG_DIR_ENV = "MCP_GUARD_LOG_DIR"

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def default_runner(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def launchctl() -> str:
    return os.environ.get(LAUNCHCTL_ENV, "/bin/launchctl")


def default_plist_path() -> Path:
    base = os.environ.get(AGENTS_DIR_ENV)
    directory = Path(base) if base else Path.home() / "Library" / "LaunchAgents"
    return directory / (LABEL + ".plist")


def default_daemon_log_path() -> Path:
    base = os.environ.get(LOG_DIR_ENV)
    directory = Path(base) if base else Path.home() / "Library" / "Logs" / "mcp-guard"
    return directory / "daemon.log"


def _target() -> str:
    return "gui/%d" % os.getuid()


def build_plist(guard_command: str, log_path: Path) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [guard_command, "daemon"],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},  # restart after a crash, not after Quit
        "LimitLoadToSessionType": "Aqua",
        "ProcessType": "Interactive",
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
    }


def is_ours(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            data = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return False
    return isinstance(data, dict) and data.get("Label") == LABEL


def install_agent(
    guard_command: str,
    runner: Runner = default_runner,
    path: Path | None = None,
    log_path: Path | None = None,
) -> Path:
    path = path or default_plist_path()
    log_path = log_path or default_daemon_log_path()
    if path.exists() and not is_ours(path):
        raise InstallError("%s exists and was not made by mcp-guard. Leaving it alone." % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    runner([launchctl(), "bootout", "%s/%s" % (_target(), LABEL)])  # unload an older copy; fails if none
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(plistlib.dumps(build_plist(guard_command, log_path)))
    os.chmod(tmp, 0o644)
    tmp.replace(path)

    result = runner([launchctl(), "bootstrap", _target(), str(path)])
    if result.returncode != 0:
        raise InstallError("launchctl could not load %s: %s" % (path, (result.stderr or "").strip()))
    return path


def uninstall_agent(runner: Runner = default_runner, path: Path | None = None) -> bool:
    """Unload and remove the agent. Returns False if it was not installed."""
    path = path or default_plist_path()
    if not path.exists():
        return False
    if not is_ours(path):
        raise InstallError("%s was not made by mcp-guard. Leaving it alone." % path)
    runner([launchctl(), "bootout", "%s/%s" % (_target(), LABEL)])  # fails if not loaded; that is fine
    path.unlink()
    return True


def agent_status(runner: Runner = default_runner, path: Path | None = None) -> str:
    path = path or default_plist_path()
    if not path.exists():
        return "off"
    loaded = runner([launchctl(), "print", "%s/%s" % (_target(), LABEL)]).returncode == 0
    return "on" if loaded else "installed but not loaded"
