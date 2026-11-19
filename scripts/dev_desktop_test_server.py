#!/usr/bin/env python3
"""Development helper: add or remove the mcp-guard test server in Claude Desktop's config.

The owner runs this, not an agent, because it edits a file outside this project.

Usage:
  python3 scripts/dev_desktop_test_server.py add [daemon|allow|block|wait]   (default: daemon)
  python3 scripts/dev_desktop_test_server.py remove

Quit Claude Desktop (Cmd+Q) first, then start it again after the change.
A timestamped backup of the config is written before every change.
Only the "mcp-guard-test" entry is touched. All other settings stay as they are.
"""
import json
import shutil
import sys
import time
from pathlib import Path

CONFIG = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
ROOT = Path(__file__).resolve().parents[1]
NAME = "mcp-guard-test"
DECIDERS = ("daemon", "allow", "block", "wait")


def server_entry(decider):
    return {
        "command": str(ROOT / ".venv" / "bin" / "mcp-guard"),
        "args": [
            "proxy", "--name", "fake", "--decider", decider, "--timeout", "45", "--",
            str(ROOT / ".venv" / "bin" / "python"),
            str(ROOT / "tests" / "fakes" / "fake_mcp_server.py"),
        ],
    }


def main(argv):
    if len(argv) < 2 or argv[1] not in ("add", "remove"):
        print(__doc__)
        return 2
    action = argv[1]
    decider = argv[2] if len(argv) > 2 else "daemon"
    if decider not in DECIDERS:
        print("Decider must be one of: " + ", ".join(DECIDERS))
        return 2
    if action == "add" and not (ROOT / ".venv" / "bin" / "mcp-guard").exists():
        print("Not found: .venv/bin/mcp-guard. Run: pip install -e \".[dev]\" first.")
        return 1

    cfg = {}
    if CONFIG.exists():
        try:
            cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print("The config is not valid JSON, nothing was changed: " + str(exc))
            return 1
        backup = CONFIG.with_name("claude_desktop_config.backup-%d.json" % int(time.time()))
        shutil.copy2(CONFIG, backup)
        print("Backup: " + str(backup))

    servers = cfg.setdefault("mcpServers", {})
    if action == "add":
        servers[NAME] = server_entry(decider)
    else:
        servers.pop(NAME, None)
        if not servers:
            cfg.pop("mcpServers")

    # Write to a temp file first, then swap it in, so a crash cannot leave a half-written config.
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_name(CONFIG.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    tmp.replace(CONFIG)

    print("Done: %s %s%s" % (action, NAME, " (decider: %s)" % decider if action == "add" else ""))
    print("MCP servers now: " + str(list(cfg.get("mcpServers", {}))))
    print("Now start Claude Desktop again.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
