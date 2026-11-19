"""PySide6 popup and menu bar icon for the daemon.

Safety rules for the popup:
- Block is the focused button. Enter never means Allow.
- For the first second after a request appears, the buttons do nothing.
  This stops a click or key press meant for another app from approving it.
- Esc or closing the window blocks the request.
- All text from requests is shown as plain text. Never as HTML.
"""
from __future__ import annotations

import json
import logging
import signal
import time
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ..approval.decider import Decision
from ..approval.queue import PendingView
from .runtime import DaemonRuntime
from .server import AlreadyRunning, DaemonError

log = logging.getLogger("mcp_guard.ui")

ARM_DELAY_MS = 1000
MAX_ARGUMENT_CHARS = 20000
SOURCE_NAMES = {"desktop": "Claude Desktop", "web": "Claude Web / remote connector"}


def format_arguments(arguments: Any) -> str:
    if isinstance(arguments, dict) and arguments.get("_truncated"):
        return str(arguments.get("preview", "")) + "\n\n[Arguments are too large to show in full.]"
    if isinstance(arguments, dict) and arguments.get("_unprintable"):
        return "[Arguments cannot be shown.]"
    try:
        text = json.dumps(arguments, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = repr(arguments)
    if len(text) > MAX_ARGUMENT_CHARS:
        text = text[:MAX_ARGUMENT_CHARS] + "\n\n[Shortened for display.]"
    return text


def _plain_label(bold: bool = False) -> QLabel:
    label = QLabel("")
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if bold:
        font = label.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 3)
        label.setFont(font)
    return label


class ApprovalWindow(QWidget):
    """Shows the oldest pending request. The queue tells it when anything changes."""

    def __init__(self, on_decide: Callable[[str, Decision], None]) -> None:
        super().__init__(
            None,
            Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint,
        )
        self._on_decide = on_decide
        self._views: list[PendingView] = []
        self._current_id: str | None = None
        self._armed = False

        self.setWindowTitle("mcp-guard: approval needed")
        self.setMinimumWidth(560)

        self.heading = _plain_label(bold=True)
        self.source = _plain_label()
        self.server = _plain_label()
        self.tool = _plain_label()
        self.arguments = QPlainTextEdit()
        self.arguments.setReadOnly(True)
        self.arguments.setMinimumHeight(180)
        self.arguments.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.countdown = _plain_label()
        self.waiting = _plain_label()
        self.block_button = QPushButton("Block")
        self.allow_button = QPushButton("Allow")
        self.block_button.setDefault(True)
        self.block_button.clicked.connect(lambda: self._decide(Decision.BLOCK))
        self.allow_button.clicked.connect(lambda: self._decide(Decision.ALLOW))

        details = QGridLayout()
        for row, (name, widget) in enumerate(
            [("From", self.source), ("Server", self.server), ("Tool", self.tool)]
        ):
            details.addWidget(QLabel(name + ":"), row, 0, Qt.AlignmentFlag.AlignTop)
            details.addWidget(widget, row, 1)
        details.setColumnStretch(1, 1)

        buttons = QHBoxLayout()
        buttons.addWidget(self.countdown)
        buttons.addStretch(1)
        buttons.addWidget(self.block_button)
        buttons.addWidget(self.allow_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.heading)
        layout.addLayout(details)
        layout.addWidget(QLabel("Arguments:"))
        layout.addWidget(self.arguments)
        layout.addWidget(self.waiting)
        layout.addLayout(buttons)

        self._tick = QTimer(self)
        self._tick.setInterval(200)
        self._tick.timeout.connect(self._update_countdown)

        # Owned by the window, so it can never fire after the window is gone.
        self._arm_timer = QTimer(self)
        self._arm_timer.setSingleShot(True)
        self._arm_timer.setInterval(ARM_DELAY_MS)
        self._arm_timer.timeout.connect(self._arm_current)

    # ---- called by the queue (through a Qt signal) ----

    def update_pending(self, views: list[PendingView]) -> None:
        self._views = list(views)
        if not self._views:
            self._current_id = None
            self._tick.stop()
            self._arm_timer.stop()
            self.hide()
            return
        head = self._views[0]
        if head.request.request_id != self._current_id:
            self._current_id = head.request.request_id
            self._show_request(head)
        count = len(self._views) - 1
        self.waiting.setText("%d more waiting" % count if count else "")
        self._update_countdown()

    # ---- internals ----

    def _show_request(self, view: PendingView) -> None:
        r = view.request
        if r.method == "tools/call":
            self.heading.setText("Claude wants to use a tool")
        else:
            self.heading.setText("Claude wants to call " + r.method)
        self.source.setText(SOURCE_NAMES.get(r.source, r.source))
        self.server.setText(r.server)
        self.tool.setText(r.tool or "(none)")
        self.arguments.setPlainText(format_arguments(r.arguments))
        self._set_armed(False)
        self._arm_timer.start()  # restarts the delay for every new request
        self._tick.start()
        self.show()
        self.raise_()
        self.activateWindow()
        self.block_button.setFocus()

    def _arm_current(self) -> None:
        if self._current_id is not None:
            self._set_armed(True)

    def _set_armed(self, armed: bool) -> None:
        self._armed = armed
        self.block_button.setEnabled(armed)
        self.allow_button.setEnabled(armed)

    def _decide(self, decision: Decision) -> None:
        if not self._armed or self._current_id is None:
            return
        self._send(decision)

    def _block_now(self) -> None:
        """Blocking is always safe, so Esc and close work even before the delay ends."""
        if self._current_id is not None:
            self._send(Decision.BLOCK)

    def _send(self, decision: Decision) -> None:
        request_id = self._current_id
        self._set_armed(False)
        self._on_decide(request_id, decision)

    def _update_countdown(self) -> None:
        if self._views:
            left = max(0, int(self._views[0].deadline - time.monotonic() + 0.999))
            self.countdown.setText("Blocks automatically in %d s" % left)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt name)
        if event.key() == Qt.Key.Key_Escape:
            self._block_now()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt name)
        if self._current_id is not None:
            event.ignore()  # the queue update hides the window or shows the next request
            self._block_now()
        else:
            event.accept()


