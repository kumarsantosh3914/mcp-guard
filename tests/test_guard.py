#!/usr/bin/env python3
"""Tests for the guard hook.

Run: python3 tests/test_guard.py
Test another copy of the hook: GUARD_PATH=docs/proposed/guard.py python3 tests/test_guard.py
Results: 0 = allow, 2 = block, "ask" = owner must approve.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
GUARD = os.path.join(ROOT, os.environ.get("GUARD_PATH", os.path.join(".claude", "hooks", "guard.py")))

# Built in pieces so this file does not contain the raw patterns.
SQL_DEL = "DELETE " + "FROM users"
SQL_DROP = "DROP " + "TABLE users"
SQL_TRUNC = "TRUNC" + "ATE users"


def run(tool, tool_input):
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input, "cwd": ROOT})
    env = dict(os.environ, CLAUDE_PROJECT_DIR=ROOT)
    p = subprocess.run([sys.executable, GUARD], input=payload, text=True,
                       capture_output=True, env=env)
    if p.returncode == 0 and '"permissionDecision": "ask"' in p.stdout:
        return "ask"
    return p.returncode


def bash(cmd):
    return run("Bash", {"command": cmd})


def path(*parts):
    return os.path.join(ROOT, *parts)


# (description, expected result, call)
CASES = [
    # basics
    ("ls allowed", 0, lambda: bash("ls -la")),
    ("relative path allowed", 0, lambda: bash("cat README.md")),
    ("python script allowed", 0, lambda: bash("python3 tests/fixtures/safe_script.py")),

    # git: read-only allowed
    ("git status allowed", 0, lambda: bash("git status")),
    ("git status -s allowed", 0, lambda: bash("git status -s")),
    ("git log allowed", 0, lambda: bash("git log --oneline -n 5")),
    ("git diff allowed", 0, lambda: bash("git diff HEAD~1")),
    ("git show allowed", 0, lambda: bash("git show HEAD")),
    ("git branch list allowed", 0, lambda: bash("git branch -a")),
    ("git tag list allowed", 0, lambda: bash("git tag")),
    ("git stash list allowed", 0, lambda: bash("git stash list")),
    ("git remote -v allowed", 0, lambda: bash("git remote -v")),
    ("git config --get allowed", 0, lambda: bash("git config --get user.name")),
    ("git log piped allowed", 0, lambda: bash("git log | head")),
    ("git status && ls allowed", 0, lambda: bash("git status && ls")),

    # git: everything that changes things is blocked
    ("git add blocked", 2, lambda: bash("git add .")),
    ("git commit blocked", 2, lambda: bash("git commit -m x")),
    ("git push blocked", 2, lambda: bash("git push origin main")),
    ("git pull blocked", 2, lambda: bash("git pull")),
    ("git fetch blocked", 2, lambda: bash("git fetch")),
    ("git merge blocked", 2, lambda: bash("git merge dev")),
    ("git rebase blocked", 2, lambda: bash("git rebase main")),
    ("git reset blocked", 2, lambda: bash("git reset --hard")),
    ("git checkout blocked", 2, lambda: bash("git checkout main")),
    ("git switch blocked", 2, lambda: bash("git switch main")),
    ("git restore blocked", 2, lambda: bash("git restore .")),
    ("git clean blocked", 2, lambda: bash("git clean -fd")),
    ("git rm blocked", 2, lambda: bash("git rm file")),
    ("git stash blocked", 2, lambda: bash("git stash")),
    ("git stash drop blocked", 2, lambda: bash("git stash drop")),
    ("git tag create blocked", 2, lambda: bash("git tag v1")),
    ("git branch delete blocked", 2, lambda: bash("git branch -D x")),
    ("git branch create blocked", 2, lambda: bash("git branch newone")),
    ("git config set blocked", 2, lambda: bash("git config user.name x")),
    ("git remote add blocked", 2, lambda: bash("git remote add o u")),
    ("git init blocked", 2, lambda: bash("git init")),
    ("git status then push blocked", 2, lambda: bash("git status && git push")),
    ("git status; commit blocked", 2, lambda: bash("git status; git commit")),
    ("git newline commit blocked", 2, lambda: bash("git status\ngit commit")),
    ("git -C blocked", 2, lambda: bash("git -C sub status")),
    ("git -c blocked", 2, lambda: bash("git -c core.pager=x status")),
    ("git --output blocked", 2, lambda: bash("git log --output=out.txt")),
    ("git --ext-diff blocked", 2, lambda: bash("git diff --ext-diff")),
    ("GIT_ env blocked", 2, lambda: bash("GIT_SSH_COMMAND=x git status")),
    ("full path git outside blocked", 2, lambda: bash("/usr/bin/git status")),
    ("git hidden in echo blocked", 2, lambda: bash("echo git")),
    ("git in sh -c blocked", 2, lambda: bash("sh -c 'git push'")),
    ("gh blocked", 2, lambda: bash("gh pr create")),
    ("shell access to .git blocked", 2, lambda: bash("cat .git/config")),
    ("edit .git blocked", 2, lambda: run("Write", {"file_path": path(".git", "config"), "content": "x"})),

    # delete files and dangerous commands
    ("rm blocked", 2, lambda: bash("rm file.txt")),
    ("find -delete blocked", 2, lambda: bash("find . -name x -delete")),
    ("curl blocked", 2, lambda: bash("curl example.com")),
    ("npm install blocked", 2, lambda: bash("npm install left-pad")),
    ("pip install blocked", 2, lambda: bash("pip install requests")),
    ("inline python blocked", 2, lambda: bash("python3 -c 'import os'")),
    ("command substitution blocked", 2, lambda: bash("echo $(whoami)")),

    # database: nothing may be deleted
    ("psql blocked", 2, lambda: bash("psql -c 'select 1'")),
    ("mysql blocked", 2, lambda: bash("mysql -e 'select 1'")),
    ("sqlite3 blocked", 2, lambda: bash("sqlite3 app.db")),
    ("redis-cli blocked", 2, lambda: bash("redis-cli ping")),
    ("prisma blocked", 2, lambda: bash("prisma migrate reset")),
    ("sql delete text blocked", 2, lambda: bash("echo '" + SQL_DEL + "'")),
    ("sql drop text blocked", 2, lambda: bash("echo '" + SQL_DROP + "'")),
    ("sql truncate text blocked", 2, lambda: bash("echo '" + SQL_TRUNC + "'")),
    ("deleteMany text blocked", 2, lambda: bash("echo prisma.user.deleteMany")),
    ("flushall text blocked", 2, lambda: bash("echo FLUSHALL")),
    ("script with db delete blocked", 2, lambda: bash("python3 tests/fixtures/db_delete_script.py")),
    ("db file in shell blocked", 2, lambda: bash("cp data.sqlite3 backup")),
    ("write db file blocked", 2, lambda: run("Write", {"file_path": path("app.db"), "content": "x"})),
    ("write code with db delete asks", "ask", lambda: run("Write", {"file_path": path("docs", "a.py"), "content": 'run("' + SQL_DEL + '")'})),
    ("edit code with db drop asks", "ask", lambda: run("Edit", {"file_path": path("docs", "a.py"), "new_string": SQL_DROP})),
    ("write code with os.remove asks", "ask", lambda: run("Write", {"file_path": path("docs", "a.py"), "content": "import os\nos.remove('x')"})),
    ("write markdown about delete allowed", 0, lambda: run("Write", {"file_path": path("docs", "a.md"), "content": SQL_DEL})),
    ("write normal code allowed", 0, lambda: run("Write", {"file_path": path("docs", "a.py"), "content": "print(1)"})),

    # folder jail
    ("cd .. blocked", 2, lambda: bash("cd .. && ls")),
    ("home path blocked", 2, lambda: bash("ls ~/Documents")),
    ("env var path blocked", 2, lambda: bash("ls $HOME")),
    ("outside absolute path blocked", 2, lambda: bash("cat /etc/passwd")),
    ("redirect outside blocked", 2, lambda: bash("echo hi > /tmp/x")),
    ("/dev/null allowed", 0, lambda: bash("ls > /dev/null")),
    ("sandbox bypass blocked", 2, lambda: run("Bash", {"command": "ls", "dangerouslyDisableSandbox": True})),
    ("background blocked", 2, lambda: run("Bash", {"command": "ls", "run_in_background": True})),

    # file tools
    ("read inside allowed", 0, lambda: run("Read", {"file_path": path("README.md")})),
    ("read outside blocked", 2, lambda: run("Read", {"file_path": "/etc/hosts"})),
    ("read .env blocked", 2, lambda: run("Read", {"file_path": path(".env")})),
    ("write inside allowed", 0, lambda: run("Write", {"file_path": path("docs", "a.md"), "content": "x"})),
    ("write settings blocked", 2, lambda: run("Write", {"file_path": path(".claude", "settings.json"), "content": "x"})),
    ("edit hook blocked", 2, lambda: run("Edit", {"file_path": path(".claude", "hooks", "guard.py"), "new_string": "x"})),
    ("glob outside blocked", 2, lambda: run("Glob", {"pattern": "/etc/*"})),
    ("grep path outside blocked", 2, lambda: run("Grep", {"pattern": "x", "path": "/etc"})),

    # tools
    ("mcp tool blocked", 2, lambda: run("mcp__server__tool", {})),
    ("WebSearch allowed", 0, lambda: run("WebSearch", {"query": "mcp spec"})),
    ("WebFetch https allowed", 0, lambda: run("WebFetch", {"url": "https://example.com/docs"})),
    ("WebFetch http blocked", 2, lambda: run("WebFetch", {"url": "http://example.com"})),
    ("WebFetch file blocked", 2, lambda: run("WebFetch", {"url": "file:///etc/passwd"})),
    ("WebFetch localhost blocked", 2, lambda: run("WebFetch", {"url": "https://localhost:8080"})),
    ("WebFetch 127.x blocked", 2, lambda: run("WebFetch", {"url": "https://127.0.0.1/"})),
    ("WebFetch private ip blocked", 2, lambda: run("WebFetch", {"url": "https://192.168.1.5/"})),
    ("WebFetch metadata ip blocked", 2, lambda: run("WebFetch", {"url": "https://169.254.169.254/"})),
    ("WebFetch ipv6 loopback blocked", 2, lambda: run("WebFetch", {"url": "https://[::1]/"})),
    ("WebFetch .local blocked", 2, lambda: run("WebFetch", {"url": "https://printer.local/"})),
    ("WebFetch login in url blocked", 2, lambda: run("WebFetch", {"url": "https://user:pw@example.com/"})),
    ("Artifact blocked", 2, lambda: run("Artifact", {})),
    ("EnterWorktree blocked", 2, lambda: run("EnterWorktree", {})),
]


def main():
    bad = 0
    for name, want, fn in CASES:
        got = fn()
        ok = got == want
        bad += 0 if ok else 1
        print(("PASS " if ok else "FAIL ") + name + " (want " + str(want) + ", got " + str(got) + ")")
    print("\n" + str(len(CASES) - bad) + "/" + str(len(CASES)) + " passed (guard: " + GUARD + ")")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
