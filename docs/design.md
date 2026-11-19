# Design

| Field | Value |
|---|---|
| Status | Built. Matches the code as of 2026-11-19. Claude Web connector issue: `docs/claude-web-findings.md` |
| Platform | macOS only |
| Language | Python 3.11 or newer |
| Inputs | `docs/requirements.md`, `docs/research/claude-web-mcp.md` |

## 1. Goal in one line

Every MCP request from Claude Desktop or Claude Web stops at mcp-guard, shows a popup on the Mac, and only goes on to the real MCP server if the user clicks Allow.

## 2. What can and cannot be covered

| MCP setup | How the call travels | Covered? | How |
|---|---|---|---|
| Claude Desktop, local server in `claude_desktop_config.json` | Desktop starts the server and talks over stdio | Yes | stdio proxy |
| Claude Desktop or Claude Web, custom connector that points to a server the user runs | Anthropic's cloud calls a public HTTPS URL | Partly | HTTP gateway plus tunnel. Works with the official MCP client. claude.ai's connector fails with `mcp_auth_start_failed` (open issue) |
| Claude Desktop extension (`.mcpb`) | Desktop starts it over stdio, config is managed by Desktop | No, first version | Known limit |
| Directory connector (Notion, Figma, and others) | Anthropic's cloud calls the vendor directly | No, never | Nothing on the Mac sees the call |

The README must state these limits clearly.

## 3. Architecture

```
                     +-----------------------------+
Claude Desktop ----> | stdio proxy (one per server)|---+
  (stdio)            +-----------------------------+   |        +---------------------+
                                                       +------> | daemon              |
Claude Web /         +-----------------------------+   |  Unix  |  - approval queue   |
remote connector --> | HTTP gateway (one process)  |---+ socket |  - popup (PySide6)  |
  Anthropic cloud    +-----------------------------+            |  - audit log        |
  -> Cloudflare           |                                     |  - menu bar icon    |
     Tunnel               |                                     +---------------------+
                          v
                 real MCP servers (stdio child processes or upstream HTTP)
```

| Component | Runs as | Job |
|---|---|---|
| stdio proxy | Child process of Claude Desktop, one per MCP server | Starts the real server, relays JSON-RPC both ways, holds gated requests until the daemon decides |
| HTTP gateway | One background process | Serves MCP over Streamable HTTP on `127.0.0.1`, checks token and IP, relays to real servers, asks the daemon for gated requests |
| Daemon | One background process with a menu bar icon, started by launchd at login | Receives approval requests, shows popups, applies timeouts, writes the audit log |
| Installer CLI | Run by the user | Wraps Desktop config entries, installs the launchd agent, undoes both |

Why one daemon and many proxies: Claude Desktop starts each server as its own process, so each needs its own proxy. Popups must come from one place so they can be queued and never fight for the screen.

## 4. Request flow

### 4.1 Gated request (default: `tools/call`)

| Step | What happens |
|---|---|
| 1 | Claude sends `tools/call` to the proxy or gateway |
| 2 | The proxy sends an approval request to the daemon: source, server, tool, arguments, a unique id |
| 3 | The daemon queues it and shows the popup. The 45 second timer starts when the request arrives, not when it is shown. Claude Desktop gives up after about 60 seconds (measured), and that budget covers both the popup and the tool. 45 seconds leaves at least 15 seconds for the tool to run |
| 4a | Allow: the proxy forwards the original message to the real server and relays the answer back |
| 4b | Block, timeout, or any error: the proxy does not forward. It sends a denial back to Claude |
| 5 | The daemon writes one audit log line with the outcome |

### 4.2 Everything else

`initialize`, `tools/list`, `ping`, notifications, and server-to-client requests pass through unchanged. Methods that need approval are set in config.

The proxy forwards the original bytes of each message. It never re-encodes them, so the real server gets exactly what Claude sent.

### 4.4 Deciders

The proxy asks a "decider" for each gated request, chosen with `--decider`:

| Decider | Answer | Use |
|---|---|---|
| `daemon` (default) | Asks the daemon, which shows the popup | Normal use |
| `block` | Always Block | Development |
| `allow` | Always Allow | Tests and development only |
| `wait` | Never answers | Tests for timeout and cancel |

A decider that fails, hangs, or gives a bad answer always means Block. If the daemon is not running, the request is blocked at once with "the mcp-guard daemon is not running".

### 4.5 Proxy to daemon protocol

