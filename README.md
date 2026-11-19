# mcp-guard

mcp-guard stops every MCP tool call from Claude Desktop (and from Claude Web, through a gateway) and asks you first. A popup shows which server and tool Claude wants to use, with the arguments. You click **Allow** or **Block**. Nothing reaches the MCP server until you decide.

macOS only. Python 3.11 or newer (tested on 3.14).

## Contents

1. [How it works](#how-it-works)
2. [What is covered](#what-is-covered)
3. [Install](#install)
4. [Daily use](#daily-use)
5. [Configuration](#configuration)
6. [Claude Web (gateway)](#claude-web-gateway)
7. [Commands](#commands)
8. [Security](#security)
9. [Troubleshooting](#troubleshooting)
10. [Uninstall](#uninstall)
11. [Development](#development)

## How it works

```
Claude Desktop --stdio--> mcp-guard proxy ----+
                          (one per server)    |   Unix socket   +--------------------+
                                              +---------------> | mcp-guard daemon   |
claude.ai --> Anthropic cloud --> tunnel -->  |                 |  queue, timers     |
              mcp-guard gateway (HTTP) -------+                 |  popup, audit log  |
                     |                                          +--------------------+
                     v
              real MCP servers
```

| Part | What it does |
|---|---|
| Proxy | Claude Desktop starts it in place of each MCP server. It starts the real server, passes messages both ways, and holds every `tools/call` until the daemon answers |
| Daemon | Runs in the background (menu bar icon, no Dock icon). Shows one popup at a time, queues the rest, blocks after 45 seconds without an answer, writes the audit log |
| Gateway | Optional. Serves local MCP servers over HTTP so Claude Web can reach them through a tunnel, with the same popup |
| Installer | Wraps the servers in Claude Desktop's config, and starts the daemon at login |

The popup:

- Shows where the request came from, the server, the tool, and the arguments (plain text only).
- Shows a countdown. No answer in 45 seconds means Block.
- **Block** is the default button. For the first second the buttons do nothing, so a stray click or key press cannot approve a request.
- **Esc** or closing the window means Block.
- If Claude cancels the request (for example you press Stop), the popup closes by itself.

Everything fails closed. If the daemon is not running, the config is broken, or anything goes wrong, the request is blocked and Claude gets a clear reason.

## What is covered

| MCP setup | Covered? | Notes |
|---|---|---|
| Claude Desktop, local servers in `claude_desktop_config.json` | **Yes** | Tested end to end: allow, block, timeout, cancel, many calls at once, full screen |
| Claude Desktop or Claude Web, custom connector to a server **you** run | **Partly** | Gateway works with the official MCP client through Cloudflare Tunnel, popup included. Adding it as a claude.ai custom connector fails today with `mcp_auth_start_failed`. See [docs/claude-web-findings.md](docs/claude-web-findings.md) |
| Claude Desktop extensions (`.mcpb`) | No | Desktop manages their config itself |
| Directory connectors (Notion, Figma, and others) | No, never | Anthropic's cloud calls the vendor directly. Nothing on your Mac sees the call |

Why Claude Web needs a gateway: Claude Web (and remote connectors in Desktop) call MCP servers from Anthropic's cloud, not from your Mac. So a local app can only guard servers that you expose yourself, through mcp-guard.

## Install

```
git clone <this repo> mcp-guard
cd mcp-guard
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Then put your Claude Desktop servers behind mcp-guard and turn on autostart:

```
mcp-guard install --dry-run     # see what will change
mcp-guard install               # do it
```

`install`:

- Backs up `~/Library/Application Support/Claude/claude_desktop_config.json` (timestamped copy), then wraps each local server as `mcp-guard proxy --name <name> -- <original command>`. Other settings are not touched.
- Adds a login item (`~/Library/LaunchAgents/local.mcp-guard.daemon.plist`) so the daemon starts at login and restarts after a crash.

Finally quit Claude Desktop (Cmd+Q) and open it again. Check everything with:

```
mcp-guard status
```

You should see `Daemon: running`, `Autostart: on`, and each server marked `protected`.

## Daily use

Use Claude Desktop as usual. When Claude wants to run a tool, the popup appears on top of every window. Read it, wait a moment, and click **Allow** or **Block**.

- Several calls at once: the popup shows the oldest one and "N more waiting".
- Claude Desktop gives up on a tool call after about 60 seconds (measured), and that time includes the popup. So answer within 45 seconds, and leave some time for the tool to run.
- Quit mcp-guard from the menu bar icon (blue **G**) to block everything until you start it again (or until next login).

## Configuration

Optional file: `~/Library/Application Support/mcp-guard/config.toml`. Without it, defaults apply.

```toml
timeout_seconds = 45            # 1 to 55
gated_methods = ["resources/read"]   # extra methods; tools/call is always gated
log_arguments = "truncated"     # "none", "truncated", or "full"
audit_log = "~/Library/Logs/mcp-guard/audit.jsonl"
```

| Rule | Detail |
|---|---|
| `tools/call` | Always needs approval. Config can add methods, never remove this one |
| Broken file | Unknown keys and bad values are errors. The daemon and proxies then refuse to start, so nothing runs unchecked |
| Restart | Restart the daemon after a change: `launchctl kickstart -k gui/$(id -u)/local.mcp-guard.daemon` |

### Audit log

One JSON line per decision in `~/Library/Logs/mcp-guard/audit.jsonl` (mode 600):

```json
{"time": "2026-11-19T08:57:19.197+00:00", "source": "desktop", "server": "files", "method": "tools/call", "tool": "echo", "decision": "allow", "reason": "Allowed by the user.", "decided_in_ms": 1971, "arguments": {"text": "hello"}}
```

`decision` is `allow`, `block`, or `cancelled`. Values under keys like `token`, `password`, `secret`, `key`, or `authorization` are replaced with `***`. Old entries are never removed.

## Claude Web (gateway)

Status: works with the official MCP client through a tunnel, with the popup. claude.ai's own connector does not connect yet. Read [docs/claude-web-findings.md](docs/claude-web-findings.md) before relying on it.

1. Add a token and your servers to `config.toml`:

   ```toml
   [gateway]
   port = 8787
   token = "<at least 32 random characters>"   # run `mcp-guard gateway` once to get one
   # allowed_ips defaults to Anthropic's outbound ranges: 160.79.104.0/21, 153.61.208.0/20

   [gateway.servers.files]
   command = "npx"
   args = ["-y", "@modelcontextprotocol/server-filesystem", "/Users/you/Documents"]
   ```

2. Start the gateway: `mcp-guard gateway`. It listens only on `127.0.0.1` and prints the local URL of each server.
3. Make it public with a tunnel: `brew install cloudflared`, then `cloudflared tunnel --url http://127.0.0.1:8787`.
4. The URL for a client is `https://<tunnel host>/<token>/<server>/mcp`. Keep it secret: the token is the password.

| Gateway check | Rule |
|---|---|
| Token | Must match (constant-time compare). Wrong token: 404 |
| Origin | If a browser sends one, it must be `https://claude.ai` or `https://claude.com`. Else 403 |
| Client IP | Must be in `allowed_ips`. Behind the tunnel it is read from `CF-Connecting-IP`. Else 403 |
| Sessions | Each MCP session gets its own process of the real server. Idle sessions close after 30 minutes. Max 10 per server |
| Answers | SSE when the client accepts it, else JSON (`response_mode = "auto"`). `GET` gets 405 |

The IP check alone is not real security, because every Anthropic customer shares those IPs. The token is the secret. OAuth is future work.

## Commands

| Command | What it does |
|---|---|
| `mcp-guard status` | Daemon, autostart, config, audit log, gateway, and which Desktop servers are protected |
| `mcp-guard install [--dry-run] [--no-autostart]` | Wrap Desktop servers, turn on autostart |
| `mcp-guard uninstall [--dry-run] [--no-autostart]` | Undo install |
| `mcp-guard daemon` | Run the daemon by hand (autostart normally does this) |
| `mcp-guard proxy --name NAME -- CMD ARGS...` | What Claude Desktop runs for each server |
| `mcp-guard gateway [--log-level debug]` | Run the HTTP gateway for Claude Web |

All commands take `--config PATH`. `daemon`, `proxy`, `gateway`, and `status` take `--socket PATH`.

## Security

| Topic | How |
|---|---|
| Fail closed | No daemon, broken config, timeout, crash, or any error: Block |
| Local socket | `~/Library/Application Support/mcp-guard/daemon.sock`, mode 600, in a mode 700 folder. One daemon only |
| Popup | Plain text only, never HTML. Block is default. One second input delay. Esc and close mean Block |
| Limits | 50 waiting requests at most; messages up to 16 MB (proxy) and 4 MB (gateway); large arguments shown as a preview |
| Logs | The gateway never logs its URL path (it holds the token). The audit log masks secret-looking keys |
| Trust model | Programs running as you on your Mac are trusted. They could talk to the socket or fake a header on the local gateway port. People on the internet cannot: Cloudflare sets `CF-Connecting-IP` itself, and the token is still needed |
| Installer | Always backs up before writing, writes atomically, never deletes the Desktop config. Uninstall removes only the login item it made |

## Troubleshooting

| Symptom | Check |
|---|---|
| Every tool call says "the mcp-guard daemon is not running" | `mcp-guard status`. Start it: `launchctl kickstart -k gui/$(id -u)/local.mcp-guard.daemon` |
| A server shows as failed in Claude Desktop | `tail ~/Library/Logs/Claude/mcp-server-<name>.log`. A broken `config.toml` stops the proxy on purpose |
| No popup appears | Look for the blue **G** in the menu bar. Daemon log: `~/Library/Logs/mcp-guard/daemon.log` |
| Tool calls time out in Claude | Desktop gives up after about 60 seconds, popup included. Answer sooner, or lower `timeout_seconds` |
| Gateway answers 403 | The body names the client IP the gateway saw. Compare it with `allowed_ips` (printed when the gateway starts) |

## Uninstall

```
mcp-guard uninstall
```

Then quit and reopen Claude Desktop. Backups of the Desktop config stay next to it (`claude_desktop_config.backup-*.json`). Remove the `.venv` folder if you like.

## Development

```
pip install -e ".[dev]"
python -m pytest -q          # all tests (some need a normal terminal: Unix sockets and local ports)
python3 tests/test_guard.py  # tests for this repo's AI-agent guardrails
```

| Path | What |
|---|---|
| `src/mcp_guard/` | The package: `jsonrpc`, `policy`, `approval/`, `proxy/`, `daemon/`, `gateway/`, `install/`, `audit`, `config`, `cli` |
| `tests/` | Unit, integration, UI (Qt offscreen), and network tests. `tests/fakes/fake_mcp_server.py` is a small MCP server for tests |
| `scripts/` | Owner-run helpers: add a test server to Claude Desktop, write a gateway config, check a URL with the official MCP client |
| `docs/` | `requirements.md`, `design.md`, `claude-web-findings.md`, `research/`, `guardrails.md` |
| `AGENTS.md` | Rules for AI coding tools working on this repo |
