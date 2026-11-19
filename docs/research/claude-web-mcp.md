# Claude Web MCP: first research notes

| Field | Value |
|---|---|
| Date | 2026-11-19 |
| Status | Draft. Sections 1 to 7 **unverified**. Section 8 checked on the web, each row marked. |
| Source | Sections 1 to 7: model knowledge up to June 2026. Section 8: web search and official docs, 2026-11-19. |

Confidence levels: **High**, **Medium**, **Low**. Check every point against official docs before you rely on it.

## 1. How MCP works in claude.ai

| Point | Detail | Confidence |
|---|---|---|
| Name | Remote MCP servers are called "Connectors" in claude.ai. They were first called "Integrations" (2025). | High |
| Types | Directory connectors (listed by Anthropic, such as Notion, Figma, Canva). Custom connectors (you add a remote MCP server URL). | High |
| Origin of calls | Calls come from Anthropic's cloud, not the user's machine. The server must be public on the internet. `localhost` and private networks do not work. | **Verified**, see section 8 |
| IP allowlist | Anthropic publishes outbound IP ranges that servers can allowlist. | **Verified**, see section 8 |
| Transport | Streamable HTTP. SSE also works but is being phased out. | Medium-High |
| Auth | OAuth 2.0 per the MCP authorization spec, with Dynamic Client Registration. Servers without auth also work. A later option lets you give your own client ID and secret. | Medium |
| MCP features | Tools are supported. Resources and prompts are partly supported. Sampling and elicitation support is unknown. | Low-Medium |
| Plans | First Max, Team, and Enterprise. Later Pro. Free plan may be limited. | Medium |

## 2. Permissions and controls

| Level | Control | Confidence |
|---|---|---|
| Chat | Turn connectors and their tools on or off per chat ("Search and tools" menu). | Medium-High |
| Tool | Per tool setting: always allow, ask for approval, or never. | Medium |
| Org (Team, Enterprise) | Owners enable or disable connectors for the org and add custom connectors. Members then do their own OAuth login. | Medium |
| User machine | No local hooks or local policy files on the web. All control is on the server side or in admin settings. | High |

## 3. Security risks

| Risk | Meaning |
|---|---|
| Prompt injection | A tool result (email, web page, ticket) holds hidden instructions for the model. |
| Tool poisoning | The tool description itself holds hidden instructions. The user does not see them, the model does. |
| Rug pull | The server changes a tool's description or behavior after the user approves it. |
| Tool shadowing | One server's tool changes how the model uses another server's tools. |
| Data exfiltration | Private data, untrusted content, and a way to send data out, all in one session ("lethal trifecta"). |
| Broad OAuth scope | The token has more access than the task needs. |
| Token passthrough, confused deputy | A proxy server forwards the wrong token. The MCP spec forbids this. |
| Supply chain | A third-party server is malicious or compromised. |

Confidence: High for the list itself. It comes from the MCP spec "Security Best Practices" and known public research. Recent cases and fixes still need checking.

## 4. Surfaces compared

| Surface | How MCP is used | Where enforcement can live |
|---|---|---|
| claude.ai web | Remote connectors only | Server or gateway side, org admin settings |
| Claude Desktop | Local (stdio, `.mcpb` extensions) and remote | Local config, server side |
| Claude Code | Local and remote, `.mcp.json`, `claude mcp add` | `settings.json` permissions (`mcp__server__tool`), PreToolUse hooks |
| Messages API | `mcp_servers` parameter (MCP connector beta), remote only | Developer code, server side |

**Observed in a Claude Code session (verified):** connectors added in claude.ai also show up in Claude Code. In this repo, `guard.py` and `AGENTS.md` block them. On the web there is no such local block.

## 5. Possible directions for mcp-guard

For discussion only. The owner decides the design.

| Option | Idea | Works on web? |
|---|---|---|
| A. Local hooks | What `guard.py` does today | No, Claude Code only |
| B. Remote MCP gateway or proxy | A public proxy between Claude and the real server: per-tool policy, audit log, description pinning (to catch rug pulls), output scanning | Yes |
| C. Scanner or auditor | A tool that checks an MCP server's tools and descriptions for risk | Yes, indirectly |
| D. Admin checklist and docs | A safe setup guide for org owners | Yes |

