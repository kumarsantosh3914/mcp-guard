#!/usr/bin/env python3
"""Development helper: talk to an MCP Streamable HTTP URL the way a real client does.

The owner runs this to debug the gateway. It needs the official MCP SDK, which is
only a debugging tool here, not a dependency of mcp-guard:

    .venv/bin/pip install mcp

Usage:
  .venv/bin/python scripts/dev_mcp_client.py URL          official SDK client: initialize, list tools, call echo
  .venv/bin/python scripts/dev_mcp_client.py URL --raw    plain HTTP: print status, headers, and body of each step

The URL holds the gateway token. Do not paste it into chats or tickets.
"""
import asyncio
import json
import sys
import traceback


def field(obj, snake, camel):
    """Newer SDKs use snake_case names (server_info), older ones camelCase (serverInfo)."""
    return getattr(obj, snake, None) if hasattr(obj, snake) else getattr(obj, camel, None)


def short(value, limit=600):
    text = value if isinstance(value, str) else json.dumps(value)
    return text if len(text) <= limit else text[:limit] + "...(cut)"


async def sdk_client(url):
    try:
        from mcp import ClientSession
    except ImportError:
        print("The MCP SDK is not installed. Run: .venv/bin/pip install mcp")
        return 2
    try:
        from mcp.client.streamable_http import streamablehttp_client as connect
    except ImportError:
        from mcp.client.streamable_http import streamable_http_client as connect

    print("1. connecting")
    async with connect(url) as streams:
        read, write = streams[0], streams[1]
        get_session_id = streams[2] if len(streams) > 2 else (lambda: None)
        async with ClientSession(read, write) as session:
            print("2. initialize")
            result = await session.initialize()
            info = field(result, "server_info", "serverInfo")
            print("   server:   %s %s" % (info.name, info.version))
            print("   protocol: %s" % field(result, "protocol_version", "protocolVersion"))
            sid = get_session_id() or ""
            print("   session:  %s" % (sid[:6] + "..." if sid else "(none)"))
            print("3. tools/list")
            tools = await session.list_tools()
            print("   tools: %s" % [t.name for t in tools.tools])
            print("4. tools/call echo  (answer the mcp-guard popup now)")
            r = await session.call_tool("echo", {"text": "hello from the SDK"})
            text = r.content[0].text if r.content else ""
            print("   isError: %s" % field(r, "is_error", "isError"))
            print("   text:    %s" % text)
    print("OK: the official client finished every step.")
    return 0


async def raw_client(url):
    import aiohttp  # already installed with mcp-guard; no extra package needed

    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-11-25",
    }
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                       "clientInfo": {"name": "mcp-guard-debug", "version": "0"}}}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=70)) as client:
        async def step(name, body, extra=None):
            print("== %s" % name)
            async with client.post(url, json=body, headers={**headers, **(extra or {})}) as r:
                text = await r.text()
                print("status:  %d %s" % (r.status, r.reason))
                for k, v in r.headers.items():
                    if k.lower() != "set-cookie":
                        print("header:  %s: %s" % (k, v))
                print("body:    %s" % short(text or "(empty)"))
                return r.headers.get("Mcp-Session-Id")

        sid = await step("initialize", init)
        if not sid:
            print("No Mcp-Session-Id header. Stopping.")
            return 1
        extra = {"Mcp-Session-Id": sid}
        await step("notifications/initialized", {"jsonrpc": "2.0", "method": "notifications/initialized"}, extra)
        await step("tools/list", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, extra)

        # Clients open this stream after initialize. mcp-guard answers 405 (allowed by the spec).
        # Anything else here means something between the client and mcp-guard answered instead.
        print("== GET stream (expected from mcp-guard: 405)")
        get_headers = {"Accept": "text/event-stream", "Mcp-Session-Id": sid,
                       "MCP-Protocol-Version": headers["MCP-Protocol-Version"]}
        try:
            async with client.get(url, headers=get_headers, timeout=aiohttp.ClientTimeout(total=20)) as r:
                print("status:  %d %s" % (r.status, r.reason))
                for k, v in r.headers.items():
                    if k.lower() != "set-cookie":
                        print("header:  %s: %s" % (k, v))
                try:
                    chunk = await asyncio.wait_for(r.content.read(400), 10)
                    print("body:    %s" % short(chunk.decode("utf-8", "replace") or "(empty)"))
                except TimeoutError:
                    print("body:    (no data within 10 s, stream left open)")
        except TimeoutError:
            print("GET timed out after 20 s")
    return 0


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2
    runner = raw_client if "--raw" in argv else sdk_client
    try:
        return asyncio.run(runner(args[0]))
    except BaseException as exc:  # the SDK wraps errors in exception groups; show all of them
        if isinstance(exc, KeyboardInterrupt):
            raise
        print("\nFAILED. Full error:")
        traceback.print_exception(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
