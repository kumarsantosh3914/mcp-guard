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
| Popup library | PySide6 (Qt), always on top | Proposed |
| Pending timeout | 60 seconds, then block. Configurable | Proposed |
| Which requests need approval | `tools/call` by default. `resources/read` and `prompts/get` can be turned on in config | Proposed |
| Popup options | Only Allow and Block, as in the brief. No "always allow" in the first version | Proposed |
| Failure behavior | Fail closed. If anything breaks, the request is blocked | Proposed |
| Audit log | Every decision is written to a local log file | Proposed |
| Claude Desktop approach | Wrap each local MCP server with a stdio proxy that talks to one background daemon | Proposed |
| Claude Web approach | Depends on research. See `docs/research/claude-web-mcp.md` | Open |

## 6. Open questions

- Where do Claude Web remote connector calls come from: Anthropic servers or the user's browser?
- Can Claude Web run local stdio MCP servers?
- Where are Claude Desktop extensions (`.mcpb`) installed, and how do they run?
- How long does Claude Desktop wait for an MCP response before it times out?
