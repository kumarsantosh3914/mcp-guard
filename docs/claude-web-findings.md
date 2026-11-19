# Claude Web: findings

| Field | Value |
|---|---|
| Date | 2026-11-19 |
| Status | Open issue. The gateway works with the official MCP client. claude.ai's custom connector does not connect |
| Setup | macOS, `mcp-guard gateway` on `127.0.0.1:8787`, Cloudflare quick tunnel (`*.trycloudflare.com`), fake MCP server from `tests/fakes/` |

## Summary

claude.ai sends `initialize` to the gateway, gets a valid answer in about 70 ms, and then sends nothing else. The UI shows "Couldn't reach". The browser's network log shows the real error:

```json
{"type": "invalid_request_error", "message": "Couldn't start sign-in. Try again.",
 "details": {"error_code": "mcp_auth_start_failed"}}
```

So claude.ai decides the server needs an OAuth sign-in, tries to start one, and fails. Why it decides that is still unknown. A public authless server (DeepWiki) connects fine from the same account.

## What works (verified)

| Check | Result |
|---|---|
| Official MCP Python SDK client to the gateway, local | All steps pass: initialize, tools/list, tools/call with popup and Allow |
| Same client through the Cloudflare tunnel | All steps pass |
| Raw HTTP through the tunnel: initialize, initialized, tools/list, GET | 200, 202, 200, 405. Identical to local |
| claude.ai requests reach the gateway | Yes, from `160.79.106.x` (inside Anthropic's published outbound range), with the right token |
| Gateway security | Wrong token: 404. Own IP through the tunnel: 403 with the IP named |
| The owner's claude.ai account | Can add custom connectors (DeepWiki at `https://mcp.deepwiki.com/mcp` connects) |

## What claude.ai sends

```
User-Agent: python-httpx/0.28.1
Accept: application/json, text/event-stream
Mcp-Protocol-Version: 2025-11-25
body: {"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2025-11-25","capabilities":{},
                 "clientInfo":{"name":"Anthropic","version":"1.0.0"}}}
```

It always sends two such requests, about 6 seconds apart, from different IPs. Each starts a new gateway session. No other request follows: no `notifications/initialized`, no `tools/list`, no `GET`.

## What was tried

| # | Idea | Change | Result |
|---|---|---|---|
| 1 | IP filter blocks part of claude.ai | Allowed every IP for one test | Same |
| 2 | Gateway too slow | Measured | 70 to 80 ms |
| 3 | claude.ai wants SSE, not JSON | Answers as SSE when accepted (now the default) | Same |
| 4 | The quick tunnel breaks `GET` or SSE | Raw `GET` and SSE through the tunnel | Tunnel passes both |
| 5 | claude.ai dislikes sessions (DeepWiki sends no `Mcp-Session-Id`) | `stateless = true` | Same |
| 6 | claude.ai dislikes the echoed protocol version `2025-11-25` | Fake server answers `2025-06-18` | Same |
| 7 | Get the real error | Browser DevTools on claude.ai | `mcp_auth_start_failed` |

## Differences from DeepWiki that are still untested

| Difference | DeepWiki | mcp-guard gateway |
|---|---|---|
| Host | Own domain | `*.trycloudflare.com` quick tunnel |
| URL path | `/mcp` | `/<token>/<server>/mcp` (secret in the path) |
| OAuth discovery URLs (`/.well-known/oauth-protected-resource`, `/.well-known/oauth-authorization-server`) | Unknown | 404 |
| Response headers | `Cache-Control: no-cache, no-transform`, `x-accel-buffering: no`, `Vary: Origin` | `Cache-Control: no-cache` |

## Next steps, in order of value

1. Log every request that reaches the gateway during a connector add, including the `/.well-known/...` ones (the gateway now logs those paths, and never other paths), to see which discovery URLs claude.ai asks for.
2. Try a named Cloudflare tunnel on a real domain, and a token in a header or query string instead of the path.
3. Answer OAuth Protected Resource Metadata (RFC 9728) in a way that says "no sign-in needed", or implement a small OAuth 2.1 server with Dynamic Client Registration, as the MCP authorization spec describes. This is also the real fix for the IP-check weakness noted in the README.
4. Ask Anthropic support, quoting the `mcp_auth_start_failed` flow ids from the browser.

## How to reproduce

```
.venv/bin/pip install mcp                                         # debug tool only
.venv/bin/python scripts/dev_gateway_config.py --local            # config with a new token
.venv/bin/mcp-guard gateway --log-level debug
cloudflared tunnel --url http://127.0.0.1:8787
.venv/bin/python scripts/dev_mcp_client.py "http://127.0.0.1:8787/<TOKEN>/fake/mcp"          # official client
.venv/bin/python scripts/dev_mcp_client.py "https://<tunnel>/<TOKEN>/fake/mcp" --raw         # plain HTTP
```

To test through the tunnel from your own Mac, allow your IP for a moment with `--allow-ip <ip>/32` or `--allow-ip <ipv6 prefix>::/64`, and remove it afterwards.
