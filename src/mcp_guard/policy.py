"""Decide which MCP messages need the user's approval, and summarize them for the popup."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .jsonrpc import Kind, Message

DEFAULT_GATED_METHODS = frozenset({"tools/call"})

# Methods the user may also choose to gate in config.
OPTIONAL_GATED_METHODS = frozenset({"resources/read", "prompts/get"})


@dataclass(frozen=True)
class Policy:
    gated_methods: frozenset[str] = DEFAULT_GATED_METHODS

    def needs_approval(self, msg: Message) -> bool:
        """Only requests can need approval. Notifications and responses never do."""
        return msg.kind is Kind.REQUEST and msg.method in self.gated_methods

    def gated_requests(self, msg: Message) -> list[Message]:
        """Return every request in `msg` (single or batch) that needs approval."""
        if msg.kind is Kind.BATCH:
            return [item for item in msg.items if self.needs_approval(item)]
        return [msg] if self.needs_approval(msg) else []


@dataclass(frozen=True)
class RequestSummary:
    """What the popup and the audit log show about a pending request."""

    server: str
    method: str
    tool: str | None
    arguments: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "server": self.server,
            "method": self.method,
            "tool": self.tool,
            "arguments": self.arguments,
        }


def summarize(msg: Message, server: str) -> RequestSummary:
    """Pull out the server, tool name, and arguments of a request."""
    if msg.kind is not Kind.REQUEST:
        raise ValueError("only requests can be summarized")
    method = msg.method or ""
    params = msg.params if isinstance(msg.params, dict) else {}
    name = params.get("name")
    name = name if isinstance(name, str) else None

    if method in ("tools/call", "prompts/get"):
        return RequestSummary(server, method, name, params.get("arguments", {}))
    if method == "resources/read":
        return RequestSummary(server, method, None, {"uri": params.get("uri")})
    return RequestSummary(server, method, None, msg.params)
