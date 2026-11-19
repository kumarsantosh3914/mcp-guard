# Requirements

Source: project brief from the owner. Sections 1 to 4 are copied as given. Section 5 lists decisions made with the owner so far.

## 1. Objective

Develop a Python-based application that monitors Model Context Protocol (MCP) communication initiated by Claude Desktop and Claude Web, and gives users control over whether each individual MCP request is allowed to proceed or is blocked.

## 2. Functional requirements

### 2.1 MCP communication monitoring

- Monitor outgoing MCP requests initiated by Claude Desktop and Claude Web.
- Detect when a new MCP communication or tool invocation is initiated.
- Capture relevant request information (MCP server, tool name, request parameters) wherever accessible.

### 2.2 User confirmation mechanism

- Whenever an MCP request is initiated, display a confirmation popup/modal on top of other active applications.
- The popup should show relevant information about the pending request so the user can make an informed decision.
- Provide two options: Allow (permit the request to proceed) or Block (prevent the request from being executed).

### 2.3 Request handling

- Keep the MCP request pending until the user makes a decision.
- If allowed, forward/release the request for execution.
- If blocked, prevent execution and return an appropriate error or denial response to the requesting application.
- Handle multiple simultaneous MCP requests reliably.

### 2.4 Application reliability

- The monitoring application should run in the background.
- The confirmation popup should appear above other active windows.
- Handle application termination, communication failures, and unexpected errors gracefully.

## 3. Technical requirements

- Language: Python.
- Use an appropriate architecture for monitoring and intercepting MCP communication.
- Code should be modular, maintainable, and properly documented.
- Include instructions for installing, configuring, and running the application.

## 4. Evaluation criteria

- Correctness and reliability of MCP request interception.
- Compatibility with Claude Desktop and Claude Web.
- Effectiveness of the user confirmation mechanism.
- Handling of concurrent requests and edge cases.
- Code quality, architecture, and documentation.
- Security and reliability of the implementation.

## 5. Decisions so far

| Topic | Decision | Status |
|---|---|---|
| Platform | macOS only | Final |
| Deadline | 26 November 2026 (one week from 19 November 2026) | Final |
| Popup library | PySide6 (Qt), always on top | Final |
| Pending timeout | 45 seconds, then block. Configurable. Claude Desktop is reported to give up after about 60 seconds, so the popup must decide first | Final |
| Which requests need approval | `tools/call` by default. `resources/read` and `prompts/get` can be turned on in config | Final |
| Popup options | Only Allow and Block, as in the brief. No "always allow" in the first version | Final |
| Failure behavior | Fail closed. If anything breaks, the request is blocked | Final |
| Audit log | Every decision is written to a local log file | Final |
| Claude Desktop approach | Wrap each local MCP server in `claude_desktop_config.json` with a stdio proxy that talks to one background daemon | Final |
| Claude Web approach | Calls come from Anthropic's cloud, so mcp-guard runs an HTTP gateway on the Mac and a tunnel makes it public on port 443. The same gateway covers remote connectors added in Claude Desktop | Final |
| Gateway auth | Secret token in the URL path plus an allowlist of Anthropic's outbound IP ranges. Full OAuth 2.1 is documented as future work | Final |
| Tunnel | Cloudflare Tunnel. The owner installs it | Final |
| Desktop extensions (`.mcpb`) | Not covered in the first version. Documented as a known limit | Final |
| Directory connectors (Notion, Figma, and others) | Cannot be covered. Anthropic's cloud calls them directly, so nothing on the Mac sees the call. Documented as a known limit | Final |

Details: `docs/design.md`. Research behind these decisions: `docs/research/claude-web-mcp.md`.

## 6. Open questions

Answered by research (see `docs/research/claude-web-mcp.md`, section 8):

- Claude Web remote connector calls come from Anthropic's cloud. Verified.
- Claude Web cannot run local stdio MCP servers. Verified.
- Claude Desktop extensions (`.mcpb`) are installed per user and run as local stdio processes. Verified.

Still open:

- Claude Desktop timeout. About 60 seconds and not configurable, from user reports only. Confirm with a real test.
- Folder where extensions are stored on macOS. The owner checks this on a real Mac.
- Does claude.ai accept a custom connector URL that has a secret token in its path? Confirm with a real test.
