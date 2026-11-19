"""Command line entry point.

Commands:
  daemon     run the approval daemon (popup and menu bar icon)
  proxy      run one stdio MCP server behind mcp-guard
  status     show what is running and which Claude Desktop servers are protected
  install    put every local Claude Desktop server behind mcp-guard
  uninstall  undo install

Everything after a bare `--` is the real MCP server command, passed through unchanged.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from . import __version__
from .approval.client import DaemonDecider
from .approval.decider import DEV_DECIDERS, Decider, make_decider
from .approval.protocol import default_socket_path
from .config import ALWAYS_GATED, ConfigError, default_config_path, load_config
from .install import desktop_config as dc
from .policy import Policy
from .proxy.stdio_proxy import StdioProxy, run_stdio_proxy

DECIDER_CHOICES = ("daemon",) + DEV_DECIDERS
LOG_LEVELS = ["debug", "info", "warning", "error"]


def _split_command(argv: list[str]) -> tuple[list[str], list[str]]:
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1:]
    return argv, []


def _setup_logging(level: str) -> None:
    # stdout is reserved for MCP messages, so logs always go to stderr.
    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, level.upper()),
        format="mcp-guard %(levelname)s [%(name)s] %(message)s",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-guard",
        description="Approve or block every MCP request from Claude Desktop and Claude Web.",
    )
    parser.add_argument("--version", action="version", version="mcp-guard " + __version__)
    sub = parser.add_subparsers(dest="command")

    socket_help = "daemon socket path (default: %s)" % default_socket_path()
    config_help = "config file (default: %s)" % default_config_path()
    desktop_help = "Claude Desktop config file (default: %s)" % dc.default_desktop_config_path()

    daemon = sub.add_parser("daemon", help="run the approval daemon (popup and menu bar icon)")
    daemon.add_argument("--socket", type=Path, help=socket_help)
    daemon.add_argument("--config", type=Path, help=config_help)
    daemon.add_argument("--log-level", default="info", choices=LOG_LEVELS)

    proxy = sub.add_parser(
        "proxy",
        help="run one stdio MCP server behind mcp-guard",
        usage="mcp-guard proxy --name NAME [options] -- SERVER_COMMAND [ARGS...]",
    )
    proxy.add_argument("--name", required=True, help="server name shown in the popup and the log")
    proxy.add_argument(
        "--decider",
        choices=DECIDER_CHOICES,
        default="daemon",
        help="who decides: the daemon popup (default), or a development decider: %s" % ", ".join(DEV_DECIDERS),
    )
    proxy.add_argument("--timeout", type=float, help="seconds to wait for a decision (default: from config, 45)")
    proxy.add_argument("--gate", action="append", metavar="METHOD",
                       help="extra method that needs approval; repeat for more (%s always does)" % ALWAYS_GATED)
    proxy.add_argument("--socket", type=Path, help=socket_help)
    proxy.add_argument("--config", type=Path, help=config_help)
    proxy.add_argument("--log-level", default="info", choices=LOG_LEVELS)

    status = sub.add_parser("status", help="show what is running and which servers are protected")
    status.add_argument("--socket", type=Path, help=socket_help)
    status.add_argument("--config", type=Path, help=config_help)
    status.add_argument("--desktop-config", type=Path, help=desktop_help)

    for name, text in (("install", "put every local Claude Desktop server behind mcp-guard"),
                       ("uninstall", "remove mcp-guard from the servers that install changed")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--desktop-config", type=Path, help=desktop_help)
        p.add_argument("--dry-run", action="store_true", help="show the changes without writing anything")
    return parser


# ---- daemon and proxy ----

def _cmd_daemon(args: argparse.Namespace) -> int:
    _setup_logging(args.log_level)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        logging.getLogger("mcp_guard").error("bad config, the daemon will not start: %s", exc)
        return 2
    from .daemon.ui import run_daemon  # imported here: PySide6 is large and only the daemon needs it

    return run_daemon(args.socket or default_socket_path(), config)


def _cmd_proxy(args: argparse.Namespace, server_command: list[str], parser: argparse.ArgumentParser) -> int:
    if not server_command:
        parser.error("proxy needs the MCP server command after '--'")
    _setup_logging(args.log_level)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        # Exiting means Claude cannot use this server at all, which is the safe choice.
        logging.getLogger("mcp_guard").error("bad config, the proxy will not start: %s", exc)
        return 2
    timeout = args.timeout if args.timeout is not None else config.timeout_seconds
    if timeout <= 0:
        parser.error("--timeout must be greater than 0")
    policy = Policy(config.gated_methods | frozenset(args.gate or []) | {ALWAYS_GATED})
    decider: Decider
    if args.decider == "daemon":
        decider = DaemonDecider(args.socket or default_socket_path(), source="desktop", timeout=timeout)
    else:
        decider = make_decider(args.decider)
    proxy = StdioProxy(args.name, server_command, decider, policy, timeout)
    return asyncio.run(run_stdio_proxy(proxy))


# ---- status ----

def _cmd_status(args: argparse.Namespace) -> int:
    from .daemon.server import socket_is_alive

    print("mcp-guard " + __version__)
    config_path = args.config or default_config_path()
    try:
        config = load_config(config_path)
        print("Config:      %s (%s)" % (config_path, "found" if config.source else "not found, using defaults"))
    except ConfigError as exc:
        print("Config:      ERROR: %s" % exc)
        config = None

    socket_path = args.socket or default_socket_path()
    running = asyncio.run(socket_is_alive(socket_path))
    print("Daemon:      %s (%s)" % ("running" if running else "NOT running", socket_path))
    if config is not None:
        log_path = config.audit_log
        print("Audit log:   %s (%s)" % (log_path, "exists" if log_path.exists() else "not created yet"))
        print("Timeout:     %g s" % config.timeout_seconds)
        print("Gated:       %s" % ", ".join(sorted(config.gated_methods)))

    desktop_path = args.desktop_config or dc.default_desktop_config_path()
    print("Desktop:     %s" % desktop_path)
    try:
        servers = dc.read_desktop_config(desktop_path).get("mcpServers") or {}
    except dc.InstallError as exc:
        print("  ERROR: %s" % exc)
        servers = {}
    if not isinstance(servers, dict) or not servers:
        print("  no MCP servers")
    for name, entry in (servers.items() if isinstance(servers, dict) else []):
        if dc.is_wrapped(entry):
            state = "protected"
        elif isinstance(entry, dict) and isinstance(entry.get("command"), str):
            state = "NOT protected (run: mcp-guard install)"
        else:
            state = "not a local command server, cannot be protected here"
        print("  %-24s %s" % (name, state))
    if not running:
        print("\nStart the daemon with: mcp-guard daemon")
    return 0 if running and config is not None else 1


# ---- install and uninstall ----

def _print_changes(changes: list[dc.Change]) -> None:
    if not changes:
        print("No MCP servers found in the Claude Desktop config.")
    for c in changes:
        print("  %-8s %-24s %s" % (c.action, c.name, c.reason))


def _cmd_install(args: argparse.Namespace, undo: bool) -> int:
    path = args.desktop_config or dc.default_desktop_config_path()
    try:
        cfg = dc.read_desktop_config(path)
        if undo:
            new_cfg, changes = dc.plan_uninstall(cfg)
        else:
            new_cfg, changes = dc.plan_install(cfg, dc.mcp_guard_command())
    except dc.InstallError as exc:
        print("Error: %s" % exc, file=sys.stderr)
        return 1

    print("Claude Desktop config: %s" % path)
    _print_changes(changes)
    action = "unwrap" if undo else "wrap"
    if not any(c.action == action for c in changes):
        print("Nothing to change.")
        return 0
    if args.dry_run:
        print("Dry run: nothing was written.")
        return 0
    backup = dc.write_desktop_config(path, new_cfg)
    if backup is not None:
        print("Backup:  %s" % backup)
    print("Done. Quit Claude Desktop (Cmd+Q) and open it again.")
    if not undo:
        print("Keep the daemon running (mcp-guard daemon), or every tool call is blocked.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    head, tail = _split_command(argv)
    parser = build_parser()
    args = parser.parse_args(head)
    if args.command == "daemon":
        return _cmd_daemon(args)
    if args.command == "proxy":
        return _cmd_proxy(args, tail, parser)
    if args.command == "status":
        return _cmd_status(args)
    if args.command in ("install", "uninstall"):
        return _cmd_install(args, undo=args.command == "uninstall")
    parser.print_help()
    return 0
