# Guardrails

Goal: nothing risky runs, even if Auto Mode is on. The owner approves everything.

## Layers

| Layer | Where | What it does |
|---|---|---|
| 1. Instructions | `AGENTS.md` | Tells every AI tool the rules. Not enforced by itself. |
| 2. Deny rules | `.claude/settings.json` | Block git, delete, database, network, install, secrets, MCP, web. Apply in every mode. |
| 3. Hook | `.claude/hooks/guard.py` | Checks every tool call. Tool allowlist, path jail, blocked words. Fails closed. |
| 4. Sandbox | `.claude/settings.json` | OS level. Writes only in the project. No network. No unsandboxed retry. |
| 5. Mode lock | `.claude/settings.json` | Bypass mode and Auto Mode are disabled. Default mode asks first. |
| 6. Self lock | deny rules and hook | Agents cannot edit `.claude/`, `.env*`, or `secrets/`. Only the owner can. |

## What is blocked

| Area | Examples |
|---|---|
| Git (changes) | `add`, `commit`, `push`, `pull`, `fetch`, `merge`, `rebase`, `reset`, `checkout`, `switch`, `restore`, `clean`, `rm`, `stash`, `tag`, `branch` changes, `config` changes, `-C`, `-c`, `GIT_*` env vars. Only the owner runs these. |
| Git (read-only) | Allowed: `status`, `log`, `diff`, `show`, `blame`, `branch` list, `tag` list, `stash list`, `remote -v`, `config --get`. Options like `--output` and `--ext-diff` are blocked. |
| GitHub CLI | any `gh` command |
| Database delete | `DELETE`, `DROP`, `TRUNCATE`, `deleteMany`, `.delete()`, `flushall` in shell text. Scripts that contain these cannot be run. Code that contains them needs owner approval to write. DB files (`.db`, `.sqlite`) cannot be touched. |
| Delete | `rm`, `rmdir`, `unlink`, `trash`, `shred`, `find -delete` |
| Database | `psql`, `mysql`, `sqlite3`, `mongosh`, `redis-cli`, `prisma`, migration tools, destructive SQL text |
| Outside the folder | `..`, `~`, `$HOME`, absolute paths outside the project, any file tool path outside |
| Network | `curl`, `wget`, `ssh`, URLs, WebFetch, WebSearch, MCP tools |
| Installs | `npm`, `pip`, `brew`, `npx`, `cargo`, `gem` install or publish |
| System | `sudo`, `chmod`, `chown`, `kill`, `docker`, `crontab`, `osascript` |
| Tricks | `eval`, `xargs`, `base64`, `$(...)`, backticks, `python -c`, `node -e`, background jobs, sandbox bypass |
| Tools | Anything not on the allowlist in `guard.py` (Artifact, worktree, cron, remote trigger...) |

## Known limits

- No setup is 100 percent safe. The sandbox is the strongest layer. The hook is strict but is text based.
- The hook may block harmless commands that contain a blocked word. That is on purpose.
- Other tools (Codex, Cursor, Antigravity, Gemini) read `AGENTS.md` only. They need their own config for hard blocks.
- Hooks may not fire in bypass mode. Bypass mode is disabled to cover this.

## Change the guardrails

- Only the owner edits `.claude/`. Agents are blocked from it.
- Run the tests after any change: `python3 tests/test_guard.py`
- To allow a new tool or command, edit `ALLOWED_TOOLS` or `BLOCKED_WORDS` in `guard.py`.
