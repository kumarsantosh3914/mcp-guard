"""JSON-RPC 2.0 helpers for MCP messages.

The MCP stdio transport sends one JSON-RPC message per line, UTF-8 encoded.
This module only parses, classifies, and builds messages. It does no I/O.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

JSONRPC_VERSION = "2.0"

# Standard JSON-RPC error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# mcp-guard error code for a request the user blocked (server error range).
REQUEST_BLOCKED = -32003

BLOCKED_TEXT = "Blocked by the user via mcp-guard."


class Kind(Enum):
    REQUEST = "request"
    NOTIFICATION = "notification"
    RESPONSE = "response"
    BATCH = "batch"
    INVALID = "invalid"


class ParseError(ValueError):
    """The input is not valid UTF-8 JSON."""


@dataclass(frozen=True)
class Message:
    """A decoded message and its kind. `items` is set only for a batch."""

    kind: Kind
    raw: Any
    items: tuple["Message", ...] = ()

    @property
    def id(self) -> Any:
        return self.raw.get("id") if isinstance(self.raw, dict) else None

    @property
    def method(self) -> str | None:
        if isinstance(self.raw, dict):
            method = self.raw.get("method")
            return method if isinstance(method, str) else None
        return None

    @property
    def params(self) -> Any:
        return self.raw.get("params") if isinstance(self.raw, dict) else None


def _valid_id(value: Any) -> bool:
    """MCP ids are strings or integers. Null, floats, and booleans are not allowed."""
    return isinstance(value, (str, int)) and not isinstance(value, bool)


def _classify_single(obj: Any) -> Message:
    if not isinstance(obj, dict) or obj.get("jsonrpc") != JSONRPC_VERSION:
        return Message(Kind.INVALID, obj)

    if "method" in obj:
        if not isinstance(obj["method"], str):
            return Message(Kind.INVALID, obj)
        if "params" in obj and not isinstance(obj["params"], (dict, list)):
            return Message(Kind.INVALID, obj)
        if "id" not in obj:
            return Message(Kind.NOTIFICATION, obj)
        if _valid_id(obj["id"]):
            return Message(Kind.REQUEST, obj)
        return Message(Kind.INVALID, obj)

    has_result = "result" in obj
    has_error = "error" in obj
    if "id" in obj and has_result != has_error:
        # Error responses may have a null id (for example, after a parse error).
        if obj["id"] is None or _valid_id(obj["id"]):
            return Message(Kind.RESPONSE, obj)
    return Message(Kind.INVALID, obj)


def classify(obj: Any) -> Message:
    """Classify an already decoded JSON value."""
    if isinstance(obj, list):
        if not obj:
            return Message(Kind.INVALID, obj)
        return Message(Kind.BATCH, obj, tuple(_classify_single(item) for item in obj))
    return _classify_single(obj)


def parse_line(data: str | bytes) -> Message:
    """Decode one line of input. Raises ParseError if it is not valid JSON."""
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ParseError("message is not valid UTF-8") from exc
    text = data.strip()
    if not text:
        raise ParseError("empty message")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError("message is not valid JSON: " + str(exc)) from exc
    return classify(obj)


def make_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": req_id, "result": result}


def make_error(req_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": req_id, "error": error}


def blocked_response(request: Message, reason: str = BLOCKED_TEXT) -> dict[str, Any]:
    """Build the answer Claude gets when a request is blocked.

    For tools/call this is a normal tool result with isError set, so the model
    can read the reason and tell the user. Other methods get a JSON-RPC error.
    """
    if request.kind is not Kind.REQUEST:
        raise ValueError("only requests can be blocked")
    if request.method == "tools/call":
        return make_result(
            request.id,
            {"content": [{"type": "text", "text": reason}], "isError": True},
        )
    return make_error(request.id, REQUEST_BLOCKED, reason)


def encode(obj: Any) -> bytes:
    """Encode one message as a single UTF-8 line ending in a newline."""
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def cancelled_request_id(msg: Message) -> str | int | None:
    """Return the request id from a notifications/cancelled message, else None."""
    if msg.kind is Kind.NOTIFICATION and msg.method == "notifications/cancelled":
        params = msg.params
        if isinstance(params, dict) and _valid_id(params.get("requestId")):
            return params["requestId"]
    return None
