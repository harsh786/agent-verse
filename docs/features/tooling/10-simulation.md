# Simulation Mode

**Simulation** lets you test agent behavior against your exact goal and tool mix — with zero side effects and zero external API calls. By replacing all real MCP tool implementations with pre-configured mock responses, you get deterministic, cost-free validation of agent logic before it touches production systems.

---

## What Simulation Is

```
Normal agent execution:
  Goal → Planner → Executor → Real MCP tools → Side effects

Simulation:
  Goal → Planner → Executor → MockMCPClient → Fake responses → No side effects
                                                               ↑ You control these
```

Simulation is the **safe testing sandbox** for the agent loop. It answers: "Given this goal, will my agent make the right decisions and call the right tools in the right order — before I let it loose on production?"

---

## Architecture

**Source**: `agent-verse-backend/app/enterprise/simulation.py`

### MockMCPClient

The core of simulation is `MockMCPClient` — a drop-in replacement for the real `MCPClient`. It accepts a dictionary mapping tool names to canned responses:

```python
class MockMCPClient:
    def __init__(
        self,
        mock_tools: dict[str, Any] | None = None,
        mock_responses: dict[str, Any] | None = None,
    ) -> None:
        # mock_responses takes priority; mock_tools is the legacy alias
        self._mocks = mock_responses or mock_tools or {}
```

When the agent loop calls `call_tool()`, the MockMCPClient looks up the mock response and returns it instantly — no network call, no credential, no side effect:

```python
async def call_tool(self, server_id, tool_name, arguments, tenant_ctx) -> dict:
    key = tool_name
    full_key = f"{server_id}.{tool_name}"
    mock = self._mocks.get(full_key) or self._mocks.get(key)
    
    if mock is not None:
        self._hit_tools.add(full_key)
        content = mock if isinstance(mock, str) else json.dumps(mock)
        return {"content": [{"type": "text", "text": content}], "simulated": True}
    
    # No mock configured for this tool
    return {
        "content": [{"type": "text", "text": f"[simulated: no mock for {tool_name}]"}],
        "simulated": True,
    }
```

All responses carry `"simulated": True` so the agent loop can distinguish real from simulated results when logging.

---

## SimulationRun Schema

```python
@dataclass
class SimulationRun:
    run_id:           str          # UUID hex
    goal:             str          # The goal text
    mock_tools:       dict         # Your configured mocks
    status:           str          # "pending" | "running" | "completed" | "failed"
    result:           dict         # Final agent output
    created_at:       str          # ISO-8601 UTC
    steps_executed:   list[dict]   # Each step: {tool, args, mock_response}
    tools_called:     list[str]    # All tool names invoked
    mock_tools_used:  list[str]    # Subset of tools that had configured mocks
    cost_estimate:    float        # Estimated LLM token cost (real LLM may be used)
    used_real_llm:    bool         # True if LLM calls were made
```

---

## Configuring Mock Responses

Mocks are a flat JSON map from `tool_name` (or `server_id.tool_name`) to a response value. The value can be a string, a dict (serialized to JSON), or a list:

```json
{
  "github:list_issues": [
    {"id": 123, "title": "Login button broken", "labels": ["bug", "P1"]},
    {"id": 124, "title": "Performance regression in search", "labels": ["performance"]}
  ],
  "jira:create_issue": {
    "id": "PLAT-456",
    "url": "https://jira.company.com/browse/PLAT-456",
    "status": "Open"
  },
  "slack:send_message": {
    "ok": true,
    "ts": "1719600000.000100",
    "channel": "C0123456789"
  },
  "email:send": "Message delivered: msg_id_abc123"
}
```

**Key formats:**
- `tool_name` — matches any server's tool of that name
- `server_id.tool_name` — matches only that specific server's tool (more specific)

---

## Simulation vs Ghost Run

| Feature | Simulation | Ghost Run |
|---|---|---|
| LLM planner used | Yes (configurable) | Yes |
| Real tool calls | Never | Never |
| Custom mock responses | Yes | No (LLM improvises) |
| Deterministic results | Yes (fixed mocks) | No (LLM varies) |
| Tests tool selection logic | Yes | No |
| Cost | Low (LLM for planning) | Low (LLM for planning) |
| Use case | Test specific tool chains | Estimate plan quality |

**Ghost run** generates a planner-only trace — the agent plans but neither calls real tools nor evaluates mock responses. **Simulation** runs the full execution loop against your exact mock responses, so you can verify the agent picks the right tools, handles the responses correctly, and produces the expected output.

---

## Policy Simulation

Beyond tool mocks, simulation can also test **governance policy compliance** — run a goal against your policy rules without actually executing anything:

```bash
curl -X POST https://api.agentverse.dev/governance/simulate \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Delete all user records from the production database",
    "dry_run": true
  }'
```

```json
{
  "allowed":     false,
  "violations":  [
    {
      "rule":    "no-destructive-in-prod",
      "message": "Tool 'db:delete_all' is blocked in PRODUCTION environment",
      "severity": "critical"
    }
  ],
  "warnings": []
}
```

Policy simulation runs the governance policy engine without the agent loop — useful for validating that your policies correctly block dangerous goals before deploying policy changes.

---

## API Reference

### `POST /enterprise/simulation`

Run a goal in simulation mode.

**Authentication**: `X-API-Key: <tenant_api_key>`

```bash
curl -X POST https://api.agentverse.dev/enterprise/simulation \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "Create a Jira ticket for the login bug and notify the team on Slack",
    "mock_responses": {
      "github:list_issues": [{"id": 123, "title": "Login button broken"}],
      "jira:create_issue": {"id": "PLAT-456", "url": "https://jira.company.com/PLAT-456"},
      "slack:send_message": {"ok": true, "ts": "1719600000.000100"}
    }
  }'
```

