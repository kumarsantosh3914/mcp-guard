import json

import pytest

from mcp_guard import jsonrpc as j
from mcp_guard.jsonrpc import Kind


def parse(obj):
    return j.parse_line(json.dumps(obj))


REQ = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "echo"}}
NOTE = {"jsonrpc": "2.0", "method": "notifications/initialized"}


# --- classification ---

def test_request():
    m = parse(REQ)
    assert m.kind is Kind.REQUEST
    assert m.id == 1
    assert m.method == "tools/call"
    assert m.params == {"name": "echo"}


def test_request_with_string_id():
    m = parse({"jsonrpc": "2.0", "id": "abc", "method": "ping"})
    assert m.kind is Kind.REQUEST
    assert m.id == "abc"


def test_notification():
    m = parse(NOTE)
    assert m.kind is Kind.NOTIFICATION
    assert m.id is None


def test_response_with_result():
    assert parse({"jsonrpc": "2.0", "id": 1, "result": {}}).kind is Kind.RESPONSE


def test_response_with_error():
    obj = {"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "x"}}
    assert parse(obj).kind is Kind.RESPONSE


def test_error_response_with_null_id():
    obj = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "x"}}
    assert parse(obj).kind is Kind.RESPONSE


@pytest.mark.parametrize("obj", [
    {"id": 1, "method": "x"},                                   # no jsonrpc field
    {"jsonrpc": "1.0", "id": 1, "method": "x"},                 # wrong version
    {"jsonrpc": "2.0", "id": None, "method": "x"},              # null id on a request
    {"jsonrpc": "2.0", "id": True, "method": "x"},              # boolean id
    {"jsonrpc": "2.0", "id": 1.5, "method": "x"},               # float id
    {"jsonrpc": "2.0", "id": 1, "method": 5},                   # method not a string
    {"jsonrpc": "2.0", "id": 1, "method": "x", "params": "p"},  # params not object or array
    {"jsonrpc": "2.0", "id": 1, "result": {}, "error": {}},     # both result and error
    {"jsonrpc": "2.0", "id": 1},                                # neither
    "text",
    5,
    [],
])
def test_invalid(obj):
    assert parse(obj).kind is Kind.INVALID


def test_batch():
    m = parse([REQ, NOTE, "junk"])
    assert m.kind is Kind.BATCH
    assert [i.kind for i in m.items] == [Kind.REQUEST, Kind.NOTIFICATION, Kind.INVALID]


def test_nested_batch_is_invalid():
    assert parse([[REQ]]).items[0].kind is Kind.INVALID


# --- parsing ---

@pytest.mark.parametrize("data", ["", "   ", "{not json", b"\xff\xfe"])
def test_parse_error(data):
    with pytest.raises(j.ParseError):
        j.parse_line(data)


def test_bytes_input_with_newline():
    assert j.parse_line(b'{"jsonrpc":"2.0","method":"x"}\n').kind is Kind.NOTIFICATION


# --- building ---

def test_encode_is_one_line():
    out = j.encode({"text": "a\nb"})
    assert out.endswith(b"\n")
    assert out.count(b"\n") == 1
    assert json.loads(out) == {"text": "a\nb"}


def test_encode_keeps_unicode():
    assert json.loads(j.encode({"t": "café"}).decode("utf-8")) == {"t": "café"}


def test_blocked_tool_call_is_error_result():
    r = j.blocked_response(parse({**REQ, "id": 7}))
    assert r["id"] == 7
    assert "error" not in r
    assert r["result"]["isError"] is True
    assert r["result"]["content"][0]["text"] == j.BLOCKED_TEXT


def test_blocked_other_method_is_jsonrpc_error():
    r = j.blocked_response(parse({"jsonrpc": "2.0", "id": "r1", "method": "resources/read"}))
    assert r["id"] == "r1"
    assert r["error"]["code"] == j.REQUEST_BLOCKED
    assert "result" not in r


def test_blocked_custom_reason():
    r = j.blocked_response(parse(REQ), "Timed out")
    assert r["result"]["content"][0]["text"] == "Timed out"


def test_blocked_needs_a_request():
    with pytest.raises(ValueError):
        j.blocked_response(parse(NOTE))


def test_make_error_with_data():
    r = j.make_error(3, -1, "bad", {"why": "x"})
    assert r == {"jsonrpc": "2.0", "id": 3, "error": {"code": -1, "message": "bad", "data": {"why": "x"}}}


# --- cancellation ---

@pytest.mark.parametrize("params, expected", [
    ({"requestId": 3}, 3),
    ({"requestId": "a"}, "a"),
    ({}, None),
    ({"requestId": None}, None),
])
def test_cancelled_request_id(params, expected):
    m = parse({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": params})
    assert j.cancelled_request_id(m) == expected


def test_cancelled_id_ignores_requests():
    m = parse({"jsonrpc": "2.0", "id": 1, "method": "notifications/cancelled", "params": {"requestId": 3}})
    assert j.cancelled_request_id(m) is None