| Rule | Detail |
|---|---|
| Transport | Unix socket at `~/Library/Application Support/mcp-guard/daemon.sock` (override with `MCP_GUARD_SOCKET` or `--socket`) |
| One connection per request | The proxy connects, sends one `request` line, and waits for one `verdict` line |
| Cancel | The proxy closes the connection. The daemon drops the request and the popup moves on |
| Daemon closes without a verdict | The proxy blocks the request |
| Format | One JSON object per line, with `"v": 1` and `"type"`. Unknown versions are rejected |
| Size | Max 1 MB per message. Arguments over 64 KB are sent to the popup as a short preview. The real request still goes to the server unchanged |
| Threads in the daemon | Qt runs in the main thread. The socket server and queue run in an asyncio thread. Queue changes reach Qt through a Qt signal. Clicks reach the queue through `call_soon_threadsafe` |

### 4.6 HTTP gateway (Claude Web and remote connectors)

URL for each server: `https://<tunnel host>/<token>/<server>/mcp`. Locally the gateway listens only on `127.0.0.1:8787`.

| Rule | Detail |
|---|---|
| Transport | MCP Streamable HTTP. `POST` carries one JSON-RPC message. Requests get an answer (200): one SSE event when the client accepts `text/event-stream`, else JSON (`response_mode`: `auto`, `json`, `sse`). Notifications and responses get 202 |
| Stateless mode (experiment) | `stateless = true`: no `Mcp-Session-Id`, one shared server process per server, `initialize` answered once and reused. Limit: two clients that use the same request id at the same time clash. Off by default |
| `GET` | 405. No server-to-client stream in this version, which the spec allows. Server-to-client notifications and requests are dropped |
| Sessions | `initialize` starts a new child process of the real server and returns `Mcp-Session-Id`. Every later message must send it. `DELETE` ends it. Idle sessions close after 30 minutes. Max 10 sessions per server |
| Checks, in order | 1. Token in the path, constant-time compare (wrong token: 404). 2. `Origin` header, if any, must be `https://claude.ai` or `https://claude.com` (else 403). 3. Client IP must be in `allowed_ips` (else 403) |
| Client IP | Behind Cloudflare Tunnel every request comes from 127.0.0.1, so the IP is read from `CF-Connecting-IP`, but only when the peer is local |
| Gating | Same policy and same daemon popup as the stdio proxy. The popup says "Claude Web / remote connector" |
| Limits | Body max 4 MB (413). Batches rejected (400). Upstream answer max 120 s |
| Cancel | `notifications/cancelled` for a request waiting for approval closes its popup. If the HTTP client goes away, the popup closes too and the server gets a cancel |
| Logs | No access log, because the path holds the token |

### 4.7 Autostart

`mcp-guard install` writes `~/Library/LaunchAgents/local.mcp-guard.daemon.plist` and loads it with `launchctl`. The daemon then starts at login, in the GUI session only. launchd restarts it after a crash, but not after Quit from the menu bar. If a daemon already runs, a second one exits cleanly (code 0), so launchd does not loop. `mcp-guard uninstall` unloads the agent and removes the plist, but only if its Label is ours.

### 4.3 Denial response

| Request | Response to Claude |
|---|---|
| `tools/call` | A normal result with `isError: true` and the text "Blocked by the user via mcp-guard." The model reads this and can explain it to the user |
| Other gated methods | JSON-RPC error, code `-32003`, message "Request blocked by the user via mcp-guard" |

## 5. Edge cases

| Case | Behavior |
|---|---|
| Daemon not running | Block at once. Message says the daemon is not running |
| User does not answer | Block after 45 seconds |
| Many requests at once | Daemon queues them. The popup shows the oldest one and "N more waiting". Each keeps its own timer |
| Claude cancels (`notifications/cancelled`) | Proxy tells the daemon. The popup for that request closes. Nothing is forwarded |
| Same JSON-RPC id from two servers | No clash. The daemon uses its own unique id per request |
| Real server crashes | Proxy answers all pending requests with an error, then exits with the same code |
| Proxy is killed | Daemon drops its pending requests when the socket closes |
| Daemon crashes while a request waits | Proxy sees the socket close and blocks the request |
| Bad JSON from Claude | Log it. Do not forward. Answer with a JSON-RPC parse error (id `null`) |
| Bad JSON from the server (often logs printed to stdout by mistake) | Log it. Drop it. Claude never sees it |
| Valid JSON but not a valid JSON-RPC message from Claude | Do not forward. Answer with "Invalid request" |
| Batch that contains a gated request | Reject the whole batch: each request in it gets a "blocked" error. Batches were removed from MCP in 2025-06-18, so this should be rare |
| Batch with no gated request | Forward unchanged |
| Second request with an id that is still pending | Reject the second one with "Invalid request" |
| Message larger than 16 MB | Drop it and log it. Answer Claude with an error if it came from Claude |
| Decider hangs or the daemon is slow | The proxy has its own 45 second timer as a second safety net, then blocks |
| Real server cannot start | Proxy exits with code 127. Claude Desktop shows the server as failed |
| Proxy gets SIGTERM or SIGINT | Stop the real server. Answer open requests with an error. Exit |
| Popup steals focus while the user types | Buttons ignore input for the first second. Enter does not mean Allow. Default focus is Block |
| Very large arguments | Popup shows a scrollable, truncated view. The full value is still forwarded if allowed |

