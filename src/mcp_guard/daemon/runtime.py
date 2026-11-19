"""Runs the asyncio side of the daemon (socket server and queue) in a background thread.

The UI runs Qt in the main thread. It must not touch the queue directly. It calls
`decide()`, which hands the call to the asyncio thread. Queue changes reach the UI
through the `on_change` callback, which is called in the asyncio thread; the UI
moves them to its own thread (with a Qt signal).

No Qt code here, so this can be tested without a screen.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Callable

from ..approval.decider import Decision
from ..approval.queue import ApprovalQueue, PendingView
from ..audit import AuditLog
from .server import DaemonServer

log = logging.getLogger("mcp_guard.runtime")

START_TIMEOUT_SECONDS = 10.0
STOP_TIMEOUT_SECONDS = 10.0


class DaemonRuntime:
    def __init__(
        self,
        socket_path: Path,
        on_change: Callable[[list[PendingView]], None] | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self.socket_path = Path(socket_path)
        self._on_change = on_change
        self._audit = audit
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="mcp-guard-daemon", daemon=True)
        self.queue: ApprovalQueue | None = None
        self.server: DaemonServer | None = None

    def start(self) -> None:
        """Start the thread and wait until the socket is ready. Raises if it cannot start."""
        self._thread.start()
        if not self._ready.wait(START_TIMEOUT_SECONDS):
            raise RuntimeError("daemon did not start in time")
        if self._error is not None:
            raise self._error

    def decide(self, request_id: str, decision: Decision) -> None:
        """Safe to call from any thread."""
        try:
            self._loop.call_soon_threadsafe(self._decide, request_id, decision)
        except RuntimeError:  # loop already closed
            log.warning("decision arrived after the daemon stopped")

    def stop(self) -> None:
        """Block every pending request, close the socket, and end the thread."""
        if self._thread.is_alive():
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except RuntimeError:
                pass
            self._thread.join(STOP_TIMEOUT_SECONDS)

    def _decide(self, request_id: str, decision: Decision) -> None:
        if self.queue is not None:
            self.queue.decide(request_id, decision)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self.queue = ApprovalQueue(on_change=self._on_change)
        self.server = DaemonServer(self.queue, self.socket_path, self._audit)
        try:
            self._loop.run_until_complete(self.server.start())
        except BaseException as exc:
            self._error = exc
            self._ready.set()
            self._loop.close()
            return
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            self._loop.run_until_complete(self.server.close())
            self._loop.close()
