# Tools Overview

The **Tools** page (`/tools`) gives operators and developers direct, on-demand access to AgentVerse's three native built-in tools without routing a request through the full agent execution pipeline. It is the diagnostic and power-user surface for the tooling subsystem.

---

## What the Tools Page Is

When an agent runs a goal it plans, executes, verifies, and replans across many steps. Each step may invoke any combination of tools automatically. The Tools page is the **imperative escape hatch**: invoke a single tool, see the raw result, iterate. No planning LLM, no goal lifecycle, no billing meter running.

```
Agent goal execution (GoalService + LangGraph)
  ↓ Plan  ↓ Execute  ↓ Verify  ↓ Replan
  Uses tools automatically, with full tracing

Tools page (direct HTTP calls)
  → User writes code / selects file / composes email
  → One API call
  → See raw result immediately
```

The page is gated behind the same `X-API-Key` tenant authentication that guards every backend endpoint. Tool executions performed here are still governance-logged via the audit trail.

---

## Three Sub-Tools

### 1. Code Runner

Executes Python, JavaScript, or Bash code inside a fully isolated Docker container. Output is returned in real time: `stdout`, `stderr`, `exit_code`, `execution_time_ms`, and a boolean `timed_out` flag.

**Source**: `app/tools/code_interpreter.py` · `app/api/tools.py:27–54`

### 2. File Manager

Reads, writes, lists, and deletes files inside the **tenant workspace** at `/tmp/agentverse-workspace/{tenant_id}/`. All path operations are protected against traversal attacks. The UI shows a split-pane: file tree on the left, editor on the right.

**Source**: `app/tools/file_ops.py` · `app/api/tools.py:63–142`

### 3. Email Composer

Sends an email via the configured SMTP server (Mailpit in development, real SMTP in production). In the current UI the form accepts To, Subject, and a plain-text body. The underlying `EmailTool` supports CC, BCC, and HTML body.

**Source**: `app/api/tools.py` (email route) · `app/tools/email_tool.py`

---

## When to Use Directly vs Through an Agent

| Scenario | Use Tools Page | Use an Agent |
|---|---|---|
| Test that a Python snippet runs correctly | ✓ | |
| Debug path-traversal protection on a file path | ✓ | |
| Inspect a file the agent wrote to the workspace | ✓ | |
| Send a one-off test email to verify SMTP config | ✓ | |
| Automate a 10-step workflow across multiple services | | ✓ |
| Generate a report, email it, and create a Jira ticket | | ✓ |
| Execute code as part of a data-processing goal | | ✓ |
| Any task requiring planning, memory, or replanning | | ✓ |

The Tools page has no planning overhead, no LLM call, and no cost. Agents use the exact same tool implementations under the hood — so what you test here is exactly what will run during an agent goal.

---

## Native Tools vs MCP Tools

AgentVerse distinguishes two classes of tools available to the agent loop:

### Native Tools (`app/tools/`)

Built directly into the backend binary. Always available. No external credentials required (other than SMTP config for email). Versioned with the backend.

```
app/tools/
├── code_interpreter.py   # CodeInterpreter — Docker execution
├── file_ops.py           # FileOps — tenant workspace filesystem
└── email_tool.py         # EmailTool — SMTP sending
```

The Tools page **only exposes native tools**.

### MCP Tools (`app/mcp/`)

Vendor-provided capabilities registered by tenants through the MCP Registry. They talk to external APIs (Jira, GitHub, Slack, Salesforce, etc.) using per-tenant OAuth tokens or API keys stored in the encrypted credential vault.

```
app/mcp/
├── registry.py           # MCPRegistry — per-tenant connector store
├── client.py             # MCPClient — tools/list + tool execution
└── servers/
    ├── jira_server.py
    ├── github_server.py
    ├── slack_server.py
    └── 50+ more...
```

MCP tools are **only accessible through the agent loop** — not from the Tools page.

```
┌─────────────────────────────────────────────────────────────┐
│                      Agent Execution                        │
│                                                             │
│   ┌──────────┐     ┌──────────────────────────────────┐   │
│   │ Executor │────▶│  Native Tools  │  MCP Tools       │   │
│   └──────────┘     │  (always on)   │  (per-tenant)    │   │
└─────────────────────────────────────────────────────────────┘
                      ↑ also exposed at /tools
                           (native only)
```

### Key Differences at a Glance

| Property | Native Tools | MCP Tools |
|---|---|---|
| Always available | Yes | Requires registration |
| Credentials needed | SMTP only (email) | OAuth / API keys |
| Accessible from Tools page | Yes | No |
| Accessible from agent loop | Yes | Yes |
| External network calls | No (email via SMTP) | Yes |
| Versioned with backend | Yes | Independent per server |
| Governance logged | Yes | Yes |

---

## Architecture Flow

```mermaid
flowchart TD
    A[User opens /tools] --> B{Select tab}
    B -- code --> C[CodeRunner component]
    B -- files --> D[FileManager component]
    B -- email --> E[EmailComposer component]

    C --> F[POST /tools/execute-code]
    D --> G[GET/POST/DELETE /tools/files]
    E --> H[POST /tools/email/send]

    F --> I[CodeInterpreter.execute]
    G --> J[FileOps tenant workspace]
    H --> K[EmailTool SMTP]

    I --> L[Docker container\nno-network · 256MB · 60s max]
    J --> M[/tmp/agentverse-workspace/{tenant_id}/]
    K --> N[Mailpit dev / real SMTP prod]
```

---

## Frontend Integration

The Tools page is implemented in `agent-verse-frontend/src/features/tools/ToolsPage.tsx`. It uses three TanStack Query mutations — one per sub-tool — so state is completely isolated between tabs:

```tsx
// Default execution timeout sent from the frontend
const runMutation = useMutation({
  mutationFn: () => toolsApi.executeCode(code, language, 30),
  // ...
});
```

The frontend passes a `30` second default timeout; the backend hard-caps this at `60` seconds regardless of what the client sends.

---

## Related Pages

| Page | What it shows |
|---|---|
| [Memory Explorer](../05-memory.md) | Long-term memories, semantic recall, tool reliability |
| [Artifacts](../06-artifacts.md) | Files produced by agent runs (screenshots, exports) |
| [RPA Sessions](../rpa/01-rpa-sessions.md) | Live browser automation sessions |
| [Perception](../07-perception.md) | Web page screenshot + AI analysis |
