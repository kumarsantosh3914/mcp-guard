#!/usr/bin/env python3
"""PreToolUse guard for mcp-guard.

Reads the tool call as JSON from stdin.
Exit 0 means allow (the normal permission flow still runs).
Exit 2 means block. Any error also blocks (fail closed).
"""
import json
import os
import re
import shlex
import sys


def block(reason):
    print("BLOCKED by mcp-guard: " + reason + ". Ask the owner.", file=sys.stderr)
    sys.exit(2)


# Only these tools may run. Everything else (MCP, web, cron, publish, worktree...) is blocked.
ALLOWED_TOOLS = {
    "Read", "Edit", "Write", "NotebookEdit", "Glob", "Grep", "Bash",
    "Agent", "Skill", "AskUserQuestion", "ToolSearch",
    "EnterPlanMode", "ExitPlanMode", "TodoWrite",
    "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TaskOutput", "ListAgents",
}

WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}
PATH_KEYS = ("file_path", "path", "notebook_path")

# Words that must never appear as a command or argument in Bash.
BLOCKED_WORDS = [
    # git and hosting
    "git", "gh", "hub",
    # delete
    "rm", "rmdir", "unlink", "trash", "shred",
    # privilege and system
    "sudo", "su", "doas", "chmod", "chown", "chgrp", "dd", "mkfs", "diskutil",
    "kill", "pkill", "killall", "crontab", "launchctl", "osascript", "open",
    # network
    "curl", "wget", "ssh", "scp", "sftp", "rsync", "nc", "ncat", "telnet", "ftp",
    # containers
    "docker", "podman", "kubectl",
    # indirection and obfuscation
    "eval", "xargs", "base64",
    # package managers and runners
    "npx", "brew", "apt", "apt-get", "gem", "cargo",
    # database clients and migration tools
    "psql", "mysql", "mariadb", "sqlite3", "mongo", "mongosh", "redis-cli",
    "sqlcmd", "prisma", "drizzle-kit", "sequelize", "knex", "alembic",
    "flyway", "liquibase", "supabase",
]

BLOCKED_PATTERNS = [
    (r"\b(npm|pnpm|yarn|bun)\s+(i|install|add|remove|rm|uninstall|update|upgrade|publish|exec|dlx|link)\b",
     "package install or publish"),
    (r"\b(pip3?|pipx|uv|poetry|conda)\b.*\b(install|add|remove|uninstall|sync)\b",
     "package install"),
    (r"\b(python3?|node|perl|ruby|php|deno|bun)\s+(-c|-e|-p|--eval)\b",
     "inline code execution"),
    (r"\s-(delete|exec|execdir|ok)\b", "find -delete or -exec"),
    (r"\b(drop|truncate)\s+(table|database|schema|index|view)\b", "destructive SQL"),
    (r"\bdelete\s+from\b", "destructive SQL"),
    (r"\balter\s+table\b", "schema change"),
    (r"\$\(|`", "command substitution"),
    (r"://", "network URL"),
]

WORD_RE = re.compile(
    r"(?:^|[\s;&|(){}<>=/\"'])(" + "|".join(re.escape(w) for w in BLOCKED_WORDS)
    + r")(?=$|[\s;&|(){}<>\"'])"
)


def main():
    try:
        data = json.load(sys.stdin)
        tool = data.get("tool_name", "")
        inp = data.get("tool_input") or {}
        cwd = data.get("cwd") or ""
        root = os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or cwd)
    except Exception:
        block("could not read hook input")
        return
    if not root or not os.path.isdir(root):
        block("project root unknown")

    def resolve(p):
        p = os.path.expanduser(p)
        if not os.path.isabs(p):
            p = os.path.join(cwd or root, p)
        return os.path.realpath(p)

    def inside(p):
        if p == "/dev/null":
            return True
        r = resolve(p)
        return r == root or r.startswith(root + os.sep)

    def rel(p):
        return os.path.relpath(resolve(p), root)

    def is_protected_write(p):
        r = rel(p)
        base = os.path.basename(r)
        return (
            r.startswith(".claude" + os.sep)
            or r == ".claude"
            or base.startswith(".env")
            or r.startswith("secrets" + os.sep)
        )

    def is_secret(p):
        r = rel(p)
        return os.path.basename(r).startswith(".env") or r.startswith("secrets" + os.sep)

    if tool not in ALLOWED_TOOLS:
        block("tool '" + tool + "' is not on the allowlist")

    if tool == "Bash":
        cmd = inp.get("command", "")
        if inp.get("dangerouslyDisableSandbox"):
            block("sandbox bypass requested")
        if inp.get("run_in_background"):
            block("background commands are not allowed")
        for pat, why in BLOCKED_PATTERNS:
            if re.search(pat, cmd, re.IGNORECASE):
                block(why)
        m = WORD_RE.search(cmd)
        if m:
            block("command '" + m.group(1) + "' is not allowed")
        try:
            lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
            lex.whitespace_split = True
            tokens = list(lex)
        except ValueError:
            block("could not parse command")
            return
        for tok in tokens:
            if not tok or set(tok) <= set(";&|<>()"):
                continue
            cands = [tok]
            if "=" in tok:
                cands.append(tok.split("=", 1)[1])
            for c in cands:
                if not c:
                    continue
                if c.startswith("$") or c.startswith("~"):
                    block("home or variable path '" + c + "'")
                if re.search(r"(^|/)\.\.(/|$)", c):
                    block("path with '..'")
                if c.startswith("/") and not inside(c):
                    block("path outside project '" + c + "'")
        return

    # File tools
    for key in PATH_KEYS:
        val = inp.get(key)
        if val:
            if not inside(val):
                block("path outside project '" + val + "'")
            if tool in WRITE_TOOLS and is_protected_write(val):
                block("protected file '" + val + "'")
            if is_secret(val):
                block("secret file '" + val + "'")
    pattern = inp.get("pattern") if tool == "Glob" else None
    if pattern and (pattern.startswith("/") or pattern.startswith("~") or ".." in pattern):
        block("glob pattern leaves the project")


if __name__ == "__main__":
    main()
    sys.exit(0)
