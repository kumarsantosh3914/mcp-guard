import json
import os
import stat

import pytest

from mcp_guard.install import desktop_config as dc

GUARD = "/opt/venv/bin/mcp-guard"

ORIGINAL = {
    "preferences": {"sidebarMode": "x"},
    "mcpServers": {
        "files": {"command": "npx", "args": ["-y", "server-files", "/tmp"], "env": {"A": "1"}},
        "plain": {"command": "/usr/local/bin/srv"},
        "remote": {"url": "https://example.com/mcp"},
        "test": {"command": "/x/.venv/bin/mcp-guard",
                 "args": ["proxy", "--name", "fake", "--decider", "daemon", "--", "python", "f.py"]},
    },
}


def test_wrap_and_unwrap_round_trip():
    entry = ORIGINAL["mcpServers"]["files"]
    wrapped = dc.wrap_entry("files", entry, GUARD)
    assert wrapped == {
        "command": GUARD,
        "args": ["proxy", "--name", "files", "--", "npx", "-y", "server-files", "/tmp"],
        "env": {"A": "1"},
    }
    assert dc.unwrap_entry(wrapped) == entry


def test_round_trip_without_args():
    entry = {"command": "/usr/local/bin/srv"}
    assert dc.unwrap_entry(dc.wrap_entry("plain", entry, GUARD)) == entry


def test_plan_install():
    new, changes = dc.plan_install(ORIGINAL, GUARD)
    actions = {c.name: c.action for c in changes}
    assert actions == {"files": "wrap", "plain": "wrap", "remote": "skip", "test": "skip"}
    assert new["preferences"] == ORIGINAL["preferences"]  # other settings untouched
    assert new["mcpServers"]["remote"] == ORIGINAL["mcpServers"]["remote"]
    assert new["mcpServers"]["test"] == ORIGINAL["mcpServers"]["test"]
    assert dc.is_installed_wrap("files", new["mcpServers"]["files"])
    assert ORIGINAL["mcpServers"]["files"]["command"] == "npx"  # input not changed in place


def test_install_twice_changes_nothing_more():
    once, _ = dc.plan_install(ORIGINAL, GUARD)
    twice, changes = dc.plan_install(once, GUARD)
    assert twice == once
    assert not any(c.action == "wrap" for c in changes)


def test_uninstall_restores_and_leaves_custom_entries():
    installed, _ = dc.plan_install(ORIGINAL, GUARD)
    restored, changes = dc.plan_uninstall(installed)
    assert restored == ORIGINAL
    actions = {c.name: c.action for c in changes}
    assert actions["test"] == "skip"  # custom options, not made by install


def test_no_servers():
    new, changes = dc.plan_install({"preferences": {}}, GUARD)
    assert new == {"preferences": {}}
    assert changes == []


def test_read_missing_file(tmp_path):
    assert dc.read_desktop_config(tmp_path / "nope.json") == {}


@pytest.mark.parametrize("text", ["{bad", "[1, 2]"])
def test_read_bad_file(tmp_path, text):
    p = tmp_path / "c.json"
    p.write_text(text)
    with pytest.raises(dc.InstallError):
        dc.read_desktop_config(p)


def test_write_makes_backup_and_keeps_mode(tmp_path):
    p = tmp_path / "claude_desktop_config.json"
    p.write_text(json.dumps(ORIGINAL))
    os.chmod(p, 0o600)
    new, _ = dc.plan_install(ORIGINAL, GUARD)
    backup = dc.write_desktop_config(p, new)
    assert json.loads(backup.read_text()) == ORIGINAL
    assert json.loads(p.read_text()) == new
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert not (tmp_path / "claude_desktop_config.json.tmp").exists()


def test_backups_never_overwrite_each_other(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{}")
    b1 = dc.write_desktop_config(p, {"a": 1})
    b2 = dc.write_desktop_config(p, {"a": 2})
    assert b1 != b2
    assert json.loads(b1.read_text()) == {}
    assert json.loads(b2.read_text()) == {"a": 1}


def test_write_new_file_has_no_backup(tmp_path):
    assert dc.write_desktop_config(tmp_path / "c.json", {}) is None