**Request Body**

| Field | Type | Required | Description |
|---|---|---|---|
| `goal` | `string` | Yes | Goal text to simulate |
| `mock_responses` | `dict` | No | Map of tool_name → response. Empty dict = LLM improvises responses |
| `mock_tools` | `dict` | No | Legacy alias for `mock_responses` |
| `max_iterations` | `int` | No | Max agent iterations (default: 10) |
| `use_real_llm` | `bool` | No | Use real LLM for planning (default: true) |

**Response**

```json
{
  "run_id":          "sim_abc123",
  "status":          "completed",
  "goal":            "Create a Jira ticket for the login bug and notify the team on Slack",
  "steps_executed": [
    {
      "step":         1,
      "tool":         "github:list_issues",
      "arguments":    {"state": "open", "labels": "bug"},
      "response":     "[{\"id\": 123, \"title\": \"Login button broken\"}]",
      "simulated":    true
    },
    {
      "step":         2,
      "tool":         "jira:create_issue",
      "arguments":    {"title": "Login button broken", "priority": "P1"},
      "response":     "{\"id\": \"PLAT-456\", \"url\": \"...\"}",
      "simulated":    true
    },
    {
      "step":         3,
      "tool":         "slack:send_message",
      "arguments":    {"channel": "#bugs", "text": "Jira ticket created: PLAT-456"},
      "response":     "{\"ok\": true}",
      "simulated":    true
    }
  ],
  "tools_called":     ["github:list_issues", "jira:create_issue", "slack:send_message"],
  "mock_tools_used":  ["github:list_issues", "jira:create_issue", "slack:send_message"],
  "result":           {"summary": "Jira ticket PLAT-456 created, Slack message sent to #bugs"},
  "cost_estimate":    0.0023,
  "used_real_llm":    true,
  "created_at":       "2024-06-29T10:00:00Z"
}
```

---

### `POST /governance/simulate`

Validate a goal against governance policies without execution.

```bash
curl -X POST https://api.agentverse.dev/governance/simulate \
  -H "X-API-Key: $API_KEY" \
  -d '{"goal": "Deploy backend to production", "dry_run": true}'
```

```json
{
  "allowed":  true,
  "warnings": [
    {
      "rule":    "prod-deploy-requires-hitl",
      "message": "Production deployments require human approval",
      "severity": "warning"
    }
  ],
  "violations": [],
  "hitl_required": true
}
```

---

## Execution Sequence

```mermaid
sequenceDiagram
    participant Client as API Client
    participant API as POST /enterprise/simulation
    participant SR as SimulationRunner
    participant MCP as MockMCPClient
    participant Loop as Agent Loop

    Client->>API: {goal, mock_responses: {...}}
    API->>SR: SimulationRunner(goal, mock_responses)
    SR->>MCP: MockMCPClient(mock_responses={"jira:create_issue": {...}})
    SR->>Loop: run_agent(goal, mcp_client=MockMCPClient)

    Loop->>Loop: Plan goal (real LLM)
    Loop->>MCP: call_tool("jira", "create_issue", {title: "..."})
    MCP->>MCP: Lookup "jira:create_issue" in _mocks
    MCP-->>Loop: {"content": [{"text": "{\"id\": \"PLAT-456\"}"}], "simulated": true}
    Loop->>MCP: call_tool("slack", "send_message", {...})
    MCP-->>Loop: {"content": [{"text": "{\"ok\": true}"}], "simulated": true}

    Loop->>Loop: Verify result (real LLM)
    Loop-->>SR: {status: "completed", result: {...}}
    SR->>SR: Build SimulationRun record
    SR-->>API: SimulationRun
    API-->>Client: JSON response
```

---

## Checking Which Mocks Were Hit

`MockMCPClient.was_hit()` lets you assert that specific tools were called during simulation:

```python
mock_client = MockMCPClient(mock_responses={
    "jira:create_issue": {"id": "PLAT-456"},
    "slack:send_message": {"ok": True},
})

# ... run simulation ...

assert mock_client.was_hit("jira:create_issue")   # True
assert mock_client.was_hit("slack:send_message")  # True
assert mock_client.was_hit("github:create_pr")    # False — not called
```

`was_hit()` checks both the `tool_name` and `server_id.tool_name` formats:

```python
def was_hit(self, tool_name: str | None) -> bool:
    return (
        tool_name in self._hit_tools or
        any(tool_name == k.split(".")[-1] for k in self._hit_tools)
    )
```

---

## When to Use Simulation

| Scenario | Use Simulation |
|---|---|
| Verify agent selects the right tools for a new goal type | ✓ |
| Test agent error handling when a tool returns failure | ✓ (mock `{"error": "rate limited"}`) |
| Validate governance policies before deploying | ✓ (policy simulation) |
| Cost estimation before running against real APIs | ✓ (`cost_estimate` field) |
| Regression testing after prompt changes | ✓ |
| Integration testing in CI pipelines | ✓ |
| Debugging an unexpected tool selection | ✓ |

---

## Pre-Configured Tool Actions

`simulation.py` includes human-readable labels for all well-known tools:

```python
_TOOL_ACTIONS: dict[str, str] = {
    "github:list_issues":   "Fetch GitHub issues",
    "github:create_pr":     "Open GitHub pull request",
    "jira:search_issues":   "Search Jira issues",
    "jira:create_issue":    "Create Jira issue",
    "confluence:create_page": "Create Confluence page",
    "slack:send_message":   "Send Slack message",
    "email:send":           "Send email",
    "datadog:get_metrics":  "Fetch Datadog metrics",
    "salesforce:query":     "Query Salesforce CRM",
    # ...
}
```

These labels appear in the simulation step log for human-readable audit trails.