def make_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#2f6fdf"))
    painter.drawEllipse(4, 4, 56, 56)
    painter.setPen(QColor("white"))
    font = QFont()
    font.setBold(True)
    font.setPixelSize(34)
    painter.setFont(font)
    painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "G")
    painter.end()
    return QIcon(pix)


class Tray:
    """Menu bar icon: shows that the daemon runs and how many requests wait."""

    def __init__(self, on_quit: Callable[[], None]) -> None:
        self.icon = QSystemTrayIcon(make_icon())
        self.icon.setToolTip("mcp-guard")
        self.menu = QMenu()
        self.status = QAction("mcp-guard is running")
        self.status.setEnabled(False)
        self.pending = QAction("No requests waiting")
        self.pending.setEnabled(False)
        self.quit = QAction("Quit mcp-guard (blocks all requests)")
        self.quit.triggered.connect(on_quit)
        for action in (self.status, self.pending):
            self.menu.addAction(action)
        self.menu.addSeparator()
        self.menu.addAction(self.quit)
        self.icon.setContextMenu(self.menu)
        self.icon.show()

    def update(self, views: list[PendingView]) -> None:
        n = len(views)
        self.pending.setText("%d request%s waiting" % (n, "" if n == 1 else "s") if n else "No requests waiting")


class Bridge(QObject):
    """Moves queue updates from the asyncio thread to the Qt thread."""

    changed = Signal(object)


def run_daemon(socket_path: Path) -> int:
    app = QApplication.instance() or QApplication(["mcp-guard"])
    app.setApplicationName("mcp-guard")
    app.setQuitOnLastWindowClosed(False)

    bridge = Bridge()
    runtime = DaemonRuntime(socket_path, on_change=bridge.changed.emit)
    window = ApprovalWindow(on_decide=runtime.decide)
    bridge.changed.connect(window.update_pending)
    tray = Tray(on_quit=app.quit) if QSystemTrayIcon.isSystemTrayAvailable() else None
    if tray is not None:
        bridge.changed.connect(tray.update)

    try:
        runtime.start()
    except AlreadyRunning as exc:
        log.error("%s", exc)
        return 1
    except (DaemonError, OSError) as exc:
        log.error("could not start the daemon: %s", exc)
        return 1

    # Qt's event loop hides signals from Python. A small timer lets Python run its handlers.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    keepalive = QTimer()
    keepalive.timeout.connect(lambda: None)
    keepalive.start(250)

    try:
        return app.exec()
    finally:
        runtime.stop()  # blocks every pending request and removes the socket
