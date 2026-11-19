"""Messages between a proxy and the daemon.

One Unix socket connection per request:

  proxy  -> daemon : one "request" line
  daemon -> proxy  : one "verdict" line, then the daemon closes the connection

If the proxy closes the connection first, the daemon treats it as a cancel.
If the daemon closes without a verdict, the proxy treats it as Block.
Every message is one JSON object on one line, with "v" (protocol version) and "type".
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_TIMEOUT_SECONDS = 300.0
MAX_DISPLAY_ARGUMENT_BYTES = 64 * 1024
PREVIEW_CHARS = 16 * 1024

SOCKET_ENV = "MCP_GUARD_SOCKET"
SOURCES = ("desktop", "web")
DECISIONS = ("allow", "block")


class ProtocolError(ValueError):
    """A message that does not follow this protocol."""


def default_socket_path() -> Path:
    env = os.environ.get(SOCKET_ENV)
    if env:
        return Path(env)
    return Path.home() / "Library" / "Application Support" / "mcp-guard" / "daemon.sock"


def display_arguments(arguments: Any) -> Any:
    """Arguments as the popup should get them: unchanged if small, else a short preview.

    The real request still goes to the server unchanged. This only limits what is shown.
    """
    try:
        text = json.dumps(arguments, ensure_ascii=False)
    except (TypeError, ValueError):
        return {"_unprintable": True}
    if len(text.encode("utf-8")) <= MAX_DISPLAY_ARGUMENT_BYTES:
        return arguments
    return {"_truncated": True, "preview": text[:PREVIEW_CHARS]}


def _dump(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _load(line: bytes, expected_type: str) -> dict[str, Any]:
    if len(line) > MAX_MESSAGE_BYTES:
        raise ProtocolError("message too large")
    try:
        obj = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("message is not valid JSON") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("message is not a JSON object")
    if obj.get("v") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    if obj.get("type") != expected_type:
        raise ProtocolError("expected a %r message" % expected_type)
    return obj


def _text(obj: dict[str, Any], key: str, max_len: int, optional: bool = False) -> str | None:
    value = obj.get(key)
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value or len(value) > max_len:
        raise ProtocolError("bad field: " + key)
    return value


@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str
    source: str  # "desktop" or "web"
    server: str
    method: str
    tool: str | None
    arguments: Any
    timeout: float  # seconds the daemon waits before it blocks

    def to_line(self) -> bytes:
        return _dump({
            "v": PROTOCOL_VERSION,
            "type": "request",
            "id": self.request_id,
            "source": self.source,
            "server": self.server,
            "method": self.method,
            "tool": self.tool,
            "arguments": self.arguments,
            "timeout": self.timeout,
        })

    @classmethod
    def parse(cls, line: bytes) -> "ApprovalRequest":
        obj = _load(line, "request")
        source = _text(obj, "source", 16)
        if source not in SOURCES:
            raise ProtocolError("bad field: source")
        timeout = obj.get("timeout")
        if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
                or not 0 < timeout <= MAX_TIMEOUT_SECONDS):
            raise ProtocolError("bad field: timeout")
        if "arguments" not in obj:
            raise ProtocolError("missing field: arguments")
        return cls(
            request_id=_text(obj, "id", 64),
            source=source,
            server=_text(obj, "server", 256),
            method=_text(obj, "method", 256),
            tool=_text(obj, "tool", 256, optional=True),
            arguments=obj["arguments"],
            timeout=float(timeout),
        )


@dataclass(frozen=True)
class VerdictMessage:
    request_id: str
    decision: str  # "allow" or "block"
    reason: str

    def to_line(self) -> bytes:
        return _dump({
            "v": PROTOCOL_VERSION,
            "type": "verdict",
            "id": self.request_id,
            "decision": self.decision,
            "reason": self.reason,
        })

    @classmethod
    def parse(cls, line: bytes) -> "VerdictMessage":
        obj = _load(line, "verdict")
        decision = _text(obj, "decision", 16)
        if decision not in DECISIONS:
            raise ProtocolError("bad field: decision")
        return cls(_text(obj, "id", 64), decision, _text(obj, "reason", 1000))