## 6. Security

| Topic | Rule |
|---|---|
| Fail closed | Any error, timeout, or missing daemon means Block |
| Local socket | Unix socket in a folder only the user can read (mode `0700`, socket `0600`) |
| Gateway bind | Only `127.0.0.1`. The tunnel is the only way in from outside |
| Gateway auth | Long random token in the URL path, compared in constant time. IP must be in Anthropic's outbound ranges (`160.79.104.0/21`, `153.61.208.0/20`), read from the tunnel's client IP header |
| Why both | Every Anthropic customer shares those IPs, so the IP check alone is not enough. The token is the real secret |
| OAuth | Future work. Documented in the README |
| Limits | Max body size, max pending requests per source, request read timeout |
| Popup content | Plain text only. Never render HTML or links from arguments |
| Popup input | Block is the default button. Buttons ignore input for the first second of each new request. Esc or closing the window means Block |
| One daemon | A live socket means a daemon already runs, so a second one exits. A leftover socket from a crash is replaced. Any other file at that path is never touched |
| Too many requests | More than 50 waiting requests: new ones are blocked at once |
| Audit log | JSON lines, mode `0600`. Arguments are truncated and common secret keys (`token`, `password`, `key`, `secret`) are masked |
| Config edits | Installer always makes a timestamped backup first and never deletes files. Uninstall restores from the backup |
| Token passthrough | The gateway never forwards Claude's auth headers to upstream servers |
| Trust model | Programs on the user's own Mac are trusted. They could reach the daemon socket only as the same user, and could send a fake `CF-Connecting-IP` header to the local gateway port. People on the internet cannot: Cloudflare sets `CF-Connecting-IP` itself, and the token is still needed |
| Dock icon | The daemon makes itself a macOS background app through the Objective-C runtime (`ctypes`, no extra package). Set `MCP_GUARD_SHOW_DOCK_ICON=1` to keep the icon for debugging |

## 7. Configuration

File: `~/Library/Application Support/mcp-guard/config.toml`

| Key | Default | Meaning |
|---|---|---|
| `timeout_seconds` | `45` | Time before an unanswered request is blocked. Must be 1 to 55, because Claude Desktop gives up at about 60 |
| `gated_methods` | `["tools/call"]` | Extra methods that need approval. `tools/call` is always gated and cannot be removed |
| `log_arguments` | `"truncated"` | `"none"`, `"truncated"`, or `"full"`. Secret-looking keys are masked in every mode |
| `audit_log` | `~/Library/Logs/mcp-guard/audit.jsonl` | Where decisions are logged |
| `gateway.port` | `8787` | Local port. The tunnel points here |
| `gateway.token` | generated at install | Secret path token |
| `gateway.allowed_ips` | Anthropic outbound ranges | IP allowlist |
| `[gateway.servers.<name>]` | none | Servers exposed through the gateway: a stdio command or an upstream URL |

Rules:

- A missing file means defaults.
- A broken file, an unknown key, or a bad value is an error. The daemon and the proxies refuse to start, so nothing can run unchecked (fail closed).
- Command line flags win over the file.
- The `[gateway]` table is reserved for the HTTP gateway.

### 7.1 Audit log

| Point | Rule |
|---|---|
| Writer | Only the daemon, so lines never mix |
| Entry | `time` (UTC), `source`, `server`, `method`, `tool`, `decision` (`allow`, `block`, or `cancelled`), `reason`, `decided_in_ms`, `arguments` |
| Masking | Values under keys that contain `token`, `password`, `secret`, `key`, `authorization`, `cookie`, `credential`, `session`, or `private` become `***` |
| Large arguments | Cut to 2000 characters after masking. A display preview from the proxy is never logged, because its text cannot be masked |
| Access | File mode 600, folder mode 700 |
| Old entries | Never removed or overwritten. No rotation in this version |
| Not in the log | Requests blocked because the daemon was not running. The proxy writes those to Claude Desktop's server log |

## 8. Install and run (user view)

