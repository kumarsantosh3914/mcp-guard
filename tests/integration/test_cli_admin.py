"""install, uninstall, and status through the real CLI, on a temp copy of a Desktop config."""
import json
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


def run(*args):
    return subprocess.run([sys.executable, "-m", "mcp_guard", *args],
                          capture_output=True, text=True, env=ENV, timeout=20)


def desktop(tmp_path, data=ORIGINAL):
    p = tmp_path / "claude_desktop_config.json"
    p.write_text(json.dumps(data))
    return p


def backups(tmp_path):
    return sorted(tmp_path.glob("claude_desktop_config.backup-*.json"))


def test_dry_run_writes_nothing(tmp_path):
    p = desktop(tmp_path)
    r = run("install", "--desktop-config", str(p), "--dry-run")
    assert r.returncode == 0
    assert "wrap" in r.stdout and "Dry run" in r.stdout
    assert json.loads(p.read_text()) == ORIGINAL
    assert backups(tmp_path) == []


def test_install_then_uninstall(tmp_path):
    p = desktop(tmp_path)
    r = run("install", "--desktop-config", str(p))
    assert r.returncode == 0, r.stderr
    cfg = json.loads(p.read_text())
    files = cfg["mcpServers"]["files"]
    assert files["command"].endswith("/mcp-guard")
    assert files["args"] == ["proxy", "--name", "files", "--", "npx", "-y", "server-files"]
    assert cfg["mcpServers"]["remote"] == ORIGINAL["mcpServers"]["remote"]
    assert cfg["preferences"] == {"keep": True}
    assert len(backups(tmp_path)) == 1

    r = run("install", "--desktop-config", str(p))
    assert "Nothing to change" in r.stdout
    assert len(backups(tmp_path)) == 1  # no write, no new backup

    r = run("uninstall", "--desktop-config", str(p))
    assert r.returncode == 0, r.stderr
    assert json.loads(p.read_text()) == ORIGINAL
    assert len(backups(tmp_path)) == 2


def test_bad_desktop_config_is_not_touched(tmp_path):
    p = tmp_path / "claude_desktop_config.json"
    p.write_text("{bad json")
    r = run("install", "--desktop-config", str(p))
    assert r.returncode == 1
    assert "Nothing was changed" in r.stderr
    assert p.read_text() == "{bad json"


def test_status_shows_protection(tmp_path):
    p = desktop(tmp_path)
    run("install", "--desktop-config", str(p))
    r = run("status", "--desktop-config", str(p))
    assert r.returncode == 1  # no daemon in tests
    assert "NOT running" in r.stdout
    assert "files" in r.stdout and "protected" in r.stdout
    assert "remote" in r.stdout and "cannot be protected here" in r.stdout


def test_status_reports_bad_config(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("timout_seconds = 3\n")
    r = run("status", "--config", str(cfg), "--desktop-config", str(desktop(tmp_path)))
    assert r.returncode == 1
    assert "unknown setting" in r.stdout


def test_proxy_refuses_bad_config(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("timeout_seconds = 99\n")
    r = run("proxy", "--name", "x", "--config", str(cfg), "--", sys.executable, "-c", "pass")
    assert r.returncode == 2
    assert r.stdout == ""
    assert "bad config" in r.stderr
