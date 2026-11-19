"""stdio proxy: sits between Claude Desktop and one real MCP server.

Claude Desktop starts this proxy in place of the real server. The proxy starts
the real server as a child process and relays MCP messages (one JSON line each)
in both directions. Requests that need approval are held until the decider
answers. Everything fails closed.

stdout carries only MCP messages. All logs go to stderr.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import Any

from .. import jsonrpc
from ..approval.decider import Decider, decide_with_timeout
from ..jsonrpc import Kind, Message
from ..policy import Policy, summarize

log = logging.getLogger("mcp_guard.proxy")

MAX_LINE_BYTES = 16 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 45.0
STOP_GRACE_SECONDS = 5.0

BATCH_REJECTED = (
    "mcp-guard does not accept batches that contain requests needing approval. "
    "Send them one at a time."
)
SERVER_STOPPED = "The MCP server stopped before it answered."
SERVER_UNAVAILABLE = "The MCP server is not available."
TOO_LARGE = "The message is larger than mcp-guard allows."
DUPLICATE_ID = "A request with this id is already pending."


def _ensure_newline(line: bytes) -> bytes:
    return line if line.endswith(b"\n") else line + b"\n"


def _safe_id(msg: Message) -> Any:
    rid = msg.id
    return rid if isinstance(rid, (str, int)) and not isinstance(rid, bool) else None


class LineWriter:
    """Writes whole lines. One lock, so lines from different tasks never mix."""

    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self._writer = writer
        self._lock = asyncio.Lock()

    async def write(self, data: bytes) -> bool:
        async with self._lock:
            if self._writer.is_closing():
                return False
            try:
                self._writer.write(data)
                await self._writer.drain()
            except OSError:
                return False
            return True

    def close(self) -> None:
        if not self._writer.is_closing():
            self._writer.close()


class StdioProxy:
    def __init__(
        self,
        server_name: str,
        command: list[str],
        decider: Decider,
        policy: Policy | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not command:
            raise ValueError("server command is empty")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.server_name = server_name
        self.command = list(command)
        self.decider = decider
        self.policy = policy or Policy()
        self.timeout = timeout
        self._approvals: dict[Any, asyncio.Task] = {}  # request id -> task waiting for a verdict
        self._forwarded: set[Any] = set()  # request ids sent to the server, not yet answered
        self._proc: asyncio.subprocess.Process | None = None
        self._client: LineWriter | None = None
        self._server: LineWriter | None = None

    # ---- lifecycle ----

    async def run(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> int:
        """Relay until either side closes. Returns the exit code for the proxy process."""
        self._client = LineWriter(client_writer)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=None,  # the server's logs go to the same place as ours
                limit=MAX_LINE_BYTES,
            )
        except OSError as exc:
            log.error("could not start MCP server %r: %s", self.server_name, exc)
            return 127
        self._server = LineWriter(self._proc.stdin)
        log.info("started MCP server %r (pid %d)", self.server_name, self._proc.pid)

        client_task = asyncio.create_task(self._pump_client(client_reader), name="client")
        server_task = asyncio.create_task(self._pump_server(), name="server")
        done, _ = await asyncio.wait({client_task, server_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if not task.cancelled() and task.exception() is not None:
                log.error("%s relay failed", task.get_name(), exc_info=task.exception())

        if server_task in done:
            log.warning("MCP server %r closed its output", self.server_name)
            client_task.cancel()
            await self._fail_pending(SERVER_STOPPED)
        else:
            log.info("client closed the connection")
            await self._cancel_approvals()
            server_task.cancel()
        self._server.close()
        code = await self._stop_server()
        await asyncio.gather(client_task, server_task, return_exceptions=True)
        log.info("MCP server %r exited with code %s", self.server_name, code)
        return code if code >= 0 else 128 - code  # killed by a signal: 128 + signal number

    def request_stop(self) -> None:
        """Called on SIGTERM or SIGINT. Stopping the server ends the relay."""
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass

    async def _stop_server(self) -> int:
        proc = self._proc
        assert proc is not None
        try:
            return await asyncio.wait_for(proc.wait(), STOP_GRACE_SECONDS)
        except TimeoutError:
            pass
        for action in (proc.terminate, proc.kill):
            try:
                action()
            except ProcessLookupError:
                pass
            try:
                return await asyncio.wait_for(proc.wait(), STOP_GRACE_SECONDS)
            except TimeoutError:
                continue
        return await proc.wait()

    async def _cancel_approvals(self) -> None:
        tasks = list(self._approvals.values())
        self._approvals.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _fail_pending(self, reason: str) -> None:
        """Answer every open request with an error, so Claude does not wait for nothing."""
        ids = list(self._approvals) + [i for i in self._forwarded if i not in self._approvals]
        await self._cancel_approvals()
        self._forwarded.clear()
        for rid in ids:
            await self._reply(jsonrpc.make_error(rid, jsonrpc.INTERNAL_ERROR, reason))

    # ---- client to server ----

    async def _pump_client(self, reader: asyncio.StreamReader) -> None:
        while True:
            try:
                line = await reader.readline()
            except ValueError:  # longer than MAX_LINE_BYTES; the stream drops it
                log.warning("dropped a client message larger than %d bytes", MAX_LINE_BYTES)
                await self._reply(jsonrpc.make_error(None, jsonrpc.INVALID_REQUEST, TOO_LARGE))
                continue
            if not line:
                return  # end of input
            if line.strip():
                await self._handle_client_line(line)

    async def _handle_client_line(self, line: bytes) -> None:
        try:
            msg = jsonrpc.parse_line(line)
        except jsonrpc.ParseError:
            log.warning("dropped a client message that is not valid JSON")
            await self._reply(jsonrpc.make_error(None, jsonrpc.PARSE_ERROR, "Parse error"))
            return

        if msg.kind is Kind.INVALID:
            log.warning("dropped an invalid client message")
            await self._reply(jsonrpc.make_error(_safe_id(msg), jsonrpc.INVALID_REQUEST, "Invalid request"))
            return

        if msg.kind is Kind.BATCH:
            await self._handle_batch(msg, line)
            return

        cancel_id = jsonrpc.cancelled_request_id(msg)
        if cancel_id is not None and cancel_id in self._approvals:
            # The server never saw this request, so it must not see the cancel either.
            self._approvals.pop(cancel_id).cancel()
            log.info("request %r was cancelled while waiting for approval", cancel_id)
            return

        if self.policy.needs_approval(msg):
            if msg.id in self._approvals or msg.id in self._forwarded:
                await self._reply(jsonrpc.make_error(msg.id, jsonrpc.INVALID_REQUEST, DUPLICATE_ID))
                return
            self._approvals[msg.id] = asyncio.create_task(self._approve(msg, line))
            return

        await self._forward(msg, line)

    async def _handle_batch(self, msg: Message, line: bytes) -> None:
        if self.policy.gated_requests(msg):
            log.warning("rejected a batch that contains requests needing approval")
            errors = [
                jsonrpc.make_error(item.id, jsonrpc.REQUEST_BLOCKED, BATCH_REJECTED)
                for item in msg.items
                if item.kind is Kind.REQUEST
            ]
            await self._reply(errors)
            return
        ids = [item.id for item in msg.items if item.kind is Kind.REQUEST]
        self._forwarded.update(ids)
        assert self._server is not None
        if not await self._server.write(_ensure_newline(line)):
            self._forwarded.difference_update(ids)
            if ids:
                await self._reply([jsonrpc.make_error(i, jsonrpc.INTERNAL_ERROR, SERVER_UNAVAILABLE) for i in ids])

    async def _approve(self, msg: Message, line: bytes) -> None:
        try:
            summary = summarize(msg, self.server_name)
            verdict = await decide_with_timeout(self.decider, summary, self.timeout)
        except asyncio.CancelledError:
            raise
        except Exception:  # fail closed: never leave a request hanging
            log.exception("approval failed for request %r", msg.id)
            if self._approvals.get(msg.id) is asyncio.current_task():
                del self._approvals[msg.id]
                await self._reply(jsonrpc.blocked_response(msg, "Blocked by mcp-guard: internal error."))
            return
        if self._approvals.get(msg.id) is not asyncio.current_task():
            return  # cancelled or answered elsewhere
        del self._approvals[msg.id]
        if verdict.allowed:
            log.info("allowed %s %s on %r", msg.method, summary.tool, self.server_name)
            await self._forward(msg, line)
        else:
            log.info("blocked %s %s on %r: %s", msg.method, summary.tool, self.server_name, verdict.reason)
            await self._reply(jsonrpc.blocked_response(msg, verdict.reason))

    async def _forward(self, msg: Message, line: bytes) -> None:
        if msg.kind is Kind.REQUEST:
            self._forwarded.add(msg.id)
        assert self._server is not None
        if not await self._server.write(_ensure_newline(line)):
            if msg.kind is Kind.REQUEST:
                self._forwarded.discard(msg.id)
                await self._reply(jsonrpc.make_error(msg.id, jsonrpc.INTERNAL_ERROR, SERVER_UNAVAILABLE))

    # ---- server to client ----

    async def _pump_server(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            try:
                line = await self._proc.stdout.readline()
            except ValueError:
                log.warning("dropped a server message larger than %d bytes", MAX_LINE_BYTES)
                continue
            if not line:
                return  # the server closed its output
            if not line.strip():
                continue
            try:
                msg = jsonrpc.parse_line(line)
            except jsonrpc.ParseError:
                # Often a server that prints logs to stdout by mistake. Claude cannot use it.
                log.warning("dropped a server line that is not valid JSON")
                continue
            self._untrack(msg)
            await self._client_write(_ensure_newline(line))

    def _untrack(self, msg: Message) -> None:
        items = msg.items if msg.kind is Kind.BATCH else (msg,)
        for item in items:
            if item.kind is Kind.RESPONSE:
                self._forwarded.discard(item.id)

    # ---- helpers ----

    async def _client_write(self, data: bytes) -> None:
        assert self._client is not None
        if not await self._client.write(data):
            log.warning("could not write to the client")

    async def _reply(self, obj: Any) -> None:
        await self._client_write(jsonrpc.encode(obj))


async def open_stdio() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Wrap this process's stdin and stdout as asyncio streams (binary)."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=MAX_LINE_BYTES)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
    transport, protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, sys.stdout.buffer)
    writer = asyncio.StreamWriter(transport, protocol, None, loop)
    return reader, writer


async def run_stdio_proxy(proxy: StdioProxy) -> int:
    reader, writer = await open_stdio()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, proxy.request_stop)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        return await proxy.run(reader, writer)
    finally:
        writer.close()
