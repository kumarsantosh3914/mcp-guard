"""Gateway sessions with the fake MCP server. No network needed."""
import asyncio
import contextlib
import json
import sys
from pathlib import Path

import pytest

from mcp_guard.approval.decider import Decision, StaticDecider, WaitDecider
from mcp_guard.config import ServerSpec
from mcp_guard.gateway.session import CANCELLED_TEXT, SessionManager, TooManySessions
from mcp_guard.jsonrpc import INTERNAL_ERROR, INVALID_REQUEST, classify
from mcp_guard.policy import Policy

FAKE = Path(__file__).resolve().parents[1] / "fakes" / "fake_mcp_server.py"


def spec(record):
    return ServerSpec(sys.executable, (str(FAKE), "--record", str(record)))


def msg(obj):
    return classify(obj), json.dumps(obj).encode()


INIT = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}


def call(req_id, name="echo", arguments=None):
    return {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {"text": "hi"}}}


def calls(record):
    return [json.loads(x) for x in record.read_text().splitlines()] if record.exists() else []


@pytest.fixture
async def manager_for(tmp_path):
    managers = []

    def _make(decider, **kwargs):
        m = SessionManager({"fake": spec(tmp_path / "calls.jsonl")}, decider, Policy(), 5.0, **kwargs)
        managers.append(m)
        return m

    yield _make
    for m in managers:
        await m.close_all()


async def test_allow_reaches_server(manager_for, tmp_path):
    m = manager_for(StaticDecider(Decision.ALLOW))
    s = await m.create("fake")
    r = await s.handle(*msg(INIT))
    assert r["result"]["serverInfo"]["name"] == "fake-mcp-server"
    r = await s.handle(*msg(call(1)))
    assert r["result"]["content"][0]["text"] == "hi"
    assert calls(tmp_path / "calls.jsonl") == [{"tool": "echo", "arguments": {"text": "hi"}}]


async def test_block_never_reaches_server(manager_for, tmp_path):
    m = manager_for(StaticDecider(Decision.BLOCK))
    s = await m.create("fake")
    r = await s.handle(*msg(call(1)))
    assert r["result"]["isError"] is True
    assert calls(tmp_path / "calls.jsonl") == []


async def test_notification_returns_none(manager_for):
    m = manager_for(StaticDecider(Decision.ALLOW))
    s = await m.create("fake")
    assert await s.handle(*msg({"jsonrpc": "2.0", "method": "notifications/initialized"})) is None


async def test_cancel_while_waiting_for_approval(manager_for, tmp_path):
    m = manager_for(WaitDecider())
    s = await m.create("fake")
    task = asyncio.create_task(s.handle(*msg(call(7))))
    await asyncio.sleep(0.1)
    note = {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 7}}
    assert await s.handle(*msg(note)) is None
    r = await asyncio.wait_for(task, 2)
    assert r["error"]["message"] == CANCELLED_TEXT
    assert calls(tmp_path / "calls.jsonl") == []


async def test_http_client_going_away_cancels_cleanly(manager_for, tmp_path):
    m = manager_for(WaitDecider())
    s = await m.create("fake")
    task = asyncio.create_task(s.handle(*msg(call(8))))
    await asyncio.sleep(0.1)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert s.active == 0
    assert calls(tmp_path / "calls.jsonl") == []


async def test_duplicate_id(manager_for):
    m = manager_for(WaitDecider())
    s = await m.create("fake")
    first = asyncio.create_task(s.handle(*msg(call(1))))
    await asyncio.sleep(0.1)
    r = await s.handle(*msg(call(1)))
    assert r["error"]["code"] == INVALID_REQUEST
    first.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await first


async def test_server_crash_answers_with_error(manager_for):
    m = manager_for(StaticDecider(Decision.ALLOW))
    s = await m.create("fake")
    r = await s.handle(*msg(call(1, "crash")))
    assert r["error"]["code"] == INTERNAL_ERROR
    await asyncio.sleep(0.1)
    assert m.get("fake", s.id) is None  # a dead session is not handed out again


async def test_each_session_has_its_own_process(manager_for):
    m = manager_for(StaticDecider(Decision.ALLOW))
    a = await m.create("fake")
    b = await m.create("fake")
    assert a.id != b.id
    assert a._proc.pid != b._proc.pid
    assert m.get("fake", a.id) is a
    assert m.get("fake", "nope") is None
    assert m.get("other", a.id) is None


async def test_max_sessions(manager_for):
    m = manager_for(StaticDecider(Decision.ALLOW), max_sessions=2)
    await m.create("fake")
    await m.create("fake")
    with pytest.raises(TooManySessions):
        await m.create("fake")


async def test_unknown_server(manager_for):
    m = manager_for(StaticDecider(Decision.ALLOW))
    with pytest.raises(KeyError):
        await m.create("nope")


async def test_close_and_idle_close(manager_for):
    m = manager_for(StaticDecider(Decision.ALLOW), idle_seconds=0.05)
    s = await m.create("fake")
    assert await m.close("fake", s.id) is True
    assert await m.close("fake", s.id) is False
    t = await m.create("fake")
    await asyncio.sleep(0.1)
    await m.close_idle()
    assert m.get("fake", t.id) is None
    assert len(m) == 0


async def test_concurrent_calls_in_one_session(manager_for):
    m = manager_for(StaticDecider(Decision.ALLOW))
    s = await m.create("fake")
    results = await asyncio.gather(*(s.handle(*msg(call(i, "slow", {"seconds": 0.3}))) for i in range(5)))
    assert sorted(r["id"] for r in results) == list(range(5))
