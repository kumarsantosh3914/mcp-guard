"""Checks that the fake MCP server behaves, so later proxy tests can trust it."""
import json
import subprocess
import sys
from pathlib import Path

from mcp_guard.jsonrpc import Kind, parse_line

FAKE = Path(__file__).resolve().parents[1] / "fakes" / "fake_mcp_server.py"


def run_server(messages, tmp_path, timeout=15):
    record = tmp_path / "calls.jsonl"
    data = "".join((m if isinstance(m, str) else json.dumps(m)) + "\n" for m in messages)
    proc = subprocess.run(
        [sys.executable, str(FAKE), "--record", str(record)],
        input=data, capture_output=True, text=True, timeout=timeout,
    )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    for line in lines:
        assert parse_line(line).kind is Kind.RESPONSE
    responses = {r["id"]: r for r in map(json.loads, lines)}
    calls = [json.loads(x) for x in record.read_text().splitlines()] if record.exists() else []
    return proc.returncode, responses, calls


def call(req_id, name, arguments=None):
    params = {"name": name, "arguments": arguments or {}}
    return {"jsonrpc": "2.0", "id": req_id, "method": "tools/call", "params": params}


INIT = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "0"}}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def test_handshake_and_list(tmp_path):
    code, res, calls = run_server(
        [INIT, INITIALIZED, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}], tmp_path)
    assert code == 0
    assert res[0]["result"]["serverInfo"]["name"] == "fake-mcp-server"
    assert [t["name"] for t in res[1]["result"]["tools"]] == ["echo", "slow", "fail"]
    assert calls == []


def test_tools(tmp_path):
    code, res, calls = run_server([
        INIT, INITIALIZED,
        call(1, "echo", {"text": "hi"}),
        call(2, "fail"),
        call(3, "slow", {"seconds": 0.1}),
        call(4, "nope"),
    ], tmp_path)
    assert code == 0
    assert res[1]["result"]["content"][0]["text"] == "hi"
    assert res[2]["result"]["isError"] is True
    assert res[3]["result"]["isError"] is False
    assert res[4]["error"]["code"] == -32602
    assert sorted(c["tool"] for c in calls) == ["echo", "fail", "nope", "slow"]


def test_slow_call_does_not_block_others(tmp_path):
    code, res, _ = run_server([
        INIT, call(1, "slow", {"seconds": 1}), call(2, "echo", {"text": "fast"}),
    ], tmp_path)
    assert code == 0
    assert set(res) == {0, 1, 2}


def test_parse_error_and_unknown_method(tmp_path):
    code, res, _ = run_server(["{bad json", {"jsonrpc": "2.0", "id": 9, "method": "nope"}], tmp_path)
    assert code == 0
    assert res[None]["error"]["code"] == -32700
    assert res[9]["error"]["code"] == -32601
