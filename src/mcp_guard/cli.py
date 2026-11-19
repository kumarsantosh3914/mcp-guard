"""Command line entry point.

Commands:
  daemon  run the approval daemon (popup and menu bar icon)
  proxy   run one stdio MCP server behind mcp-guard

More commands (gateway, install, uninstall, status) come in later steps.

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
from .policy import DEFAULT_GATED_METHODS, Policy
from .proxy.stdio_proxy import DEFAULT_TIMEOUT_SECONDS, StdioProxy, run_stdio_proxy

DECIDER_CHOICES = ("daemon",) + DEV_DECIDERS


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

    daemon = sub.add_parser("daemon", help="run the approval daemon (popup and menu bar icon)")
    daemon.add_argument("--socket", type=Path, help=socket_help)
    daemon.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])

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
    proxy.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="seconds to wait for a decision before blocking (default: %(default)s)",
    )
    proxy.add_argument(
        "--gate",
        action="append",
        metavar="METHOD",
        help="method that needs approval; repeat for more (default: %s)" % ", ".join(sorted(DEFAULT_GATED_METHODS)),
    )
    proxy.add_argument("--socket", type=Path, help=socket_help)
    proxy.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    return parser


def _cmd_daemon(args: argparse.Namespace) -> int:
    _setup_logging(args.log_level)
    from .daemon.ui import run_daemon  # imported here: PySide6 is large and only the daemon needs it

    return run_daemon(args.socket or default_socket_path())


def _cmd_proxy(args: argparse.Namespace, server_command: list[str], parser: argparse.ArgumentParser) -> int:
    if not server_command:
        parser.error("proxy needs the MCP server command after '--'")
    if args.timeout <= 0:
        parser.error("--timeout must be greater than 0")
    _setup_logging(args.log_level)
    policy = Policy(frozenset(args.gate)) if args.gate else Policy()
    decider: Decider
    if args.decider == "daemon":
        decider = DaemonDecider(args.socket or default_socket_path(), source="desktop", timeout=args.timeout)
    else:
        decider = make_decider(args.decider)
    proxy = StdioProxy(args.name, server_command, decider, policy, args.timeout)
    return asyncio.run(run_stdio_proxy(proxy))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    head, tail = _split_command(argv)
    parser = build_parser()
    args = parser.parse_args(head)
    if args.command == "daemon":
        return _cmd_daemon(args)
    if args.command == "proxy":
        return _cmd_proxy(args, tail, parser)
    parser.print_help()
    return 0
