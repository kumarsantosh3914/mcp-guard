"""The gateway's HTTP layer on a real local port.

The agent sandbox does not allow opening ports, so the owner runs these:
    .venv/bin/python -m pytest tests/network -q
"""
import json
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient as HttpClient  # renamed so pytest does not collect them
from aiohttp.test_utils import TestServer as HttpServer

from mcp_guard.approval.decider import Decision, StaticDecider
from mcp_guard.config import GatewayConfig, ServerSpec
from mcp_guard.gateway.http import MAX_BODY_BYTES, SESSION_HEADER, create_app
from mcp_guard.gateway.session import SessionManager
from mcp_guard.policy import Policy

FAKE = Path(__file__).resolve().parents[1] / "fakes" / "fake_mcp_server.py"
TOKEN = "T" * 43
ANTHROPIC_IP = {"CF-Connecting-IP": "160.79.104.10"}
PATH = "/%s/fake/mcp" % TOKEN

INIT = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}


def call(req_id, name="echo"):
    return {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
            "params": {"name": name, "arguments": {"text": "hi"}}}


@pytest.fixture
async def client_for(tmp_path):
    made = []

    async def _make(decision=Decision.ALLOW):
        servers = {"fake": ServerSpec(sys.executable, (str(FAKE), "--record", str(tmp_path / "calls.jsonl")))}
        gw = GatewayConfig(token=TOKEN, servers=servers)
        manager = SessionManager(servers, StaticDecider(decision), Policy(), 5.0)
        client = HttpClient(HttpServer(create_app(gw, manager)))
        await client.start_server()
        made.append((client, manager))
        return client

    yield _make
    for client, manager in made:
        await manager.close_all()
        await client.close()


async def start_session(client):
    r = await client.post(PATH, json=INIT, headers=ANTHROPIC_IP)
    assert r.status == 200
    return r.headers[SESSION_HEADER]


async def test_full_flow_allow(client_for, tmp_path):
    client = await client_for(Decision.ALLOW)
    sid = await start_session(client)
    headers = {**ANTHROPIC_IP, SESSION_HEADER: sid}
    r = await client.post(PATH, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
    assert r.status == 202
    r = await client.post(PATH, json=call(1), headers=headers)
    assert r.status == 200
    assert (await r.json())["result"]["content"][0]["text"] == "hi"
    assert (tmp_path / "calls.jsonl").exists()
    r = await client.delete(PATH, headers=headers)
    assert r.status == 200
    r = await client.post(PATH, json=call(2), headers=headers)
    assert r.status == 404


async def test_block(client_for, tmp_path):
    client = await client_for(Decision.BLOCK)
    sid = await start_session(client)
    r = await client.post(PATH, json=call(1), headers={**ANTHROPIC_IP, SESSION_HEADER: sid})
    body = await r.json()
    assert body["result"]["isError"] is True
    assert not (tmp_path / "calls.jsonl").exists()


async def test_wrong_token_is_404(client_for):
    client = await client_for()
    r = await client.post("/%s/fake/mcp" % ("x" * 43), json=INIT, headers=ANTHROPIC_IP)
    assert r.status == 404


async def test_ip_outside_anthropic_is_403(client_for):
    client = await client_for()
    r = await client.post(PATH, json=INIT, headers={"CF-Connecting-IP": "8.8.8.8"})
    assert r.status == 403
    r = await client.post(PATH, json=INIT)  # local peer, no tunnel header
    assert r.status == 403


async def test_browser_origin_is_403(client_for):
    client = await client_for()
    r = await client.post(PATH, json=INIT, headers={**ANTHROPIC_IP, "Origin": "https://evil.example"})
    assert r.status == 403


async def test_unknown_server_is_404(client_for):
    client = await client_for()
    r = await client.post("/%s/nope/mcp" % TOKEN, json=INIT, headers=ANTHROPIC_IP)
    assert r.status == 404


async def test_get_is_405(client_for):
    client = await client_for()
    r = await client.get(PATH, headers=ANTHROPIC_IP)
    assert r.status == 405


async def test_missing_and_unknown_session(client_for):
    client = await client_for()
    r = await client.post(PATH, json=call(1), headers=ANTHROPIC_IP)
    assert r.status == 400
    r = await client.post(PATH, json=call(1), headers={**ANTHROPIC_IP, SESSION_HEADER: "nope"})
    assert r.status == 404


async def test_bad_bodies(client_for):
    client = await client_for()
    r = await client.post(PATH, data=b"{bad", headers=ANTHROPIC_IP)
    assert r.status == 400 and (await r.json())["error"]["code"] == -32700
    r = await client.post(PATH, json=[INIT], headers=ANTHROPIC_IP)
    assert r.status == 400
    r = await client.post(PATH, json={"jsonrpc": "1.0", "id": 1, "method": "x"}, headers=ANTHROPIC_IP)
    assert r.status == 400


async def test_body_too_large_is_413(client_for):
    client = await client_for()
    r = await client.post(PATH, data=b"x" * (MAX_BODY_BYTES + 1), headers=ANTHROPIC_IP)
    assert r.status == 413
