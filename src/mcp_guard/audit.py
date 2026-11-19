"""Audit log: one JSON line per decision.

Only the daemon writes it, so lines from different requests never mix.
The file is private (mode 600) in a private folder (mode 700).
Values under secret-looking keys are masked. Old entries are never removed.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from .approval.protocol import ApprovalRequest

log = logging.getLogger("mcp_guard.audit")

MASK = "***"
TRUNCATE_CHARS = 2000
MAX_DEPTH = 20
SECRET_KEY_PARTS = (
    "token", "password", "passwd", "secret", "key", "authorization",
    "cookie", "credential", "session", "private",
)


def is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SECRET_KEY_PARTS)


def mask_secrets(value: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        return "[too deep]"
    if isinstance(value, dict):
        return {
            k: MASK if isinstance(k, str) and is_secret_key(k) else mask_secrets(v, depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [mask_secrets(v, depth + 1) for v in value]
    return value


def prepare_arguments(arguments: Any, mode: str) -> Any:
    """Arguments as they go into the log, for mode "none", "truncated", or "full"."""
    if mode == "none":
        return None
    if isinstance(arguments, dict) and (arguments.get("_truncated") or arguments.get("_unprintable")):
        # Already a display preview. Its text cannot be masked, so it is not logged.
        return {"_not_logged": "too large to log safely"}
    masked = mask_secrets(arguments)
    if mode == "full":
        return masked
    text = json.dumps(masked, ensure_ascii=False, default=str)
    if len(text) <= TRUNCATE_CHARS:
        return masked
    return {"_truncated": True, "preview": text[:TRUNCATE_CHARS]}


class AuditLog:
    def __init__(self, path: Path, log_arguments: str = "truncated") -> None:
        self.path = Path(path)
        self.log_arguments = log_arguments
        self._file: TextIO | None = None

    def open(self) -> None:
        """Open for appending. Raises OSError if the file cannot be opened."""
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        os.fchmod(fd, 0o600)  # also fix a file that already existed with wider access
        self._file = os.fdopen(fd, "a", encoding="utf-8", buffering=1)

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def record(self, request: ApprovalRequest, decision: str, reason: str, decided_in: float) -> None:
        """Write one entry. Never raises: a logging problem must not change a decision."""
        entry: dict[str, Any] = {
            "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "source": request.source,
            "server": request.server,
            "method": request.method,
            "tool": request.tool,
            "decision": decision,
            "reason": reason,
            "decided_in_ms": round(decided_in * 1000),
        }
        if self.log_arguments != "none":
            entry["arguments"] = prepare_arguments(request.arguments, self.log_arguments)
        try:
            if self._file is None:
                raise OSError("audit log is not open")
            self._file.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            self._file.flush()
        except OSError:
            log.exception("could not write to the audit log %s", self.path)
