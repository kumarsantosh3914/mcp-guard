"""Pending approval requests, their timers, and their answers.

No GUI code here, so it can be tested without a screen. All methods must be
called from the event loop thread that owns the queue. Other threads (the UI)
use `loop.call_soon_threadsafe`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable

from ..jsonrpc import BLOCKED_TEXT
from .decider import Decision, Verdict
from .protocol import ApprovalRequest

log = logging.getLogger("mcp_guard.queue")

ALLOWED_TEXT = "Allowed by the user."
TOO_MANY_TEXT = "Blocked by mcp-guard: too many requests are waiting."
STOPPING_TEXT = "Blocked by mcp-guard: the mcp-guard daemon is stopping."
DEFAULT_MAX_PENDING = 50


@dataclass(frozen=True)
class PendingView:
    """What the UI needs to show one pending request."""

    request: ApprovalRequest
    deadline: float  # time.monotonic() value when the request is blocked


@dataclass
class _Entry:
    view: PendingView
    future: asyncio.Future
    timer: asyncio.TimerHandle


class ApprovalQueue:
    def __init__(
        self,
        on_change: Callable[[list[PendingView]], None] | None = None,
        max_pending: int = DEFAULT_MAX_PENDING,
    ) -> None:
        self._on_change = on_change
        self.max_pending = max_pending
        self._entries: dict[str, _Entry] = {}  # keeps arrival order

    def __len__(self) -> int:
        return len(self._entries)

    def submit(self, request: ApprovalRequest) -> asyncio.Future:
        """Add a request. The returned future gets a Verdict. The timer starts now."""
        if request.request_id in self._entries:
            raise ValueError("duplicate request id")
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        if len(self._entries) >= self.max_pending:
            future.set_result(Verdict(Decision.BLOCK, TOO_MANY_TEXT))
            return future
        timer = loop.call_later(request.timeout, self._expire, request.request_id)
        view = PendingView(request, time.monotonic() + request.timeout)
        self._entries[request.request_id] = _Entry(view, future, timer)
        self._notify()
        return future

    def decide(self, request_id: str, decision: Decision) -> bool:
        """The user's answer. Returns False if the request is no longer pending."""
        reason = ALLOWED_TEXT if decision is Decision.ALLOW else BLOCKED_TEXT
        return self._resolve(request_id, Verdict(decision, reason))

    def cancel(self, request_id: str) -> bool:
        """The proxy went away. Drop the request without an answer."""
        entry = self._entries.pop(request_id, None)
        if entry is None:
            return False
        entry.timer.cancel()
        if not entry.future.done():
            entry.future.cancel()
        self._notify()
        return True

    def block_all(self, reason: str = STOPPING_TEXT) -> None:
        for request_id in list(self._entries):
            self._resolve(request_id, Verdict(Decision.BLOCK, reason))

    def snapshot(self) -> list[PendingView]:
        """Pending requests, oldest first."""
        return [entry.view for entry in self._entries.values()]

    def _expire(self, request_id: str) -> None:
        entry = self._entries.get(request_id)
        if entry is not None:
            timeout = entry.view.request.timeout
            self._resolve(request_id, Verdict(
                Decision.BLOCK, "Blocked by mcp-guard: no answer within %g seconds." % timeout))

    def _resolve(self, request_id: str, verdict: Verdict) -> bool:
        entry = self._entries.pop(request_id, None)
        if entry is None:
            return False
        entry.timer.cancel()
        if not entry.future.done():
            entry.future.set_result(verdict)
        self._notify()
        return True

    def _notify(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change(self.snapshot())
        except Exception:  # a UI problem must not break the queue
            log.exception("on_change callback failed")