| Step | Command |
|---|---|
| Install package | `pip install .` (in a virtual env) |
| Set up | `mcp-guard install` wraps Desktop servers and turns on autostart. `--dry-run` shows the changes first. `--no-autostart` skips launchd |
| Restart Claude Desktop | so it starts the proxies |
| Web, optional | Add a `token` and `[gateway.servers.<name>]` to `config.toml`, run `mcp-guard gateway`, then `cloudflared tunnel --url http://127.0.0.1:8787`, then add `https://<tunnel host>/<token>/<name>/mcp` as a custom connector in claude.ai |
| Check | `mcp-guard status` |
| Undo | `mcp-guard uninstall` |

## 9. Code layout

```
pyproject.toml
src/mcp_guard/
  cli.py                 entry points: daemon, proxy, gateway, install, uninstall, status
  config.py              load and validate config.toml
  jsonrpc.py             parse, build, and classify JSON-RPC messages
  policy.py              which messages need approval
  audit.py               audit log with masking
  approval/
    protocol.py          messages between proxies and the daemon
    client.py            proxy side: ask the daemon, fail closed
    queue.py             daemon side: pending requests, timers, cancel
  daemon/
    server.py            Unix socket server
    ui.py                PySide6 popup and menu bar icon
  proxy/
    stdio_proxy.py       wraps one stdio server
    http_gateway.py      Streamable HTTP gateway
  install/
    desktop_config.py    wrap and unwrap claude_desktop_config.json
    launchd.py           launchd agent plist
tests/
  test_guard.py          repo guardrails (existing)
  fakes/                 fake MCP server, fake daemon
  unit/
  integration/
```

The core logic (`jsonrpc`, `policy`, `approval/queue`) has no GUI code, so it can be tested without a screen.

## 10. Dependencies

| Package | Why | Needed for |
|---|---|---|
| PySide6 | Popup and menu bar icon | Daemon |
| aiohttp | HTTP server and client | Gateway |
| pytest, pytest-asyncio | Tests | Dev only |

Everything else is the standard library (`asyncio`, `json`, `tomllib`, `ipaddress`, `secrets`, `logging`, `plistlib`). The owner installs packages. Agents cannot.

## 11. Testing

| Level | What | Who runs it |
|---|---|---|
| Unit | JSON-RPC parsing, policy, queue timers, masking, config wrap and unwrap on fixture files | Agent, with approval |
| Integration | Proxy with a fake MCP server and a fake daemon: allow, block, timeout, cancel, 20 requests at once, daemon down, server crash | Agent, with approval |
| Gateway | Token check, IP check, body limit, relay | Agent, if local port binding is allowed in the sandbox. Otherwise the owner |
| Manual | Real Claude Desktop, real claude.ai through the tunnel, popup over full screen apps | Owner |

## 12. Risks

| Risk | Plan |
|---|---|
| Desktop timeout is not really 60 seconds | Tested on 2026-11-19: 55 s works, 70 s times out. The 45 s default stays |
| Desktop does not send cancel when the user presses Stop | Tested on 2026-11-19: Stop in Claude Desktop reaches the proxy, the daemon logs "cancelled by the proxy", and the popup closes |
| Popup does not show over full screen apps | Tested on 2026-11-19 by the owner: the popup shows above a full screen Claude Desktop with Qt window flags only. PyObjC is not needed |
| claude.ai rejects a URL with a path token | Fall back to a token in a query string, or a custom header if supported |
| Quick tunnel URL changes on every run | Document a named tunnel for a stable URL |
| Qt and asyncio in one process | Run the socket server in a worker thread and send events to the UI with Qt signals |

## 13. Plan (7 days, ends 26 November 2026)

| Day | Date | Work |
|---|---|---|
| 1 | 20 Nov | Project skeleton, `jsonrpc`, `policy`, fake MCP server, unit tests |
| 2 | 21 Nov | stdio proxy with a terminal prompt in place of the popup. Test the Desktop timeout |
| 3 | 22 Nov | Daemon, Unix socket, queue, PySide6 popup, menu bar icon |
| 4 | 23 Nov | Edge cases: timeout, cancel, concurrency, crashes. Integration tests. Audit log |
| 5 | 24 Nov | Installer, uninstaller, launchd agent |
| 6 | 25 Nov | HTTP gateway, token and IP checks, tunnel test with claude.ai |
| 7 | 26 Nov | README, install guide, final review, buffer |

Each day starts with a short plan and waits for the owner's "proceed".

Outcome: days 1 to 5 done as planned. Day 6 added the Dock icon fix, gateway debugging tools, SSE answers, and the stateless experiment, and found the claude.ai connector issue. Day 7: README, findings, final review.
