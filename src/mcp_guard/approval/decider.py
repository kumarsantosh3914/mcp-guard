"""Deciders answer one question: may this request go to the MCP server?

The real decider (the daemon with its popup) comes later. Until then the proxy
uses one of the simple deciders below. They are for tests and development only.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from ..policy import RequestSummary


class Decision(Enum):
    ALLOW = "allow"
    BLOCK = "block"


@dataclass(frozen=True)
class Verdict:
    decision: Decision
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


class Decider(Protocol):
    async def decide(self, summary: RequestSummary) -> Verdict:
        """Return a verdict. May wait as long as it likes; the caller applies the timeout."""
        ...


class StaticDecider:
    """Always gives the same answer. Development and tests only."""

    def __init__(self, decision: Decision) -> None:
        self.decision = decision

    async def decide(self, summary: RequestSummary) -> Verdict:
        if self.decision is Decision.ALLOW:
            return Verdict(Decision.ALLOW, "Allowed by the static allow decider (development only).")
        return Verdict(Decision.BLOCK, "Blocked by mcp-guard.")


class WaitDecider:
    """Never answers. Used to test timeouts and cancellation."""

    async def decide(self, summary: RequestSummary) -> Verdict:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


DEV_DECIDERS = ("block", "allow", "wait")


def make_decider(name: str) -> Decider:
    if name == "block":
        return StaticDecider(Decision.BLOCK)
    if name == "allow":
        return StaticDecider(Decision.ALLOW)
    if name == "wait":
        return WaitDecider()
    raise ValueError("unknown decider: " + repr(name))


async def decide_with_timeout(decider: Decider, summary: RequestSummary, timeout: float) -> Verdict:
    """Ask the decider, but never wait longer than `timeout` seconds.

    Fails closed: a timeout, an error, or a bad answer all mean Block.
    Cancellation is passed on to the caller.
    """
    try:
        verdict = await asyncio.wait_for(decider.decide(summary), timeout)
    except TimeoutError:
        return Verdict(Decision.BLOCK, "Blocked by mcp-guard: no answer within %g seconds." % timeout)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # any decider failure blocks the request
        return Verdict(
            Decision.BLOCK,
            "Blocked by mcp-guard: the approval check failed (%s)." % type(exc).__name__,
        )
    if not isinstance(verdict, Verdict):
        return Verdict(Decision.BLOCK, "Blocked by mcp-guard: the approval check gave no valid answer.")
    return verdict
