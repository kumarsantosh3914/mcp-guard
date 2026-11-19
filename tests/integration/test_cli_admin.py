"""install, uninstall, and status through the real CLI, on temp files only.

launchctl is replaced by /usr/bin/true and every path points into tmp_path, so
these tests never touch the owner's Claude Desktop config or launchd.
"""
import json
import plistlib
import subprocess
import sys

from test_stdio_proxy import ENV

ORIGINAL = {
    "preferences": {"keep": True},
    "mcpServers": {
        "files": {"command": "npx", "args": ["-y", "server-files"], "env": {"A": "1"}},
        "remote": {"url": "https://example.com/mcp"},
    },
}


def run(tmp_path, *args):
    env = dict(ENV,
               MCP_GUARD_LAUNCH_AGENTS_DIR=str(tmp_path / "LaunchAgents"),
               MCP_GUARD_LOG_DIR=str(tmp_path / "logs"))
    return subprocess.run([sys.executable, "-m", "mcp_guard", *args],
                          capture_output=True, text=True, env=env, timeout=20)


def desktop(tmp_path, data=ORIGINAL):
    p = tmp_path / "claude_desktop_config.json"
    p.write_text(json.dumps(data))
    return p


def backups(tmp_path):
    return sorted(tmp_path.glob("claude_desktop_config.backup-*.json"))


def plist(tmp_path):
    return tmp_path / "LaunchAgents" / "local.mcp-guard.daemon.plist"


def test_dry_run_writes_nothing(tmp_path):
    p = desktop(tmp_path)
    r = run(tmp_path, "install", "--desktop-config", str(p), "--dry-run")
    assert r.returncode == 0
    assert "wrap" in r.stdout and "Dry run" in r.stdout
    assert json.loads(p.read_text()) == ORIGINAL
    assert backups(tmp_path) == []
    assert not plist(tmp_path).exists()


def test_install_then_uninstall(tmp_path):
    p = desktop(tmp_path)
    r = run(tmp_path, "install", "--desktop-config", str(p))
    assert r.returncode == 0, r.stderr
    cfg = json.loads(p.read_text())
    files = cfg["mcpServers"]["files"]
    assert files["command"].endswith("/mcp-guard")
    assert files["args"] == ["proxy", "--name", "files", "--", "npx", "-y", "server-files"]
    assert cfg["mcpServers"]["remote"] == ORIGINAL["mcpServers"]["remote"]
    assert cfg["preferences"] == {"keep": True}
    assert len(backups(tmp_path)) == 1
    agent = plistlib.loads(plist(tmp_path).read_bytes())
    assert agent["Label"] == "local.mcp-guard.daemon"
    assert agent["ProgramArguments"][1] == "daemon"

    r = run(tmp_path, "install", "--desktop-config", str(p))
    assert "nothing to change" in r.stdout
    assert len(backups(tmp_path)) == 1  # no write, no new backup

    r = run(tmp_path, "uninstall", "--desktop-config", str(p))
    assert r.returncode == 0, r.stderr
    assert json.loads(p.read_text()) == ORIGINAL
    assert len(backups(tmp_path)) == 2
    assert not plist(tmp_path).exists()


def test_no_autostart_leaves_launchd_alone(tmp_path):
    p = desktop(tmp_path)
    r = run(tmp_path, "install", "--desktop-config", str(p), "--no-autostart")
    assert r.returncode == 0, r.stderr
    assert not plist(tmp_path).exists()
    assert "Keep the daemon running" in r.stdout


def test_foreign_plist_is_never_replaced(tmp_path):
    agent = plist(tmp_path)
    agent.parent.mkdir(parents=True)
    agent.write_bytes(plistlib.dumps({"Label": "someone.else"}))
    r = run(tmp_path, "install", "--desktop-config", str(desktop(tmp_path)))
    assert r.returncode == 1
    assert "not made by mcp-guard" in r.stderr
    assert plistlib.loads(agent.read_bytes()) == {"Label": "someone.else"}


def test_bad_desktop_config_is_not_touched(tmp_path):
    p = tmp_path / "claude_desktop_config.json"
    p.write_text("{bad json")
    r = run(tmp_path, "install", "--desktop-config", str(p))
    assert r.returncode == 1
    assert "Nothing was changed" in r.stderr
    assert p.read_text() == "{bad json"
    assert not plist(tmp_path).exists()


def test_status_shows_protection(tmp_path):
    p = desktop(tmp_path)
    run(tmp_path, "install", "--desktop-config", str(p))
    r = run(tmp_path, "status", "--desktop-config", str(p))
    assert r.returncode == 1  # no daemon in tests
    assert "NOT running" in r.stdout
    assert "Autostart:   on" in r.stdout
    assert "files" in r.stdout and "protected" in r.stdout
    assert "remote" in r.stdout and "cannot be protected here" in r.stdout


def test_status_reports_bad_config(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("timout_seconds = 3\n")
    r = run(tmp_path, "status", "--config", str(cfg), "--desktop-config", str(desktop(tmp_path)))
    assert r.returncode == 1
    assert "unknown setting" in r.stdout


def test_proxy_refuses_bad_config(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("timeout_seconds = 99\n")
    r = run(tmp_path, "proxy", "--name", "x", "--config", str(cfg), "--", sys.executable, "-c", "pass")
    assert r.returncode == 2
    assert r.stdout == ""
    assert "bad config" in r.stderr


def test_gateway_without_token_explains_what_to_add(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[gateway.servers.fake]\ncommand = "python3"\n')
    r = run(tmp_path, "gateway", "--config", str(cfg))
    assert r.returncode == 2
    assert "No gateway token is set" in r.stdout
    assert 'token = "' in r.stdout


def test_gateway_without_servers_explains_what_to_add(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[gateway]\ntoken = "%s"\n' % ("a" * 40))
    r = run(tmp_path, "gateway", "--config", str(cfg))
    assert r.returncode == 2
    assert "No servers are set" in r.stdout
