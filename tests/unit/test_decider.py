import asyncio

import pytest

from mcp_guard.approval.decider import (
    Decision,
    StaticDecider,
    Verdict,
    WaitDecider,
    decide_with_timeout,
    make_decider,
)
from mcp_guard.policy import RequestSummary

SUMMARY = RequestSummary("files", "tools/call", "echo", {"text": "hi"})


async def test_static_allow():
    v = await decide_with_timeout(StaticDecider(Decision.ALLOW), SUMMARY, 1)
    assert v.allowed


async def test_static_block():
    v = await decide_with_timeout(StaticDecider(Decision.BLOCK), SUMMARY, 1)
    assert not v.allowed
    assert "Blocked" in v.reason


async def test_timeout_blocks():
    v = await decide_with_timeout(WaitDecider(), SUMMARY, 0.05)
    assert not v.allowed
    assert "no answer within 0.05 seconds" in v.reason


async def test_decider_error_blocks():
    class Broken:
        async def decide(self, summary):
            raise RuntimeError("boom")

    v = await decide_with_timeout(Broken(), SUMMARY, 1)
    assert not v.allowed
    assert "RuntimeError" in v.reason


async def test_bad_answer_blocks():
    class Wrong:
        async def decide(self, summary):
            return True  # not a Verdict

    v = await decide_with_timeout(Wrong(), SUMMARY, 1)
    assert not v.allowed


async def test_cancel_is_passed_on():
    task = asyncio.create_task(decide_with_timeout(WaitDecider(), SUMMARY, 10))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_make_decider():
    assert isinstance(make_decider("wait"), WaitDecider)
    assert make_decider("allow").decision is Decision.ALLOW
    assert make_decider("block").decision is Decision.BLOCK
    with pytest.raises(ValueError):
        make_decider("maybe")


def test_verdict_allowed_flag():
    assert Verdict(Decision.ALLOW, "").allowed
    assert not Verdict(Decision.BLOCK, "").allowed
