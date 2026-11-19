"""DaemonRuntime runs the socket server in its own thread. Decisions come from another thread."""
import asyncio
import contextlib

from mcp_guard.approval.client import DaemonDecider
from mcp_guard.approval.decider import Decision
from mcp_guard.approval.queue import STOPPING_TEXT
from mcp_guard.daemon.runtime import DaemonRuntime
from mcp_guard.policy import RequestSummary

SUMMARY = RequestSummary("files", "tools/call", "echo", {"text": "hi"})


async def test_decision_from_another_thread(socket_path):
    holder = {}
    seen = set()

    def on_change(views):  # runs in the runtime thread, like the Qt bridge would
        for view in views:
            rid = view.request.request_id
            if rid not in seen:
                seen.add(rid)
                holder["runtime"].decide(rid, Decision.ALLOW)

    runtime = DaemonRuntime(socket_path, on_change)
    holder["runtime"] = runtime
    await asyncio.to_thread(runtime.start)
    try:
        v = await DaemonDecider(socket_path, timeout=5).decide(SUMMARY)
        assert v.allowed
    finally:
        await asyncio.to_thread(runtime.stop)
    assert not socket_path.exists()


async def test_stop_blocks_waiting_requests(socket_path):
    runtime = DaemonRuntime(socket_path)
    await asyncio.to_thread(runtime.start)
    task = asyncio.create_task(DaemonDecider(socket_path, timeout=10).decide(SUMMARY))
    for _ in range(300):
        if runtime.queue is not None and len(runtime.queue) == 1:
            break
        await asyncio.sleep(0.01)
    await asyncio.to_thread(runtime.stop)
    v = await asyncio.wait_for(task, 3)
    assert not v.allowed and v.reason == STOPPING_TEXT


async def test_start_fails_when_already_running(socket_path):
    first = DaemonRuntime(socket_path)
    await asyncio.to_thread(first.start)
    try:
        second = DaemonRuntime(socket_path)
        raised = None
        try:
            await asyncio.to_thread(second.start)
        except Exception as exc:  # AlreadyRunning
            raised = exc
        assert type(raised).__name__ == "AlreadyRunning"
    finally:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(first.stop)
