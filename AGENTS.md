# mcp-guard

Guardrails for MCP (Model Context Protocol) usage. Exact design is still to be decided with the owner.

This file is the single source of truth for all AI coding tools (Claude Code, Cursor, Codex, Antigravity, Gemini CLI, and others). Edit rules here only.

## Status

- Early setup. No code yet.
- Stack, language, and architecture: TBD. Do not assume. Ask first.

## Guardrails (strict, apply to every agent and tool)

These rules override everything else. If a task seems to need breaking one, stop and ask the owner.

| # | Rule | Meaning |
|---|---|---|
| 1 | Ask before running anything | Do not run any command, script, or tool without explicit owner permission. |
| 2 | Git is read-only for agents | Allowed: `git status`, `log`, `diff`, `show`, `blame`, and other read-only commands. Never run `add`, `commit`, `push`, `pull`, `fetch`, `merge`, `rebase`, `reset`, `checkout`, `switch`, `restore`, `clean`, `rm`, `stash`, `tag`, `branch` changes, or any other command that changes things. Only the owner runs those. No `-C` or `-c` options. If another agent tries, stop it. |
| 3 | Hands off the database | Never touch the database directly. No queries, no migrations, no seeds, no schema changes. **Never delete, drop, or truncate any data**, in any form: SQL, ORM calls, scripts, or tools. Do not write code that deletes data unless the owner approves that exact code. |
| 4 | No deleting | Never delete files, folders, or any other resource. This includes `rm`, `rmdir`, `unlink`, `trash`, and `find -delete`. |
| 5 | Stay in this folder | Never read, write, or run anything outside this project folder. If you need something outside, ask the owner. |
| 6 | Plan first, then act | Do not create or change any file, config, or resource until the owner has explained the plan and said to proceed. Until then, only talk and ask. |

Also blocked: network access, installing packages, MCP and web tools, and editing `.claude/`, `.env*`, or `secrets/`. Details are in `docs/guardrails.md`.

Enforcement lives in `.claude/settings.json` and `.claude/hooks/guard.py`. Do not try to edit, disable, or work around them.

How to follow these:

- When unsure, ask. Asking is always allowed.
- Do not try to bypass a block with another tool, shell trick, or script.
- Permission for one action does not carry over to the next one.
- Report what you did after each approved step.

## Working rules

- All files in this repo must be in English.
- Keep code and docs simple and short. No over-engineering.
- Do not add dependencies without asking.
- Never commit secrets. Use `.env` files and keep them out of git.
- Do not create files or folders outside the agreed structure.
- Ask before any destructive or outward-facing action (delete, push, publish).

## Style for docs and comments

- Simple words. Short sentences.
- Use bullets and tables over long paragraphs.
- No em-dashes. Use a comma or a period.

## Commands

- Build, test, lint: TBD. Fill this in once the stack is chosen.

## Layout

| Path | Purpose |
|---|---|
| `AGENTS.md` | Shared rules for every AI tool (this file) |
| `CLAUDE.md` | Thin pointer to `AGENTS.md` for Claude Code |
| `GEMINI.md` | Thin pointer to `AGENTS.md` for Gemini CLI |
| `README.md` | Project overview for humans |
| `.claude/settings.json` | Claude Code permissions, sandbox, hook config (owner edits only) |
| `.claude/hooks/guard.py` | Hook that checks every tool call (owner edits only) |
| `tests/test_guard.py` | Tests for the hook. Run `python3 tests/test_guard.py` |
| `docs/guardrails.md` | What is blocked and why |
| `docs/proposed/` | Changes to `.claude/` waiting for the owner to apply |
| `docs/` | Design notes and decisions |

## Keeping tools in sync

- Add or change rules in `AGENTS.md` only.
- Do not copy rules into `CLAUDE.md` or `GEMINI.md`. They must only import this file.
- If a tool needs its own config file, keep it minimal and point back here.
