import json
import os
import stat

from mcp_guard.approval.protocol import ApprovalRequest
from mcp_guard.audit import MASK, TRUNCATE_CHARS, AuditLog, mask_secrets, prepare_arguments


def req(arguments=None):
    return ApprovalRequest("r1", "desktop", "files", "tools/call", "write_file",
                           {"path": "a.txt"} if arguments is None else arguments, 45.0)


def test_mask_secret_keys_at_any_depth():
    data = {
        "path": "a.txt",
        "api_key": "k1",
        "Authorization": "Bearer x",
        "nested": {"password": "p", "list": [{"accessToken": "t", "ok": 1}]},
    }
    assert mask_secrets(data) == {
        "path": "a.txt",
        "api_key": MASK,
        "Authorization": MASK,
        "nested": {"password": MASK, "list": [{"accessToken": MASK, "ok": 1}]},
    }


def test_mask_leaves_plain_values():
    assert mask_secrets("text") == "text"
    assert mask_secrets([1, "a"]) == [1, "a"]


def test_prepare_modes():
    args = {"path": "a.txt", "token": "t"}
    assert prepare_arguments(args, "none") is None
    assert prepare_arguments(args, "full") == {"path": "a.txt", "token": MASK}
    assert prepare_arguments(args, "truncated") == {"path": "a.txt", "token": MASK}


def test_truncated_mode_cuts_long_arguments_after_masking():
    args = {"secret": "s" * 10, "body": "x" * (TRUNCATE_CHARS * 2)}
    out = prepare_arguments(args, "truncated")
    assert out["_truncated"] is True
    assert len(out["preview"]) == TRUNCATE_CHARS
    assert "ssssssssss" not in out["preview"]


def test_display_preview_is_never_logged():
    preview = {"_truncated": True, "preview": '{"password": "hunter2", ...'}
    out = prepare_arguments(preview, "full")
    assert "hunter2" not in json.dumps(out)


def test_writes_one_private_line_per_decision(tmp_path):
    path = tmp_path / "logs" / "audit.jsonl"
    log = AuditLog(path)
    log.open()
    log.record(req({"path": "a.txt", "token": "t"}), "allow", "Allowed by the user.", 1.234)
    log.record(req(), "block", "Blocked by the user via mcp-guard.", 0.5)
    log.close()

    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert [x["decision"] for x in lines] == ["allow", "block"]
    first = lines[0]
    assert first["source"] == "desktop"
    assert first["server"] == "files"
    assert first["tool"] == "write_file"
    assert first["decided_in_ms"] == 1234
    assert first["arguments"] == {"path": "a.txt", "token": MASK}
    assert first["time"].endswith("+00:00")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


def test_appends_and_fixes_open_permissions(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text('{"old": true}\n')
    os.chmod(path, 0o644)
    log = AuditLog(path)
    log.open()
    log.record(req(), "allow", "ok", 0)
    log.close()
    assert len(path.read_text().splitlines()) == 2  # old entry kept
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_none_mode_leaves_out_arguments(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, log_arguments="none")
    log.open()
    log.record(req(), "allow", "ok", 0)
    log.close()
    assert "arguments" not in json.loads(path.read_text())


def test_record_never_raises(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")  # never opened
    log.record(req(), "allow", "ok", 0)