Note: only B gives real enforcement on the web. It raises open questions about stack, hosting, and OAuth.

## 6. To verify (when web access is available)

1. Current tool-level permission options and their names (section 2).
2. Org admin controls: who can add custom connectors, and whether an allowlist exists.
3. Supported MCP features: resources, prompts, sampling, elicitation.
4. Whether SSE is still supported, and Anthropic's IP ranges.
5. How claude.ai connectors sync to Claude Code, and how to turn that off.

## 7. Open questions for the owner

- Which direction (A, B, C, D, or a mix)?
- Is web enforcement (option B) in scope?

## 8. Verification, 2026-11-19

Platform: macOS only.

Marks:
- **Verified**: an official Anthropic page says it.
- **Unverified**: only user reports, blogs, or mirrors say it. Or nothing was found.

### Q1. Where do custom connector (remote MCP) calls come from?

| Point | Answer | Mark | Source |
|---|---|---|---|
| Origin | Anthropic's cloud, not the browser or the device. This is true for every client: claude.ai, Desktop, Cowork, mobile. Quote: "Claude connects to your remote MCP server from Anthropic's cloud infrastructure, rather than from your local device." | **Verified** | [Get started with custom connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp) |
| Reachability | The server must be reachable over the public internet from Anthropic's IP ranges. VPN, private network, or firewall blocks it. | **Verified** | Same page |
| Port | Only the standard HTTPS port 443. A URL with another port, such as `:8443`, does not work. | **Verified** | Same page |
| IP ranges | See the table below. Allowlist the outbound ranges for a private server. | **Verified** | [Anthropic IP addresses](https://platform.claude.com/docs/en/api/ip-addresses) |

Anthropic IP ranges (read 2026-11-19):

| Direction | Use | IPv4 | IPv6 |
|---|---|---|---|
| **Outbound** (Anthropic to your server) | MCP tool calls to external servers. Quote: "the stable IP addresses that Anthropic uses for outbound requests (for example, when making MCP tool calls to external servers)." | `160.79.104.0/21`, `153.61.208.0/20` | None listed |
| Inbound (you to Anthropic) | Anthropic services receive connections here | `160.79.104.0/23`, `153.61.224.0/22` | `2607:6bc0::/48` |
| Phased out | No longer used. Remove from allowlists. | `34.162.46.92/32`, `34.162.102.82/32`, `34.162.136.91/32`, `34.162.142.92/32`, `34.162.183.95/32` | n/a |

Notes:
- The page says: "These addresses will not change without notice." It gives no dates.
- The page talks about "MCP tool calls" and the "MCP connector". It does not use the words "custom connectors". The support page links here for custom connectors, so they are taken to be the same ranges.
- For Claude Platform on AWS, outbound tool calls (MCP connector, web search, web fetch) also come from these Anthropic ranges.

Meaning for mcp-guard: a gateway for the web (option B) must be public, on port 443, and can allowlist only the two outbound IPv4 ranges. An IP allowlist alone is not real auth, because every Anthropic customer's calls come from the same ranges. OAuth is still needed.

### Q2. Can Claude Web run local stdio MCP servers?

| Point | Answer | Mark | Source |
|---|---|---|---|
| claude.ai web | No. Local servers "aren't available in claude.ai or in Cowork sessions that run in the cloud". | **Verified** | [Get started with custom connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp) |
| Where local works | Claude Desktop (`claude_desktop_config.json` and extensions), and a Cowork session that runs locally on the device. | **Verified** | Same page, and [Local MCP servers on Claude Desktop](https://support.claude.com/en/articles/10949351) |

### Q3. Claude Desktop extensions (`.mcpb`): install and run

| Point | Answer | Mark | Source |
|---|---|---|---|
| Name | `.mcpb` (MCP Bundle) replaced `.dxt`. | **Verified** | [Desktop Extensions (Anthropic Engineering)](https://www.anthropic.com/engineering/desktop-extensions) |
| How to install | Settings, Extensions, Advanced settings, "Install Extension...", pick the `.mcpb` file. Double-click and drag-and-drop also work. Install is per user. | **Verified** | [Local MCP servers on Claude Desktop](https://support.claude.com/en/articles/10949351), [MCPB docs](https://claude.com/docs/connectors/building/mcpb) |
| How it runs | The server runs on the user's machine over stdio. Node.js, Python, and binary servers are supported. | **Verified** | [MCPB docs](https://claude.com/docs/connectors/building/mcpb), [Local MCP servers on Claude Desktop](https://support.claude.com/en/articles/10949351) |
| Runtime | Node.js ships with Claude Desktop, so no separate install. No official page says Python is bundled. | **Verified** (Node), **Unverified** (Python) | Same pages, and the engineering post |
| Secrets | Sensitive config values are encrypted in the OS secure storage (Keychain on macOS). | **Verified** | [Local MCP servers on Claude Desktop](https://support.claude.com/en/articles/10949351) |
| Updates | Extensions from the official directory update automatically by default. Private `.mcpb` files must be reinstalled by hand. | **Verified** | Same page |
| Admin controls | Owners can enable or disable public extensions and use an allowlist. Machine-level policy (MDM on macOS) overrides in-app controls (blocklist, allowlist). | **Verified** | Same page, [Extension allowlist](https://support.claude.com/en/articles/12592343-enabling-and-using-the-desktop-extension-allowlist), engineering post |
| Directory | The directory no longer accepts MCPB submissions. Directory listings for desktop extensions are deprecated. | **Verified** | [MCPB docs](https://claude.com/docs/connectors/building/mcpb) |
| Sandbox | No official page mentions a sandbox for extensions. | **Unverified** (nothing found) | n/a |
| File path on macOS | No official page states it. A user bug report names `~/Library/Application Support/Claude/Claude Extensions/` and `~/Library/Application Support/Claude/extensions/`, and staging folders `dxt-install-*` under `~/Library/Application Support/Claude/`. | **Unverified** | [claude-code issue 68484](https://github.com/anthropics/claude-code/issues/68484) |

To verify the path, the owner can install a test extension and look in `~/Library/Application Support/Claude/` (outside this folder, so the owner must do it).

### Q4. How long does Claude Desktop wait for an MCP request (timeout)?

| Point | Answer | Mark | Source |
|---|---|---|---|
| Official value | No official page states a timeout. | **Unverified** (nothing found) | Checked: the 4 official pages above |
| Reported value | About 60 seconds. Error: `MCP error -32001: Request timed out`. Reporters think Desktop uses the SDK default. | **Unverified** | [claude-code issue 22542](https://github.com/anthropics/claude-code/issues/22542) (closed as not planned, no Anthropic reply), [claudeissues 63379](https://claudeissues.com/issue/63379-bug-desktop-app-cancels-stdio-mcp-tool-calls-at-60s-and-ignores-mcp-tool-timeout) |
| Can it be changed? | Reports say no. A `timeout` field in `claude_desktop_config.json` and the `MCP_TOOL_TIMEOUT` env var are ignored by Desktop. Claude Code CLI does honor `MCP_TOOL_TIMEOUT`. | **Unverified** | Same issues, [claudeissues 43791](https://claudeissues.com/issue/43791-bug-timeout-field-in-claude-desktop-config-json-not-honored-for-mcp-servers) |
| Startup | Reports say the `initialize` request is also cut at 60 seconds. | **Unverified** | [claudeissues 84136](https://claudeissues.com/issue/84136-bug-claude-desktop-hardcoded-60s-initialize-timeout-drops-stdio-mcp-servers-whos) |
| **Measured, 2026-11-19** | Owner's test on macOS through `mcp-guard proxy` and the fake server's `slow` tool. 30 s and 55 s calls returned. A 70 s call failed with "Request timed out". So Desktop stops waiting between 55 and 70 seconds, which fits the reported 60 seconds. | **Verified by test** | Manual test, see `docs/design.md` section 4.1 |
| SDK default | The MCP TypeScript SDK sets `DEFAULT_REQUEST_TIMEOUT_MSEC = 60000`. Seen only in search snippets and mirrors. The official raw file URL returned 404. | **Unverified** | [TS SDK docs (v1.x)](https://ts.sdk.modelcontextprotocol.io/protocol.html) |

Meaning for mcp-guard: if a proxy sits in front of a local server, the proxy plus the real tool must answer in under about 60 seconds.

### Still open

- Find the extension folder on a real Mac.
- Check `DEFAULT_REQUEST_TIMEOUT_MSEC` in the current TS SDK repo on GitHub.
