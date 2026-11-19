"""Gateway sessions.

Each MCP session (one `initialize` from Claude Web) gets its own child process of
the real stdio server, because a stdio server serves exactly one client.

Messages arrive one per HTTP POST. A request waits for the decider (if gated),
then for the server's answer with the same id. Server-to-client messages other
than responses are dropped in this version (the gateway has no GET stream).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
import time
from typing import Any

from .. import jsonrpc
from ..approval.decider import Decider, decide_with_timeout
from ..config import ServerSpec
from ..jsonrpc import Kind, Message
from ..policy import Policy, summarize
from ..proxy.stdio_proxy import (
    DUPLICATE_ID,
    MAX_LINE_BYTES,
    SERVER_STOPPED,
    SERVER_UNAVAILABLE,
    STOP_GRACE_SECONDS,
    LineWriter,
)

log = logging.getLogger("mcp_guard.gateway")

UPSTREAM_TIMEOUT_SECONDS = 120.0
DEFAULT_IDLE_SECONDS = 30 * 60
DEFAULT_MAX_SESSIONS = 10
REQUEST_CANCELLED = -32800
CANCELLED_TEXT = "Request cancelled."
NO_ANSWER_TEXT = "The MCP server did not answer in time."


class TooManySessions(RuntimeError):
    pass


def _line(raw: bytes) -> bytes:
    raw = raw.strip()
    return raw + b"\n"


class UpstreamSession:
    def __init__(self, server_name: str, spec: ServerSpec, decider: Decider, policy: Policy, timeout: float) -> None:
        self.server_name = server_name
        self.spec = spec
        self.decider = decider
        self.policy = policy
        self.timeout = timeout
        self.id = secrets.token_urlsafe(24)
        self.last_used = time.monotonic()
        self.active = 0  # requests in progress; a busy session is never closed as idle
        self.closed = False
        self._proc: asyncio.subprocess.Process | None = None
        self._writer: LineWriter | None = None
        self._reader_task: asyncio.Task | None = None
        self._pending: dict[Any, asyncio.Future] = {}
        self._approvals: dict[Any, asyncio.Task] = {}
        self._background: set[asyncio.Task] = set()

    async def start(self) -> None:
        env = dict(os.environ)
        env.update(self.spec.env)
        self._proc = await asyncio.create_subprocess_exec(
            self.spec.command, *self.spec.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,
            env=env,
            limit=MAX_LINE_BYTES,
        )
        self._writer = LineWriter(self._proc.stdin)
        self._reader_task = asyncio.create_task(self._read_loop())
        log.info("started %r for a new session (pid %d)", self.server_name, self._proc.pid)

    # ---- one message from the client ----

    async def handle(self, msg: Message, raw: bytes) -> dict[str, Any] | None:
        """Returns the JSON-RPC answer for a request, or None for a notification or response."""
        self.active += 1
        self.last_used = time.monotonic()
        try:
            return await self._handle(msg, raw)
        finally:
            self.active -= 1
            self.last_used = time.monotonic()

    async def _handle(self, msg: Message, raw: bytes) -> dict[str, Any] | None:
        if self.closed:
            return jsonrpc.make_error(msg.id, jsonrpc.INTERNAL_ERROR, SERVER_STOPPED) if msg.kind is Kind.REQUEST else None

        cancel_id = jsonrpc.cancelled_request_id(msg)
        if cancel_id is not None and cancel_id in self._approvals:
            self._approvals.pop(cancel_id).cancel()  # the server never saw it
            return None

        if msg.kind is not Kind.REQUEST:
            await self._write(raw)
            return None

        if msg.id in self._pending or msg.id in self._approvals:
            return jsonrpc.make_error(msg.id, jsonrpc.INVALID_REQUEST, DUPLICATE_ID)

        if self.policy.needs_approval(msg):
            summary = summarize(msg, self.server_name)
            task = asyncio.create_task(decide_with_timeout(self.decider, summary, self.timeout))
            self._approvals[msg.id] = task
            try:
                verdict = await task
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise  # the HTTP client went away; the decider task is cancelled with us
                return jsonrpc.make_error(msg.id, REQUEST_CANCELLED, CANCELLED_TEXT)
            finally:
                if self._approvals.get(msg.id) is task:
                    del self._approvals[msg.id]
            if not verdict.allowed:
                log.info("blocked %s %s on %r: %s", msg.method, summary.tool, self.server_name, verdict.reason)
                return jsonrpc.blocked_response(msg, verdict.reason)
            log.info("allowed %s %s on %r", msg.method, summary.tool, self.server_name)

        return await self._call(msg, raw)

    async def _call(self, msg: Message, raw: bytes) -> dict[str, Any]:
        if self.closed:
            return jsonrpc.make_error(msg.id, jsonrpc.INTERNAL_ERROR, SERVER_STOPPED)
        future = asyncio.get_running_loop().create_future()
        self._pending[msg.id] = future
        try:
            if not await self._write(raw):
                return jsonrpc.make_error(msg.id, jsonrpc.INTERNAL_ERROR, SERVER_UNAVAILABLE)
            return await asyncio.wait_for(future, UPSTREAM_TIMEOUT_SECONDS)
        except TimeoutError:
            return jsonrpc.make_error(msg.id, jsonrpc.INTERNAL_ERROR, NO_ANSWER_TEXT)
        except asyncio.CancelledError:
            self._tell_server_cancelled(msg.id)
            raise
        finally:
            self._pending.pop(msg.id, None)

    def _tell_server_cancelled(self, request_id: Any) -> None:
        note = {"jsonrpc": "2.0", "method": "notifications/cancelled",
                "params": {"requestId": request_id, "reason": "The client went away."}}
        task = asyncio.create_task(self._write(jsonrpc.encode(note)))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _write(self, raw: bytes) -> bool:
        assert self._writer is not None
        return await self._writer.write(_line(raw))

    # ---- server output ----

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                try:
                    line = await self._proc.stdout.readline()
                except ValueError:
                    log.warning("dropped a server message larger than %d bytes", MAX_LINE_BYTES)
                    continue
                if not line:
                    break
                if not line.strip():
                    continue
                try:
                    msg = jsonrpc.parse_line(line)
                except jsonrpc.ParseError:
                    log.warning("dropped a server line that is not valid JSON")
                    continue
                items = msg.items if msg.kind is Kind.BATCH else (msg,)
                for item in items:
                    if item.kind is Kind.RESPONSE:
                        future = self._pending.get(item.id)
                        if future is not None and not future.done():
                            future.set_result(item.raw)
                    else:
                        log.debug("dropped a server-to-client message (%s): not supported over the gateway",
                                  item.method)
        finally:
            self.closed = True
            for rid, future in list(self._pending.items()):
                if not future.done():
                    future.set_result(jsonrpc.make_error(rid, jsonrpc.INTERNAL_ERROR, SERVER_STOPPED))

    # ---- shutdown ----

    async def close(self) -> None:
        self.closed = True
        for task in list(self._approvals.values()):
            task.cancel()
        if self._proc is None:
            return
        if self._writer is not None:
            self._writer.close()
        proc = self._proc
        try:
            await asyncio.wait_for(proc.wait(), STOP_GRACE_SECONDS)
        except TimeoutError:
            for action in (proc.terminate, proc.kill):
                with contextlib.suppress(ProcessLookupError):
                    action()
                try:
                    await asyncio.wait_for(proc.wait(), STOP_GRACE_SECONDS)
                    break
                except TimeoutError:
                    continue
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        log.info("closed a session of %r", self.server_name)


class SessionManager:
    def __init__(
        self,
        servers: dict[str, ServerSpec],
        decider: Decider,
        policy: Policy,
        timeout: float,
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
    ) -> None:
        self.servers = servers
        self.decider = decider
        self.policy = policy
        self.timeout = timeout
        self.idle_seconds = idle_seconds
        self.max_sessions = max_sessions
        self._sessions: dict[tuple[str, str], UpstreamSession] = {}

    def __len__(self) -> int:
        return len(self._sessions)

    async def create(self, server_name: str) -> UpstreamSession:
        """Start a new session. Raises KeyError for an unknown server, TooManySessions, or OSError."""
        spec = self.servers[server_name]
        await self.close_idle()
        open_for_server = sum(1 for (name, _), s in self._sessions.items() if name == server_name)
        if open_for_server >= self.max_sessions:
            raise TooManySessions(server_name)
        session = UpstreamSession(server_name, spec, self.decider, self.policy, self.timeout)
        await session.start()
        self._sessions[(server_name, session.id)] = session
        return session

    def get(self, server_name: str, session_id: str) -> UpstreamSession | None:
        session = self._sessions.get((server_name, session_id))
        if session is not None and session.closed and session.active == 0:
            return None
        return session

    async def close(self, server_name: str, session_id: str) -> bool:
        session = self._sessions.pop((server_name, session_id), None)
        if session is None:
            return False
        await session.close()
        return True

    async def close_idle(self) -> None:
        now = time.monotonic()
        for key, session in list(self._sessions.items()):
            idle = now - session.last_used > self.idle_seconds
            if session.active == 0 and (session.closed or idle):
                self._sessions.pop(key, None)
                await session.close()

    async def close_all(self) -> None:
        sessions = list(self._sessions.values())
        self._sessions.clear()
        await asyncio.gather(*(s.close() for s in sessions), return_exceptions=True)
