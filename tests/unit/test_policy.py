import pytest

from mcp_guard.jsonrpc import classify
from mcp_guard.policy import DEFAULT_GATED_METHODS, Policy, summarize


def req(method, params=None, req_id=1):
    obj = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        obj["params"] = params
    return classify(obj)


CALL = req("tools/call", {"name": "echo", "arguments": {"text": "hi"}})


# --- needs_approval ---

def test_default_gates_tools_call_only():
    assert DEFAULT_GATED_METHODS == {"tools/call"}
    p = Policy()
    assert p.needs_approval(CALL)
    assert not p.needs_approval(req("tools/list"))
    assert not p.needs_approval(req("initialize"))
    assert not p.needs_approval(req("resources/read", {"uri": "file:///x"}))


def test_notifications_and_responses_never_need_approval():
    p = Policy()
    note = classify({"jsonrpc": "2.0", "method": "tools/call"})
    resp = classify({"jsonrpc": "2.0", "id": 1, "result": {}})
    assert not p.needs_approval(note)
    assert not p.needs_approval(resp)


def test_custom_gated_methods():
    p = Policy(frozenset({"tools/call", "resources/read"}))
    assert p.needs_approval(req("resources/read", {"uri": "file:///x"}))
    assert not p.needs_approval(req("prompts/get", {"name": "p"}))


def test_gated_requests_single():
    p = Policy()
    assert p.gated_requests(CALL) == [CALL]
    assert p.gated_requests(req("ping")) == []


def test_gated_requests_batch():
    batch = classify([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "a"}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "b"}},
    ])
    assert [m.id for m in Policy().gated_requests(batch)] == [1, 3]


# --- summarize ---

def test_summarize_tool_call():
    s = summarize(CALL, "files")
    assert s.to_dict() == {
        "server": "files",
        "method": "tools/call",
        "tool": "echo",
        "arguments": {"text": "hi"},
    }


def test_summarize_tool_call_without_arguments():
    s = summarize(req("tools/call", {"name": "echo"}), "files")
    assert s.arguments == {}


def test_summarize_tool_call_with_bad_name():
    s = summarize(req("tools/call", {"name": 42}), "files")
    assert s.tool is None


def test_summarize_resources_read():
    s = summarize(req("resources/read", {"uri": "file:///a.txt"}), "files")
    assert s.tool is None
    assert s.arguments == {"uri": "file:///a.txt"}


def test_summarize_prompts_get():
    s = summarize(req("prompts/get", {"name": "review", "arguments": {"lang": "py"}}), "files")
    assert (s.tool, s.arguments) == ("review", {"lang": "py"})


def test_summarize_missing_params():
    s = summarize(req("tools/call"), "files")
    assert (s.tool, s.arguments) == (None, {})


def test_summarize_needs_a_request():
    with pytest.raises(ValueError):
        summarize(classify({"jsonrpc": "2.0", "method": "x"}), "files")
