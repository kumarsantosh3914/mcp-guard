import plistlib
import subprocess

import pytest

from mcp_guard.install import launchd
from mcp_guard.install.desktop_config import InstallError


class FakeLaunchctl:
    def __init__(self, fail_bootstrap=False, loaded=True):
        self.calls = []
        self.fail_bootstrap = fail_bootstrap
        self.loaded = loaded

    def __call__(self, cmd):
        self.calls.append(cmd[1:])
        code = 0
        if cmd[1] == "bootstrap" and self.fail_bootstrap:
            code = 5
        if cmd[1] == "print" and not self.loaded:
            code = 113
        return subprocess.CompletedProcess(cmd, code, "", "boom" if code else "")


def test_plist_content(tmp_path):
    p = launchd.build_plist("/venv/bin/mcp-guard", tmp_path / "daemon.log")
    assert p["Label"] == launchd.LABEL
    assert p["ProgramArguments"] == ["/venv/bin/mcp-guard", "daemon"]
    assert p["RunAtLoad"] is True
    assert p["KeepAlive"] == {"SuccessfulExit": False}  # restart after crash, not after Quit
    assert p["LimitLoadToSessionType"] == "Aqua"
    assert p["StandardErrorPath"].endswith("daemon.log")


def test_install_writes_plist_and_loads_it(tmp_path):
    fake = FakeLaunchctl()
    path = tmp_path / "agents" / "x.plist"
    launchd.install_agent("/venv/bin/mcp-guard", fake, path, tmp_path / "logs" / "daemon.log")
    assert plistlib.loads(path.read_bytes())["Label"] == launchd.LABEL
    assert [c[0] for c in fake.calls] == ["bootout", "bootstrap"]
    assert fake.calls[1][2] == str(path)
    assert (tmp_path / "logs").is_dir()


def test_install_failure_is_reported(tmp_path):
    with pytest.raises(InstallError):
        launchd.install_agent("/g", FakeLaunchctl(fail_bootstrap=True), tmp_path / "x.plist", tmp_path / "d.log")


def test_install_refuses_foreign_plist(tmp_path):
    path = tmp_path / "x.plist"
    path.write_bytes(plistlib.dumps({"Label": "other"}))
    with pytest.raises(InstallError):
        launchd.install_agent("/g", FakeLaunchctl(), path, tmp_path / "d.log")
    assert plistlib.loads(path.read_bytes()) == {"Label": "other"}


def test_uninstall_removes_only_ours(tmp_path):
    fake = FakeLaunchctl()
    path = tmp_path / "x.plist"
    assert launchd.uninstall_agent(fake, path) is False  # not installed

    path.write_bytes(plistlib.dumps({"Label": "other"}))
    with pytest.raises(InstallError):
        launchd.uninstall_agent(fake, path)
    assert path.exists()

    path.write_bytes(plistlib.dumps(launchd.build_plist("/g", tmp_path / "d.log")))
    assert launchd.uninstall_agent(fake, path) is True
    assert not path.exists()
    assert fake.calls[-1][0] == "bootout"


def test_status(tmp_path):
    path = tmp_path / "x.plist"
    assert launchd.agent_status(FakeLaunchctl(), path) == "off"
    path.write_bytes(plistlib.dumps(launchd.build_plist("/g", tmp_path / "d.log")))
    assert launchd.agent_status(FakeLaunchctl(loaded=True), path) == "on"
    assert launchd.agent_status(FakeLaunchctl(loaded=False), path) == "installed but not loaded"
