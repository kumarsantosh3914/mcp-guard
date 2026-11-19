# Design

| Field | Value |
|---|---|
| Status | Draft for owner review. Nothing is built yet. |
| Platform | macOS only |
| Language | Python 3.11 or newer |
| Inputs | `docs/requirements.md`, `docs/research/claude-web-mcp.md` |

## 1. Goal in one line

Every MCP request from Claude Desktop or Claude Web stops at mcp-guard, shows a popup on the Mac, and only goes on to the real MCP server if the user clicks Allow.

## 2. What can and cannot be covered

| MCP setup | How the call travels | Covered? | How |
|---|---|---|---|
| Claude Desktop, local server in `claude_desktop_config.json` | Desktop starts the server and talks over stdio | Yes | stdio proxy |
| Claude Desktop or Claude Web, custom connector that points to a server the user runs | Anthropic's cloud calls a public HTTPS URL | Yes | HTTP gateway plus tunnel |
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
| 3 | The daemon queues it and shows the popup. The 45 second timer starts when the request arrives, not when it is shown |
| 4a | Allow: the proxy forwards the original message to the real server and relays the answer back |
| 4b | Block, timeout, or any error: the proxy does not forward. It sends a denial back to Claude |
| 5 | The daemon writes one audit log line with the outcome |

### 4.2 Everything else

`initialize`, `tools/list`, `ping`, notifications, and server-to-client requests pass through unchanged. Methods that need approval are set in config.

The proxy forwards the original bytes of each message. It never re-encodes them, so the real server gets exactly what Claude sent.

### 4.4 Deciders

The proxy asks a "decider" for each gated request. Until the daemon exists, the proxy has three development deciders, chosen with `--decider`:

| Decider | Answer | Use |
|---|---|---|
| `block` (default) | Always Block | Safe default |
| `allow` | Always Allow | Tests and development only |
| `wait` | Never answers | Tests for timeout and cancel |

The daemon decider replaces these in step 3. A decider that fails, hangs, or gives a bad answer always means Block.

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
| Audit log | JSON lines, mode `0600`. Arguments are truncated and common secret keys (`token`, `password`, `key`, `secret`) are masked |
| Config edits | Installer always makes a timestamped backup first and never deletes files. Uninstall restores from the backup |
| Token passthrough | The gateway never forwards Claude's auth headers to upstream servers |

## 7. Configuration

File: `~/Library/Application Support/mcp-guard/config.toml`

| Key | Default | Meaning |
|---|---|---|
| `timeout_seconds` | `45` | Time before an unanswered request is blocked |
| `gated_methods` | `["tools/call"]` | Methods that need approval |
| `log_arguments` | `"truncated"` | `"none"`, `"truncated"`, or `"full"` |
| `gateway.port` | `8787` | Local port. The tunnel points here |
| `gateway.token` | generated at install | Secret path token |
| `gateway.allowed_ips` | Anthropic outbound ranges | IP allowlist |
| `[gateway.servers.<name>]` | none | Servers exposed through the gateway: a stdio command or an upstream URL |

## 8. Install and run (user view)

| Step | Command |
|---|---|
| Install package | `pip install .` (in a virtual env) |
| Set up | `mcp-guard install` wraps Desktop servers, writes config, installs the launchd agent |
| Restart Claude Desktop | so it starts the proxies |
| Web, optional | `mcp-guard gateway`, then `cloudflared tunnel --url http://127.0.0.1:8787`, then add the printed URL as a custom connector in claude.ai |
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
| Desktop timeout is not really 60 seconds | Test early on day 2 with a slow fake tool. Change the default if needed |
| Popup does not show over full screen apps | Try Qt window flags first. If not enough, set the macOS window level with PyObjC (one more dependency, ask first) |
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
