"""Unix socket server: receives approval requests from proxies and answers them.

Security:
- The socket lives in a folder only this user can open (mode 700), and the
  socket itself is mode 600. Other users and sandboxed apps cannot connect.
- Only one daemon runs at a time. A live socket means another daemon is running.
- A leftover socket from a crashed daemon is replaced. Any other kind of file at
  that path is left alone, and the daemon refuses to start.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import stat
from pathlib import Path

from .. import __version__
from ..approval import protocol
from ..approval.queue import ApprovalQueue
from ..audit import AuditLog

log = logging.getLogger("mcp_guard.daemon")

READ_TIMEOUT_SECONDS = 5.0
CANCELLED_TEXT = "Cancelled by Claude before a decision."


class DaemonError(RuntimeError):
    pass


class AlreadyRunning(DaemonError):
    pass


def ensure_private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = path.stat()
    if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
        raise DaemonError("%s must be owned by you and private (chmod 700)" % path)


async def socket_is_alive(path: Path) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(path)), 1.0)
    except (OSError, TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return True


class DaemonServer:
    def __init__(self, queue: ApprovalQueue, socket_path: Path, audit: AuditLog | None = None) -> None:
        self.queue = queue
        self.path = Path(socket_path)
        self.audit = audit
        self._server: asyncio.base_events.Server | None = None
        self._closed = False

    async def start(self) -> None:
        ensure_private_dir(self.path.parent)
        if self.path.exists() or self.path.is_socket():
            if await socket_is_alive(self.path):
                raise AlreadyRunning("another mcp-guard daemon is already running on %s" % self.path)
            if not self.path.is_socket():
                raise DaemonError("%s exists and is not a socket. Refusing to touch it." % self.path)
            log.info("replacing a leftover socket from an earlier daemon")
            self.path.unlink()
        self._server = await asyncio.start_unix_server(
            self._handle, path=str(self.path), limit=protocol.MAX_MESSAGE_BYTES + 1)
        os.chmod(self.path, 0o600)
        log.info("mcp-guard %s daemon listening on %s", __version__, self.path)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.queue.block_all()  # answer everyone first, so open connections can finish
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            if self.path.is_socket():
                self.path.unlink(missing_ok=True)
        log.info("daemon stopped")

    def _record(self, request: protocol.ApprovalRequest, decision: str, reason: str, started: float) -> None:
        if self.audit is not None:
            self.audit.record(request, decision, reason, asyncio.get_running_loop().time() - started)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = None
        recorded = False
        started = asyncio.get_running_loop().time()
        try:
            try:
                line = await asyncio.wait_for(reader.readline(), READ_TIMEOUT_SECONDS)
            except (TimeoutError, ValueError, OSError):
                return
            if not line:
                return
            try:
                request = protocol.ApprovalRequest.parse(line)
            except protocol.ProtocolError as exc:
                log.warning("rejected a bad request: %s", exc)
                return
            try:
                future = self.queue.submit(request)
            except ValueError:
                log.warning("rejected a request with a duplicate id")
                request = None
                return
            log.info("waiting for a decision: %s %s on %r", request.method, request.tool, request.server)

            # Wait for the user's answer, or for the proxy to hang up (that means cancel).
            hang_up = asyncio.ensure_future(reader.read(1))
            try:
                done, _ = await asyncio.wait({future, hang_up}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                hang_up.cancel()
            if future in done and not future.cancelled():
                verdict = future.result()
                log.info("%s: %s %s on %r", verdict.decision.value, request.method, request.tool, request.server)
                self._record(request, verdict.decision.value, verdict.reason, started)
                recorded = True
                writer.write(protocol.VerdictMessage(
                    request.request_id, verdict.decision.value, verdict.reason).to_line())
                await writer.drain()
            else:
                self.queue.cancel(request.request_id)
                log.info("request was cancelled by the proxy")
                self._record(request, "cancelled", CANCELLED_TEXT, started)
                recorded = True
        except OSError:
            if request is not None:
                self.queue.cancel(request.request_id)
                if not recorded:
                    self._record(request, "cancelled", CANCELLED_TEXT, started)
        except asyncio.CancelledError:
            if request is not None:
                self.queue.cancel(request.request_id)
            raise
        finally:
            writer.close()
            with contextlib.suppress(OSError, asyncio.CancelledError):
                await writer.wait_closed()
