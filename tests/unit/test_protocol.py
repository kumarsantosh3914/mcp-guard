import json

import pytest

from mcp_guard.approval.protocol import (
    MAX_DISPLAY_ARGUMENT_BYTES,
    ApprovalRequest,
    ProtocolError,
    VerdictMessage,
    display_arguments,
)

REQ = ApprovalRequest("abc123", "desktop", "files", "tools/call", "echo", {"text": "hi"}, 45.0)


def line(**fields):
    obj = {"v": 1, "type": "request", "id": "abc", "source": "desktop", "server": "s",
           "method": "tools/call", "tool": "t", "arguments": {}, "timeout": 45}
    obj.update(fields)
    obj = {k: v for k, v in obj.items() if v is not ...}
    return (json.dumps(obj) + "\n").encode()


def test_request_round_trip():
    assert ApprovalRequest.parse(REQ.to_line()) == REQ


def test_request_without_tool():
    r = ApprovalRequest.parse(line(tool=None))
    assert r.tool is None


def test_verdict_round_trip():
    v = VerdictMessage("abc", "allow", "Allowed by the user.")
    assert VerdictMessage.parse(v.to_line()) == v


@pytest.mark.parametrize("data", [
    b"not json\n",
    b"[1,2]\n",
    line(v=2),
    line(type="verdict"),
    line(source="phone"),
    line(timeout=0),
    line(timeout=-1),
    line(timeout=301),
    line(timeout=True),
    line(timeout="45"),
    line(id=""),
    line(id="x" * 65),
    line(server=5),
    line(server="s" * 257),
    line(method=...),
    line(arguments=...),
    b"\xff\xfe\n",
])
def test_bad_requests(data):
    with pytest.raises(ProtocolError):
        ApprovalRequest.parse(data)


@pytest.mark.parametrize("obj", [
    {"v": 1, "type": "verdict", "id": "a", "decision": "maybe", "reason": "r"},
    {"v": 1, "type": "verdict", "id": "a", "decision": "allow", "reason": ""},
    {"v": 1, "type": "verdict", "id": "a", "decision": "allow"},
    {"v": 1, "type": "request", "id": "a", "decision": "allow", "reason": "r"},
])
def test_bad_verdicts(obj):
    with pytest.raises(ProtocolError):
        VerdictMessage.parse((json.dumps(obj) + "\n").encode())


def test_small_arguments_are_unchanged():
    args = {"text": "hi", "n": [1, 2]}
    assert display_arguments(args) is args


def test_large_arguments_become_a_preview():
    args = {"text": "x" * (MAX_DISPLAY_ARGUMENT_BYTES + 10)}
    shown = display_arguments(args)
    assert shown["_truncated"] is True
    assert len(shown["preview"]) < MAX_DISPLAY_ARGUMENT_BYTES


def test_unprintable_arguments():
    assert display_arguments({"x": object()}) == {"_unprintable": True}
