"""The daemon's socket server and the proxy-side DaemonDecider, in one event loop."""
import asyncio
import contextlib
import os
import socket
import stat

import pytest

from mcp_guard.approval.client import DaemonDecider, NOT_RUNNING_TEXT
from mcp_guard.approval.decider import Decision
from mcp_guard.approval.queue import ALLOWED_TEXT, STOPPING_TEXT, ApprovalQueue
from mcp_guard.daemon.server import AlreadyRunning, DaemonError, DaemonServer
from mcp_guard.jsonrpc import BLOCKED_TEXT
from mcp_guard.policy import RequestSummary


def summary(tool="echo", arguments=None):
    return RequestSummary("files", "tools/call", tool, arguments if arguments is not None else {"text": "hi"})


class AutoAnswer:
    """Stands in for the user: answers each new request by a rule, on the next loop turn."""

    def __init__(self, rule):
        self.rule = rule
        self.queue = None
        self.seen = {}

    def __call__(self, views):
        for view in views:
            rid = view.request.request_id
            if rid in self.seen:
                continue
            self.seen[rid] = view.request
            decision = self.rule(view.request)
            if decision is not None:
                asyncio.get_running_loop().call_soon(self.queue.decide, rid, decision)


@pytest.fixture
async def daemon(socket_path):
    servers = []

    async def _start(rule=lambda r: None):
        auto = AutoAnswer(rule)
        queue = ApprovalQueue(on_change=auto)
        auto.queue = queue
        server = DaemonServer(queue, socket_path)
        await server.start()
        servers.append(server)
        return server, queue, auto

    yield _start
    for server in servers:
        await server.close()


async def wait_until(check, timeout=3.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not check():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


async def test_allow(daemon, socket_path):
    await daemon(lambda r: Decision.ALLOW)
    v = await DaemonDecider(socket_path, timeout=5).decide(summary())
    assert v.allowed and v.reason == ALLOWED_TEXT


async def test_block(daemon, socket_path):
    await daemon(lambda r: Decision.BLOCK)
    v = await DaemonDecider(socket_path, timeout=5).decide(summary())
    assert not v.allowed and v.reason == BLOCKED_TEXT


async def test_daemon_not_running_blocks_fast(socket_path):
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    v = await DaemonDecider(socket_path, timeout=5).decide(summary())
    assert not v.allowed and v.reason == NOT_RUNNING_TEXT
    assert loop.time() - t0 < 1


async def test_no_answer_blocks_after_timeout(daemon, socket_path):
    await daemon()
    v = await DaemonDecider(socket_path, timeout=0.3).decide(summary())
    assert not v.allowed
    assert "no answer within 0.3 seconds" in v.reason


async def test_proxy_hang_up_cancels_request(daemon, socket_path):
    _, queue, _ = await daemon()
    task = asyncio.create_task(DaemonDecider(socket_path, timeout=10).decide(summary()))
    await wait_until(lambda: len(queue) == 1)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await wait_until(lambda: len(queue) == 0)


async def test_bad_request_is_dropped(daemon, socket_path):
    _, queue, _ = await daemon(lambda r: Decision.ALLOW)
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    writer.write(b'{"v":1,"type":"request"}\n')
    await writer.drain()
    assert await asyncio.wait_for(reader.read(), 3) == b""  # closed, no verdict
    writer.close()
    assert len(queue) == 0


async def test_wrong_protocol_version_is_dropped(daemon, socket_path):
    await daemon(lambda r: Decision.ALLOW)
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    writer.write(b'{"v":99,"type":"request"}\n')
    await writer.drain()
    assert await asyncio.wait_for(reader.read(), 3) == b""
    writer.close()


async def test_twenty_requests_at_once(daemon, socket_path):
    await daemon(lambda r: Decision.ALLOW if int(r.tool[1:]) % 2 == 0 else Decision.BLOCK)
    decider = DaemonDecider(socket_path, timeout=5)
    results = await asyncio.gather(*(decider.decide(summary(tool="t%d" % i)) for i in range(20)))
    assert [v.allowed for v in results] == [i % 2 == 0 for i in range(20)]


async def test_large_arguments_reach_the_popup_as_preview(daemon, socket_path):
    _, _, auto = await daemon(lambda r: Decision.ALLOW)
    v = await DaemonDecider(socket_path, timeout=5).decide(summary(arguments={"text": "x" * 200_000}))
    assert v.allowed
    (shown,) = auto.seen.values()
    assert shown.arguments["_truncated"] is True


async def test_second_daemon_is_refused(daemon, socket_path):
    await daemon()
    with pytest.raises(AlreadyRunning):
        await DaemonServer(ApprovalQueue(), socket_path).start()


async def test_leftover_socket_is_replaced(daemon, socket_path):
    stale = socket.socket(socket.AF_UNIX)
    stale.bind(str(socket_path))
    stale.close()  # the file stays, but nobody listens: like a crashed daemon
    assert socket_path.is_socket()
    await daemon(lambda r: Decision.ALLOW)
    assert (await DaemonDecider(socket_path, timeout=5).decide(summary())).allowed


async def test_regular_file_is_never_replaced(socket_path):
    socket_path.write_text("not a socket")
    with pytest.raises(DaemonError):
        await DaemonServer(ApprovalQueue(), socket_path).start()
    assert socket_path.read_text() == "not a socket"


async def test_socket_and_folder_are_private(daemon, socket_path):
    await daemon()
    assert stat.S_IMODE(os.stat(socket_path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(socket_path.parent).st_mode) == 0o700


async def test_open_folder_is_refused(socket_path):
    os.chmod(socket_path.parent, 0o755)
    with pytest.raises(DaemonError):
        await DaemonServer(ApprovalQueue(), socket_path).start()


async def test_decisions_and_cancels_are_audited(socket_path, tmp_path):
    import json

    from mcp_guard.audit import AuditLog

    audit = AuditLog(tmp_path / "audit.jsonl")
    audit.open()
    auto = AutoAnswer(lambda r: Decision.ALLOW if r.tool == "yes" else None)
    queue = ApprovalQueue(on_change=auto)
    auto.queue = queue
    server = DaemonServer(queue, socket_path, audit)
    await server.start()
    try:
        assert (await DaemonDecider(socket_path, timeout=5).decide(summary(tool="yes"))).allowed
        task = asyncio.create_task(DaemonDecider(socket_path, timeout=10).decide(summary(tool="wait")))
        await wait_until(lambda: len(queue) == 1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await wait_until(lambda: len(queue) == 0)
        await asyncio.sleep(0.05)
    finally:
        await server.close()
        audit.close()
    lines = [json.loads(x) for x in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [(x["tool"], x["decision"]) for x in lines] == [("yes", "allow"), ("wait", "cancelled")]


async def test_stopping_daemon_blocks_waiting_requests(daemon, socket_path):
    server, queue, _ = await daemon()
    task = asyncio.create_task(DaemonDecider(socket_path, timeout=10).decide(summary()))
    await wait_until(lambda: len(queue) == 1)
    await server.close()
    v = await asyncio.wait_for(task, 3)
    assert not v.allowed and v.reason == STOPPING_TEXT
    assert not socket_path.exists()
