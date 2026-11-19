#!/usr/bin/env python3
"""PreToolUse guard for mcp-guard (version 2).

Reads the tool call as JSON from stdin.
Exit 0 with no output means allow (the normal permission flow still runs).
Exit 0 with a JSON "ask" decision means the owner must approve.
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


def ask(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": "mcp-guard: " + reason,
        }
    }))
    sys.exit(0)


# Only these tools may run. Everything else (MCP, cron, publish, worktree...) is blocked.
ALLOWED_TOOLS = {
    "Read", "Edit", "Write", "NotebookEdit", "Glob", "Grep", "Bash",
    "WebSearch", "WebFetch",
    "Agent", "Skill", "AskUserQuestion", "ToolSearch",
    "EnterPlanMode", "ExitPlanMode", "TodoWrite",
    "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TaskOutput", "ListAgents",
}

WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}
PATH_KEYS = ("file_path", "path", "notebook_path")

# Words that must never appear as a command or argument in Bash.
# Git is handled separately (read-only git is allowed).
BLOCKED_WORDS = [
    "gh", "hub",
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
    (r"\$\(|`", "command substitution"),
    (r"://", "network URL"),
    (r"\bGIT_[A-Z_]+=", "GIT_* environment variables"),
]

# Database deletion and schema destruction. Hard block in shell text and in scripts that are run.
# Ask in content that is written to a file.
DB_DESTRUCTIVE = [
    (r"\bdelete\s+from\b", "SQL DELETE"),
    (r"\bdrop\s+(table|database|schema|index|view|column|collection|user|role)\b", "SQL DROP"),
    (r"\btruncate\b", "TRUNCATE"),
    (r"\balter\s+table\b[^;]*\bdrop\b", "SQL ALTER ... DROP"),
    (r"\bdelete_?(many|one)\b", "ORM bulk or single delete"),
    (r"\bdrop_?(database|collection)\b", "drop database or collection"),
    (r"\bflush(all|db)\b", "Redis flush"),
    (r"\bdetach\s+delete\b", "graph DELETE"),
    (r"\.delete\s*\(", "ORM .delete()"),
    (r"\bsession\.delete\b", "ORM session.delete"),
    (r"\bdestroy_?all\b", "ORM destroy_all"),
]

# File deletion inside scripts (a script must not delete files either).
FILE_DELETE_CODE = [
    (r"\bos\.(remove|unlink|rmdir|removedirs)\b", "file delete (os)"),
    (r"\bshutil\.rmtree\b", "file delete (shutil)"),
    (r"\.unlink\s*\(", "file delete (unlink)"),
    (r"\bfs\.(rm|rmSync|rmdir|rmdirSync|unlink|unlinkSync)\b", "file delete (fs)"),
    (r"\brm\s+-", "file delete (rm)"),
]

SCRIPT_RUNNERS = re.compile(r"^(python|pypy|node|bash|sh|zsh|ruby|perl|php|deno|bun|tsx|ts-node)[\d.]*$")
SCRIPT_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".sh", ".bash", ".rb", ".pl", ".php", ".sql"}
SCAN_SKIP = {os.path.join("tests", "test_guard.py")}
NO_CONTENT_SCAN_EXT = {".md", ".txt", ".rst"}
DB_FILE_EXT = {".db", ".sqlite", ".sqlite3", ".mdb", ".rdb"}

BOUNDARY_BEFORE = r"(?:^|[\s;&|(){}<>=/\"'])"
BOUNDARY_AFTER = r"(?=$|[\s;&|(){}<>\"'])"
WORD_RE = re.compile(
    BOUNDARY_BEFORE + "(" + "|".join(re.escape(w) for w in BLOCKED_WORDS) + ")" + BOUNDARY_AFTER
)
GIT_RE = re.compile(BOUNDARY_BEFORE + "git" + BOUNDARY_AFTER)

# Git: only these read-only subcommands may run. Everything else is for the owner.
GIT_READ = {
    "status", "log", "diff", "show", "blame", "shortlog", "rev-parse", "ls-files",
    "ls-tree", "describe", "grep", "cat-file", "show-ref", "rev-list", "name-rev",
    "diff-tree", "diff-files", "diff-index", "whatchanged",
}
GIT_BAD_OPT_PREFIXES = (
    "--output", "--ext-diff", "--open-files-in-pager", "--exec", "--upload-pack",
    "--receive-pack", "--git-dir", "--work-tree", "--config", "-O",
)
GIT_BRANCH_FLAGS = {
    "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose", "--list", "-l",
    "--show-current", "--no-color", "--color",
}


def git_check(args):
    """Return None if the git call is read-only, else a reason string."""
    if not args:
        return "git needs a subcommand"
    sub, rest = args[0], args[1:]
    for a in rest:
        if a.startswith(GIT_BAD_OPT_PREFIXES):
            return "git option '" + a + "' is not allowed"
    if sub in GIT_READ:
        return None
    if sub == "branch" and all(a in GIT_BRANCH_FLAGS for a in rest):
        return None
    if sub == "tag" and (not rest or rest[0] in ("-l", "--list")):
        return None
    if sub == "remote" and rest in ([], ["-v"], ["--verbose"]):
        return None
    if sub == "stash" and rest[:1] in (["list"], ["show"]):
        return None
    if sub == "config" and rest[:1] in (["--get"], ["--get-all"], ["--list"], ["-l"]):
        return None
    if sub == "reflog" and (not rest or rest[0] == "show" or rest[0].startswith("-")):
        return None
    return ("git '" + sub + "' changes things. Only the owner runs it "
            "(use '!git ...' in the prompt or your own terminal)")


def db_hit(text, include_files=False):
    pats = DB_DESTRUCTIVE + (FILE_DELETE_CODE if include_files else [])
    for pat, label in pats:
        if re.search(pat, text, re.IGNORECASE):
            return label
    return None


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
            r == ".claude" or r.startswith(".claude" + os.sep)
            or r == ".git" or r.startswith(".git" + os.sep)
            or base.startswith(".env")
            or r.startswith("secrets" + os.sep)
            or os.path.splitext(base)[1].lower() in DB_FILE_EXT
        )

    def is_secret(p):
        r = rel(p)
        return os.path.basename(r).startswith(".env") or r.startswith("secrets" + os.sep)

    def script_problem(p):
        r = rel(p)
        if r in SCAN_SKIP or r.startswith(os.path.join(".claude", "hooks")):
            return None
        if os.path.splitext(r)[1].lower() not in SCRIPT_EXT:
            return None
        try:
            full = resolve(p)
            if not os.path.isfile(full):
                return None
            if os.path.getsize(full) > 1000000:
                return "script is too large to check"
            with open(full, "r", errors="ignore") as f:
                text = f.read()
        except OSError:
            return "script could not be read"
        return db_hit(text, include_files=True)

    if tool not in ALLOWED_TOOLS:
        block("tool '" + tool + "' is not on the allowlist")

    # Web tools: the settings file makes the owner approve each call.
    # WebFetch may only reach public https pages.
    if tool == "WebSearch":
        return

    if tool == "WebFetch":
        from urllib.parse import urlsplit
        try:
            u = urlsplit(str(inp.get("url", "")))
            h = (u.hostname or "").lower()
        except ValueError:
            block("bad URL")
            return
        if u.scheme != "https":
            block("only https URLs may be fetched")
        if u.username or u.password:
            block("URL with login details")
        if (h in ("", "localhost", "0.0.0.0", "::1") or h.endswith(".local")
                or re.match(r"^(127\.|10\.|192\.168\.|169\.254\.|172\.(1[6-9]|2\d|3[01])\.)", h)):
            block("local or private address")
        return

    if tool == "Bash":
        cmd = inp.get("command", "")
        if inp.get("dangerouslyDisableSandbox"):
            block("sandbox bypass requested")
        if inp.get("run_in_background"):
            block("background commands are not allowed")
        for pat, why in BLOCKED_PATTERNS:
            if re.search(pat, cmd, re.IGNORECASE):
                block(why)
        hit = db_hit(cmd)
        if hit:
            block("database delete or drop is never allowed (" + hit + ")")
        m = WORD_RE.search(cmd)
        if m:
            block("command '" + m.group(1) + "' is not allowed")
        try:
            lex = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=True)
            lex.whitespace_split = True
            tokens = list(lex)
        except ValueError:
            block("could not parse command")
            return

        # Split into command segments.
        seps = {";", "&&", "||", "|", "&", "(", ")", ";;", "|&"}
        segments, cur = [], []
        for tok in tokens:
            if tok in seps:
                if cur:
                    segments.append(cur)
                cur = []
            else:
                cur.append(tok)
        if cur:
            segments.append(cur)

        git_ok_count = 0
        for seg in segments:
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", seg[0]):
                block("environment assignment before a command")
            head = os.path.basename(seg[0])
            if head == "git":
                why = git_check(seg[1:])
                if why:
                    block(why)
                git_ok_count += 1
            if SCRIPT_RUNNERS.match(head) or seg[0].startswith("./"):
                candidates = seg[1:] if SCRIPT_RUNNERS.match(head) else [seg[0]] + seg[1:]
                for arg in candidates:
                    if arg.startswith("-") or arg in (">", ">>", "<"):
                        continue
                    why = script_problem(arg)
                    if why:
                        block("script '" + arg + "' contains a delete: " + why)
        if len(GIT_RE.findall(cmd)) != git_ok_count:
            block("git must be a plain read-only command (no -C, -c, quotes, or wrappers)")

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
                if re.search(r"(^|/)\.git(/|$)", c):
                    block("shell access to .git (use the Read tool, or ask the owner)")
                if c.startswith("/") and not inside(c):
                    block("path outside project '" + c + "'")
                if os.path.splitext(c)[1].lower() in DB_FILE_EXT:
                    block("database file '" + c + "' must not be touched from the shell")
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

    # Written code that deletes data or files: the owner must approve.
    if tool in WRITE_TOOLS:
        target = inp.get("file_path") or inp.get("notebook_path") or ""
        if os.path.splitext(target)[1].lower() not in NO_CONTENT_SCAN_EXT:
            text = "\n".join(str(inp.get(k, "")) for k in ("content", "new_string", "new_source"))
            hit = db_hit(text, include_files=True)
            if hit:
                ask("this content contains a delete (" + hit + "). Approve only if you are sure")


if __name__ == "__main__":
    main()
    sys.exit(0)
