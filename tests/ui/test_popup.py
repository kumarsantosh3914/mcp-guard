"""Popup tests without a screen (Qt offscreen platform)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mcp_guard.approval.decider import Decision  # noqa: E402
from mcp_guard.approval.protocol import ApprovalRequest  # noqa: E402
from mcp_guard.approval.queue import PendingView  # noqa: E402
from mcp_guard.daemon.ui import ARM_DELAY_MS, ApprovalWindow, format_arguments  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    got = []
    w = ApprovalWindow(lambda rid, decision: got.append((rid, decision)))
    w.got = got
    yield w
    w._current_id = None
    w.close()
    w.deleteLater()  # let Qt delete it, not Python's garbage collector later
    QTest.qWait(10)


def view(rid="r1", server="files", tool="echo", args=None, timeout=45.0, method="tools/call"):
    request = ApprovalRequest(rid, "desktop", server, method, tool,
                              {"text": "hi"} if args is None else args, timeout)
    return PendingView(request, time.monotonic() + timeout)


def wait_armed():
    QTest.qWait(ARM_DELAY_MS + 200)


def test_shows_the_request(window):
    window.update_pending([view()])
    assert window.isVisible()
    assert window.heading.text() == "Claude wants to use a tool"
    assert window.source.text() == "Claude Desktop"
    assert window.server.text() == "files"
    assert window.tool.text() == "echo"
    assert '"text": "hi"' in window.arguments.toPlainText()


def test_buttons_do_nothing_during_the_first_second(window):
    window.update_pending([view()])
    assert not window.allow_button.isEnabled()
    window._decide(Decision.ALLOW)
    assert window.got == []
    wait_armed()
    assert window.allow_button.isEnabled()


def test_allow(window):
    window.update_pending([view()])
    wait_armed()
    window.allow_button.click()
    assert window.got == [("r1", Decision.ALLOW)]
    assert not window.allow_button.isEnabled()  # no double click


def test_block(window):
    window.update_pending([view()])
    wait_armed()
    window.block_button.click()
    assert window.got == [("r1", Decision.BLOCK)]


def test_block_is_the_default_button(window):
    window.update_pending([view()])
    assert window.block_button.isDefault()
    assert not window.allow_button.isDefault()


def test_escape_blocks_at_once(window):
    window.update_pending([view()])
    QTest.keyClick(window, Qt.Key.Key_Escape)
    assert window.got == [("r1", Decision.BLOCK)]


def test_closing_the_window_blocks(window):
    window.update_pending([view()])
    window.close()
    assert window.got == [("r1", Decision.BLOCK)]


def test_waiting_count_and_next_request(window):
    window.update_pending([view("a", server="one"), view("b", server="two"), view("c")])
    assert window.server.text() == "one"
    assert window.waiting.text() == "2 more waiting"
    wait_armed()
    window.update_pending([view("b", server="two"), view("c")])  # "a" was answered
    assert window.server.text() == "two"
    assert window.waiting.text() == "1 more waiting"
    assert not window.allow_button.isEnabled()  # the delay starts again for the new request


def test_empty_queue_hides_the_window(window):
    window.update_pending([view()])
    window.update_pending([])
    assert not window.isVisible()


def test_text_is_plain_never_html(window):
    window.update_pending([view(server="<b>evil</b>", tool="<a href='x'>t</a>")])
    assert window.server.textFormat() == Qt.TextFormat.PlainText
    assert window.server.text() == "<b>evil</b>"
    assert window.tool.textFormat() == Qt.TextFormat.PlainText


def test_countdown(window):
    window.update_pending([view(timeout=45)])
    assert window.countdown.text() in ("Blocks automatically in 45 s", "Blocks automatically in 44 s")


def test_other_method_heading(window):
    window.update_pending([view(method="resources/read", tool=None)])
    assert window.heading.text() == "Claude wants to call resources/read"
    assert window.tool.text() == "(none)"


def test_dock_icon_can_be_kept_for_debugging(monkeypatch):
    from mcp_guard.daemon.ui import SHOW_DOCK_ICON_ENV, hide_dock_icon

    monkeypatch.setenv(SHOW_DOCK_ICON_ENV, "1")
    assert hide_dock_icon() is False


def test_hide_dock_icon_does_nothing_off_macos(monkeypatch):
    from mcp_guard.daemon import ui

    monkeypatch.delenv(ui.SHOW_DOCK_ICON_ENV, raising=False)
    monkeypatch.setattr(ui.sys, "platform", "linux")
    assert ui.hide_dock_icon() is False


def test_format_arguments():
    assert "too large" in format_arguments({"_truncated": True, "preview": "abc"})
    assert "cannot be shown" in format_arguments({"_unprintable": True})
    assert format_arguments({"a": 1}) == '{\n  "a": 1\n}'
