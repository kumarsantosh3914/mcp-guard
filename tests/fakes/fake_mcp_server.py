#!/usr/bin/env python3
"""A small fake MCP server for tests. Standard library only.

Speaks MCP over stdio: one JSON-RPC message per line.

Tools:
  echo   returns the "text" argument
  slow   waits "seconds" (default 2, max 120), then answers
  fail   always returns a tool error

Options:
  --record PATH   append one JSON line for every tools/call that reaches this server.
                  Tests use it to prove that blocked calls never arrive.

Each tools/call runs in its own thread, so slow calls do not hold up others.
"""
import argparse
import json
import sys
import threading
import time

PROTOCOL_VERSION = "2025-06-18"

TOOLS = [
    {
        "name": "echo",
        "description": "Return the given text.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "slow",
        "description": "Wait for some seconds, then answer.",
        "inputSchema": {"type": "object", "properties": {"seconds": {"type": "number"}}},
    },
    {
        "name": "fail",
        "description": "Always return a tool error.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

_write_lock = threading.Lock()
_record_lock = threading.Lock()


def send(obj):
    line = json.dumps(obj, separators=(",", ":")) + "\n"
    with _write_lock:
        sys.stdout.write(line)
        sys.stdout.flush()


def send_result(req_id, result):
    send({"jsonrpc": "2.0", "id": req_id, "result": result})


def send_error(req_id, code, message):
    send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def text_result(text, is_error=False):
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def record_call(path, name, arguments):
    if not path:
        return
    with _record_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"tool": name, "arguments": arguments}) + "\n")


def call_tool(req_id, params, record_path):
    name = params.get("name")
    args = params.get("arguments") or {}
    record_call(record_path, name, args)
    if name == "echo":
        send_result(req_id, text_result(str(args.get("text", ""))))
    elif name == "slow":
        seconds = max(0.0, min(float(args.get("seconds", 2)), 120.0))
        time.sleep(seconds)
        send_result(req_id, text_result("done after " + str(seconds) + "s"))
    elif name == "fail":
        send_result(req_id, text_result("this tool always fails", is_error=True))
    else:
        send_error(req_id, -32602, "unknown tool: " + str(name))


def handle(msg, record_path, threads):
    if not isinstance(msg, dict):
        send_error(None, -32600, "invalid request")
        return
    method = msg.get("method")
    if method is None or "id" not in msg:
        return  # a notification or a response: nothing to answer
    req_id = msg["id"]
    if method == "initialize":
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        send_result(req_id, {
            "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake-mcp-server", "version": "0.1.0"},
        })
    elif method == "ping":
        send_result(req_id, {})
    elif method == "tools/list":
        send_result(req_id, {"tools": TOOLS})
    elif method == "tools/call":
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        t = threading.Thread(target=call_tool, args=(req_id, params, record_path), daemon=True)
        t.start()
        threads.append(t)
    else:
        send_error(req_id, -32601, "method not found: " + str(method))


def main():
    parser = argparse.ArgumentParser(description="Fake MCP server for tests.")
    parser.add_argument("--record", help="file to append received tools/call messages to")
    args = parser.parse_args()

    threads = []
    while True:
        line = sys.stdin.readline()
        if not line:
            break  # stdin closed
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            send_error(None, -32700, "parse error")
            continue
        handle(msg, args.record, threads)

    for t in threads:
        t.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
