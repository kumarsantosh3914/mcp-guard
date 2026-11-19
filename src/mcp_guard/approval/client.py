"""Proxy side: ask the daemon for a decision. Fails closed."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from pathlib import Path

from ..policy import RequestSummary
from .decider import Decision, Verdict
from .protocol import (
    MAX_MESSAGE_BYTES,
    ApprovalRequest,
    ProtocolError,
    VerdictMessage,
    default_socket_path,
    display_arguments,
)

log = logging.getLogger("mcp_guard.client")

NOT_RUNNING_TEXT = "Blocked by mcp-guard: the mcp-guard daemon is not running."
NO_ANSWER_TEXT = "Blocked by mcp-guard: the daemon closed the connection without an answer."
BAD_ANSWER_TEXT = "Blocked by mcp-guard: the daemon gave an answer that could not be read."


class DaemonDecider:
    """Asks the daemon over its Unix socket. One connection per request.

    If this coroutine is cancelled (timeout, or Claude cancelled the request),
    the connection is closed and the daemon drops the request.
    """

    def __init__(self, socket_path: Path | None = None, source: str = "desktop", timeout: float = 45.0) -> None:
        self.socket_path = socket_path or default_socket_path()
        self.source = source
        self.timeout = timeout

    async def decide(self, summary: RequestSummary) -> Verdict:
        request = ApprovalRequest(
            request_id=uuid.uuid4().hex,
            source=self.source,
            server=summary.server,
            method=summary.method,
            tool=summary.tool,
            arguments=display_arguments(summary.arguments),
            timeout=self.timeout,
        )
        try:
            reader, writer = await asyncio.open_unix_connection(
                str(self.socket_path), limit=MAX_MESSAGE_BYTES + 1)
        except OSError:
            log.warning("mcp-guard daemon is not running (socket %s)", self.socket_path)
            return Verdict(Decision.BLOCK, NOT_RUNNING_TEXT)
        try:
            writer.write(request.to_line())
            await writer.drain()
            line = await reader.readline()
            if not line:
                return Verdict(Decision.BLOCK, NO_ANSWER_TEXT)
            answer = VerdictMessage.parse(line)
            if answer.request_id != request.request_id:
                return Verdict(Decision.BLOCK, BAD_ANSWER_TEXT)
            return Verdict(Decision(answer.decision), answer.reason)
        except (ProtocolError, ValueError, OSError):
            return Verdict(Decision.BLOCK, BAD_ANSWER_TEXT)
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
