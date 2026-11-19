"""End to end tests: a test client talks to `mcp-guard proxy`, which wraps the fake MCP server.

The fake server records every tools/call it receives, so the tests can prove that
blocked calls never reach it.
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mcp_guard import jsonrpc
from mcp_guard.proxy.stdio_proxy import BATCH_REJECTED, DUPLICATE_ID, SERVER_STOPPED

ROOT = Path(__file__).resolve().parents[2]
FAKE = ROOT / "tests" / "fakes" / "fake_mcp_server.py"
ENV = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
CRASH_EXIT_CODE = 3


class ProxyUnderTest:
    def __init__(self, proc, record):
        self.proc = proc
        self.record = record

    @classmethod
    async def start(cls, tmp_path, decider="allow", timeout=5.0, gate=(), server_cmd=None):
        record = tmp_path / "calls.jsonl"
        server = server_cmd or [sys.executable, str(FAKE), "--record", str(record)]
        args = [sys.executable, "-m", "mcp_guard", "proxy", "--name", "fake", "--timeout", str(timeout)]
        if decider is not None:
            args += ["--decider", decider]
        for method in gate:
            args += ["--gate", method]
        proc = await asyncio.create_subprocess_exec(
            *args, "--", *server,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=ENV,
            limit=32 * 1024 * 1024,
        )
        return cls(proc, record)

    async def send(self, obj):
        data = obj if isinstance(obj, str) else json.dumps(obj)
        self.proc.stdin.write(data.encode("utf-8") + b"\n")
        await self.proc.stdin.drain()

    async def recv(self, timeout=5.0):
        line = await asyncio.wait_for(self.proc.stdout.readline(), timeout)
        assert line, "proxy closed stdout"
        return json.loads(line)

    async def recv_many(self, n, timeout=5.0):
        return [await self.recv(timeout) for _ in range(n)]

    async def wait_exit(self, timeout=10.0):
        return await asyncio.wait_for(self.proc.wait(), timeout)

    async def close(self, timeout=10.0):
        if not self.proc.stdin.is_closing():
            self.proc.stdin.close()
        return await self.wait_exit(timeout)

    def calls(self):
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text().splitlines()]


@pytest.fixture
async def start(tmp_path):
    started = []

    async def _start(**kwargs):
        p = await ProxyUnderTest.start(tmp_path, **kwargs)
        started.append(p)
        return p

    yield _start
    for p in started:
        if p.proc.returncode is None:
            p.proc.kill()
            await p.proc.wait()


def init(req_id=0):
    return {"jsonrpc": "2.0", "id": req_id, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "0"}}}


INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def call(req_id, name, arguments=None):
    return {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}}}


def ping(req_id):
    return {"jsonrpc": "2.0", "id": req_id, "method": "ping"}


def cancel(req_id):
    return {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": req_id}}


def text_of(response):
    return response["result"]["content"][0]["text"]


# ---- pass through ----

async def test_requests_without_approval_pass_through(start):
    p = await start(decider="block")
    await p.send(init(0))
    await p.send(INITIALIZED)
    await p.send({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    r0, r1 = await p.recv_many(2)
    assert r0["id"] == 0 and r0["result"]["serverInfo"]["name"] == "fake-mcp-server"
    assert r1["id"] == 1 and len(r1["result"]["tools"]) == 4
    assert await p.close() == 0
    assert p.calls() == []


# ---- allow and block ----

async def test_allowed_call_reaches_server(start):
    p = await start(decider="allow")
    await p.send(call(1, "echo", {"text": "hi"}))
    r = await p.recv()
    assert r["id"] == 1 and text_of(r) == "hi"
    assert await p.close() == 0
    assert p.calls() == [{"tool": "echo", "arguments": {"text": "hi"}}]


async def test_default_decider_blocks_and_call_never_reaches_server(start):
    p = await start(decider=None)
    await p.send(call(1, "echo", {"text": "hi"}))
    r = await p.recv()
    assert r["id"] == 1
    assert r["result"]["isError"] is True
    assert "Blocked" in text_of(r)
    await p.send(ping(2))  # the proxy keeps working after a block
    assert (await p.recv())["id"] == 2
    assert await p.close() == 0
    assert p.calls() == []


async def test_other_gated_method_gets_jsonrpc_error(start):
    p = await start(decider="block", gate=["tools/call", "resources/read"])
    await p.send({"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": "file:///x"}})
    r = await p.recv()
    assert r["id"] == 1 and r["error"]["code"] == jsonrpc.REQUEST_BLOCKED


# ---- timeout and cancel ----

async def test_no_answer_blocks_after_timeout(start):
    p = await start(decider="wait", timeout=0.5)
    t0 = time.monotonic()
    await p.send(call(1, "echo", {"text": "hi"}))
    r = await p.recv()
    assert time.monotonic() - t0 < 3
    assert r["result"]["isError"] is True
    assert "no answer within 0.5 seconds" in text_of(r)
    assert await p.close() == 0
    assert p.calls() == []


async def test_cancel_while_waiting_drops_the_request(start):
    p = await start(decider="wait", timeout=1.5)
    await p.send(call(5, "echo", {"text": "hi"}))
    await p.send(cancel(5))
    await p.send(ping(6))
    assert (await p.recv())["id"] == 6
    await asyncio.sleep(2)  # past the timeout: a late answer for 5 would show up first
    await p.send(ping(7))
    assert (await p.recv())["id"] == 7
    assert await p.close() == 0
    assert p.calls() == []


async def test_cancel_after_forward_goes_to_server(start):
    p = await start(decider="allow")
    await p.send(call(3, "slow", {"seconds": 0.5}))
    await asyncio.sleep(0.2)
    await p.send(cancel(3))  # already forwarded, so the server gets the cancel
    r = await p.recv()
    assert r["id"] == 3  # the fake server ignores cancels and still answers
    assert await p.close() == 0


async def test_duplicate_pending_id_is_rejected(start):
    p = await start(decider="wait", timeout=5)
    await p.send(call(1, "echo", {"text": "a"}))
    await p.send(call(1, "echo", {"text": "b"}))
    r = await p.recv()
    assert r["id"] == 1 and r["error"]["code"] == jsonrpc.INVALID_REQUEST
    assert r["error"]["message"] == DUPLICATE_ID
    assert await p.close() == 0
    assert p.calls() == []


# ---- bad input ----

async def test_bad_json_is_answered_and_not_forwarded(start):
    p = await start(decider="allow")
    await p.send("{bad json")
    r = await p.recv()
    assert r["id"] is None and r["error"]["code"] == jsonrpc.PARSE_ERROR
    await p.send(ping(1))
    assert (await p.recv())["id"] == 1  # the server did not send a second parse error
    assert await p.close() == 0


async def test_invalid_request_is_answered_and_not_forwarded(start):
    p = await start(decider="allow")
    await p.send({"jsonrpc": "1.0", "id": 3, "method": "ping"})
    r = await p.recv()
    assert r["id"] == 3 and r["error"]["code"] == jsonrpc.INVALID_REQUEST
    await p.send(ping(4))
    assert (await p.recv())["id"] == 4  # the fake server would have answered id 3 if it got it
    assert await p.close() == 0


async def test_junk_from_server_is_dropped(start):
    p = await start(decider="allow")
    await p.send({"jsonrpc": "2.0", "id": 1, "method": "debug/junk"})
    r = await p.recv()  # would fail to parse if the junk line came through
    assert r["id"] == 1
    assert await p.close() == 0


async def test_batch_with_gated_request_is_rejected(start):
    p = await start(decider="allow")
    await p.send([call(1, "echo", {"text": "a"}), ping(2), {"jsonrpc": "2.0", "method": "notifications/x"}])
    r = await p.recv()
    assert isinstance(r, list)
    assert [e["id"] for e in r] == [1, 2]
    assert all(e["error"]["code"] == jsonrpc.REQUEST_BLOCKED for e in r)
    assert r[0]["error"]["message"] == BATCH_REJECTED
    assert await p.close() == 0
    assert p.calls() == []


# ---- crashes and shutdown ----

async def test_server_crash_answers_forwarded_request(start):
    p = await start(decider="allow")
    await p.send(call(1, "crash"))
    r = await p.recv()
    assert r["id"] == 1 and r["error"]["code"] == jsonrpc.INTERNAL_ERROR
    assert r["error"]["message"] == SERVER_STOPPED
    assert await p.wait_exit() == CRASH_EXIT_CODE


async def test_server_crash_answers_pending_approvals(start):
    p = await start(decider="wait", timeout=10)
    await p.send(call(1, "echo", {"text": "a"}))  # waits for approval
    await asyncio.sleep(0.2)
    await p.send({"jsonrpc": "2.0", "id": 2, "method": "debug/crash"})  # not gated, crashes the server
    r = await p.recv_many(2)
    assert {x["id"] for x in r} == {1, 2}
    assert all(x["error"]["code"] == jsonrpc.INTERNAL_ERROR for x in r)
    assert await p.wait_exit() == CRASH_EXIT_CODE
    assert p.calls() == []


async def test_client_close_stops_server(start):
    p = await start(decider="allow")
    await p.send(init(0))
    await p.recv()
    assert await p.close() == 0


async def test_missing_server_command_exits_127(start):
    p = await start(server_cmd=["/nonexistent/mcp-server"])
    assert await p.wait_exit() == 127


def test_proxy_without_server_command_is_usage_error():
    proc = subprocess.run(
        [sys.executable, "-m", "mcp_guard", "proxy", "--name", "x"],
        capture_output=True, text=True, env=ENV, timeout=10,
    )
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "needs the MCP server command" in proc.stderr


# ---- size and concurrency ----

async def test_one_megabyte_round_trip(start):
    p = await start(decider="allow")
    big = "x" * 1_000_000
    await p.send(call(1, "echo", {"text": big}))
    r = await p.recv(timeout=10)
    assert text_of(r) == big
    assert await p.close() == 0


async def test_ten_calls_at_once(start):
    p = await start(decider="allow")
    t0 = time.monotonic()
    for i in range(10):
        await p.send(call(i, "slow", {"seconds": 0.5}))
    r = await p.recv_many(10)
    assert {x["id"] for x in r} == set(range(10))
    assert time.monotonic() - t0 < 3  # in parallel, not one after another
    assert await p.close() == 0
    assert len(p.calls()) == 10


# ---- logging ----

async def test_logs_go_to_stderr_not_stdout(start):
    p = await start(decider="block")
    await p.send(call(1, "echo", {"text": "hi"}))
    await p.recv()  # recv() parses JSON, so any log line on stdout would fail here
    assert await p.close() == 0
    err = (await p.proc.stderr.read()).decode()
    assert "blocked tools/call echo" in err
