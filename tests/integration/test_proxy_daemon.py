"""Full path: client -> mcp-guard proxy (default daemon decider) -> daemon socket -> fake MCP server."""
import pytest

from mcp_guard.approval.decider import Decision
from mcp_guard.approval.queue import ApprovalQueue
from mcp_guard.daemon.server import DaemonServer

from test_daemon_socket import AutoAnswer
from test_stdio_proxy import ENV, ProxyUnderTest, call, text_of


@pytest.fixture
async def daemon(socket_path):
    servers = []

    async def _start(rule):
        auto = AutoAnswer(rule)
        queue = ApprovalQueue(on_change=auto)
        auto.queue = queue
        server = DaemonServer(queue, socket_path)
        await server.start()
        servers.append(server)
        return auto

    yield _start
    for server in servers:
        await server.close()


@pytest.fixture
async def proxy(tmp_path, socket_path):
    started = []

    async def _start():
        env = dict(ENV, MCP_GUARD_SOCKET=str(socket_path))
        p = await ProxyUnderTest.start(tmp_path, decider=None, env=env)
        started.append(p)
        return p

    yield _start
    for p in started:
        if p.proc.returncode is None:
            p.proc.kill()
            await p.proc.wait()


async def test_user_allows(daemon, proxy):
    auto = await daemon(lambda r: Decision.ALLOW)
    p = await proxy()
    await p.send(call(1, "echo", {"text": "hi"}))
    r = await p.recv()
    assert text_of(r) == "hi"
    assert await p.close() == 0
    assert p.calls() == [{"tool": "echo", "arguments": {"text": "hi"}}]
    (shown,) = auto.seen.values()
    assert (shown.server, shown.tool, shown.source) == ("fake", "echo", "desktop")


async def test_user_blocks(daemon, proxy):
    await daemon(lambda r: Decision.BLOCK)
    p = await proxy()
    await p.send(call(1, "echo", {"text": "hi"}))
    r = await p.recv()
    assert r["result"]["isError"] is True
    assert text_of(r) == "Blocked by the user via mcp-guard."
    assert await p.close() == 0
    assert p.calls() == []
