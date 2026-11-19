# Claude Web MCP: first research notes

| Field | Value |
|---|---|
| Date | 2026-11-19 |
| Status | Draft, **unverified** |
| Source | Model knowledge up to June 2026. No web search or fetch was used. |

Confidence levels: **High**, **Medium**, **Low**. Check every point against official docs before you rely on it.

## 1. How MCP works in claude.ai

| Point | Detail | Confidence |
|---|---|---|
| Name | Remote MCP servers are called "Connectors" in claude.ai. They were first called "Integrations" (2025). | High |
| Types | Directory connectors (listed by Anthropic, such as Notion, Figma, Canva). Custom connectors (you add a remote MCP server URL). | High |
| Origin of calls | Calls come from Anthropic's cloud, not the user's machine. The server must be public on the internet. `localhost` and private networks do not work. | High |
| IP allowlist | Anthropic publishes outbound IP ranges that servers can allowlist. | Medium |
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
