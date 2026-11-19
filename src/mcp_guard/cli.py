"""Command line entry point.

Subcommands (daemon, proxy, gateway, install, uninstall, status) are added in later steps.
"""
from __future__ import annotations

import argparse

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mcp-guard",
        description="Approve or block every MCP request from Claude Desktop and Claude Web.",
    )
    parser.add_argument("--version", action="version", version="mcp-guard " + __version__)
    parser.parse_args(argv)
    parser.print_help()
    return 0
