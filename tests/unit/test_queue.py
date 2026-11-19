import asyncio
import time

import pytest

from mcp_guard.approval.decider import Decision
from mcp_guard.approval.protocol import ApprovalRequest
from mcp_guard.approval.queue import ALLOWED_TEXT, STOPPING_TEXT, TOO_MANY_TEXT, ApprovalQueue
from mcp_guard.jsonrpc import BLOCKED_TEXT


def req(rid="r1", timeout=45.0, tool="echo"):
    return ApprovalRequest(rid, "desktop", "files", "tools/call", tool, {}, timeout)


async def test_allow():
    q = ApprovalQueue()
    fut = q.submit(req())
    assert q.decide("r1", Decision.ALLOW)
    v = await fut
    assert v.allowed and v.reason == ALLOWED_TEXT
    assert len(q) == 0


async def test_block():
    q = ApprovalQueue()
    fut = q.submit(req())
    q.decide("r1", Decision.BLOCK)
    v = await fut
    assert not v.allowed and v.reason == BLOCKED_TEXT


async def test_timeout_blocks():
    q = ApprovalQueue()
    v = await asyncio.wait_for(q.submit(req(timeout=0.05)), 2)
    assert not v.allowed
    assert "no answer within 0.05 seconds" in v.reason
    assert len(q) == 0


async def test_cancel():
    q = ApprovalQueue()
    fut = q.submit(req())
    assert q.cancel("r1")
    assert fut.cancelled()
    assert not q.decide("r1", Decision.ALLOW)
    assert not q.cancel("r1")


async def test_decide_unknown_id():
    assert not ApprovalQueue().decide("nope", Decision.ALLOW)


async def test_duplicate_id_is_refused():
    q = ApprovalQueue()
    q.submit(req("a"))
    with pytest.raises(ValueError):
        q.submit(req("a"))


async def test_snapshot_is_oldest_first_with_deadline():
    q = ApprovalQueue()
    before = time.monotonic()
    for rid in ("a", "b", "c"):
        q.submit(req(rid, timeout=10))
    views = q.snapshot()
    assert [v.request.request_id for v in views] == ["a", "b", "c"]
    assert before + 9 < views[0].deadline < time.monotonic() + 10.1


async def test_on_change_sees_every_change():
    seen = []
    q = ApprovalQueue(on_change=lambda views: seen.append([v.request.request_id for v in views]))
    q.submit(req("a"))
    q.submit(req("b"))
    q.decide("a", Decision.ALLOW)
    q.cancel("b")
    assert seen == [["a"], ["a", "b"], ["b"], []]


async def test_broken_on_change_does_not_break_queue():
    def boom(views):
        raise RuntimeError("ui broke")

    q = ApprovalQueue(on_change=boom)
    fut = q.submit(req())
    q.decide("r1", Decision.ALLOW)
    assert (await fut).allowed


async def test_too_many_pending_blocks_at_once():
    q = ApprovalQueue(max_pending=2)
    q.submit(req("a"))
    q.submit(req("b"))
    v = await q.submit(req("c"))
    assert not v.allowed and v.reason == TOO_MANY_TEXT
    assert len(q) == 2


async def test_block_all():
    q = ApprovalQueue()
    futs = [q.submit(req(str(i))) for i in range(3)]
    q.block_all()
    for f in futs:
        v = await f
        assert not v.allowed and v.reason == STOPPING_TEXT
    assert len(q) == 0


async def test_twenty_requests_each_get_their_own_answer():
    q = ApprovalQueue()
    futs = {str(i): q.submit(req(str(i))) for i in range(20)}
    for i in range(20):
        q.decide(str(i), Decision.ALLOW if i % 2 == 0 else Decision.BLOCK)
    for rid, fut in futs.items():
        assert (await fut).allowed == (int(rid) % 2 == 0)


async def test_answer_after_timeout_is_ignored():
    q = ApprovalQueue()
    fut = q.submit(req(timeout=0.05))
    await asyncio.sleep(0.1)
    assert not q.decide("r1", Decision.ALLOW)
    assert not (await fut).allowed
