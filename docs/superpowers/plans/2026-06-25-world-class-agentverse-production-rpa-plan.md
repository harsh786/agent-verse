# World-Class AgentVerse Production + RPA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform AgentVerse from a strong scaffold into a production-scale autonomous agent platform that can execute complex multi-tool, multi-agent, scheduled RPA workflows without fake providers, in-memory state, or mocked execution paths.

**Architecture:** Build a durable execution fabric around persisted agents, goals, connectors, schedules, workflow plans, tool calls, approvals, checkpoints, and audit events. The runtime path becomes: user or schedule submits goal -> router selects agent/workflow -> planner discovers tools -> executor calls real MCP/RPA tools -> governance gates side effects -> verifier validates result -> state, artifacts, metrics, traces, and audit are persisted.

**Tech Stack:** FastAPI, SQLAlchemy async, PostgreSQL 16 + RLS + pgvector, Redis, Celery, LangGraph, MCP JSON-RPC, Playwright/browser automation for RPA, React/Vite, TanStack Query, Zustand, OpenTelemetry, Prometheus, pytest, Vitest, Playwright E2E.

---

## Product Promise

AgentVerse must support goals like:

```text
Every weekday at 10 AM, fetch all open Jira issues in BAU, summarize them into a Confluence page, email the report to the team, and request approval before changing any Jira fields.
```

The platform must execute this as real autonomous work:

```text
Schedule fires
  -> loads agent/workflow
  -> discovers Jira, Confluence, Email, and RPA tools
  -> plans execution
  -> calls Jira MCP tools
  -> calls Confluence MCP tools
  -> sends email via connector
  -> uses browser/RPA fallback for missing APIs
  -> gates risky mutations through governance/HITL
  -> persists events, artifacts, traces, metrics, and audit
```

## Gap Inventory By Layer

### Layer 1: Identity, Tenancy, Persistence

Current gaps:
- Several services are still in-memory-first.
- DB writes are sometimes fire-and-forget.
- Some startup sync paths are blocked by RLS or incomplete.
- Agent, schedule, approval, audit, knowledge, and execution state are not uniformly DB-backed.

Target state:
- PostgreSQL is the source of truth for tenants, agents, goals, schedules, policies, approvals, tool calls, artifacts, and audit.
- Redis is used for queues, rate limits, locks, ephemeral streams, and connector registry cache.
- Every tenant-scoped query uses RLS context.

### Layer 2: Agent, Goal, Connector Binding

Current gaps:
- Goal submission does not require or select `agent_id`.
- Agent records own `connector_ids`, but execution does not load them.
- Frontend goal form has no agent picker.

Target state:
- Goals can be submitted with explicit `agent_id` or `workflow_mode=auto_route`.
- Agent router can choose best agent when user selects auto-route.
- Agent execution receives connector/tool context.

### Layer 3: MCP Tool Runtime

Current gaps:
- Connector test supports Atlassian MCP initialize, but runtime MCP client still needs full JSON-RPC `tools/list` and `tools/call` support.
- Agent loop does not call MCP tools.
- Tool schemas are not injected into planning.

Target state:
- Runtime can discover tools from MCP servers.
- Planner sees available tools and schemas.
- Executor emits structured tool calls.
- Tool calls are executed, audited, retried, rate-limited, and rendered in UI.

### Layer 4: Complex Workflow Planning

Current gaps:
- No planner maps complex goals to multi-connector steps.
- A2A and collaboration are in-memory and not part of execution.
- Goal-tree execution exists but is disabled and not connected to agents.

Target state:
- Workflow planner decomposes goals into typed steps.
- Steps can run sequentially or in parallel based on dependencies.
- Steps can target tools, agents, or RPA actions.

### Layer 5: RPA Automation

Current gaps:
- Browser agent exists but is not integrated into planning/tool execution.
- No durable browser session model.
- No selector vault, screenshot artifacts, replay, or human takeover.

Target state:
- RPA connector exposes browser tools through the same MCP/tool interface.
- Agent can use RPA when no API connector exists.
- Browser actions are sandboxed, recorded, auditable, and recoverable.

### Layer 6: Schedules And Autonomous Execution

Current gaps:
- Schedule API and Celery schedule scanner are disconnected.
- Schedules do not reliably submit agent-bound goals.
- Webhook triggers return OK but do not execute work.

Target state:
- Every schedule references an agent/workflow and goal template.
- Celery Beat dispatches due schedules into durable goal jobs.
- Webhooks/events/cron/interval/once all create durable execution records.

### Layer 7: Governance And Safety

Current gaps:
- Policies are not fully enforced at real tool-call time.
- HITL UI actions are incomplete.
- Rollback is a no-op stub.
- Budget config is not fully tied to runtime execution.

Target state:
- Every tool/RPA action runs through a policy pipeline.
- Risky side effects require approval.
- Rollback handlers are registered per tool type.
- Budgets are enforced before LLM/tool execution.

### Layer 8: Observability And Scale

Current gaps:
- Metrics are sparse.
- Celery queues are not production-ready.
- No durable checkpoints or restart recovery.
- No queue depth autoscaling or DLQ.

Target state:
- Full trace: API -> planner -> tool call -> external system -> verifier.
- Metrics: queue depth, goal duration, tool latency, success/failure, cost, tokens, approval wait time.
- Durable checkpoints and retry/recovery for all executions.

### Layer 9: Frontend UX

Current gaps:
- Goal form cannot select agent.
- Agent creation cannot bind connectors/knowledge/policies in a guided way.
- Execution timeline lacks rich tool/action rendering.
- Schedules lack agent picker and run history.

Target state:
- Guided agent builder.
- Goal form with agent picker, auto-route option, dry-run side-effect preview.
- Timeline showing plan, tool calls, artifacts, approvals, retries, verification.

### Layer 10: Tests Without Mocking

Current gaps:
- Many tests use `FakeProvider`, `AsyncMock`, fake Redis, and in-memory stores.
- E2E tests often use ASGI transport instead of real workers/services.

Target state:
- Real Postgres, Redis, Celery worker, MCP mock server, browser/RPA sandbox, and frontend E2E coverage.

---

## Phase Roadmap

| Phase | Goal | Exit Criteria |
|---|---|---|
| 1 | Goal-to-agent binding | User can submit goal with `agent_id`; backend stores and returns it. |
| 2 | Agent-to-connector tool context | Backend loads selected agent, discovers tools from its connectors, and exposes tool context. |
| 3 | MCP JSON-RPC runtime `tools/list` and `tools/call` | Runtime client can list and call Atlassian MCP tools, not just test initialize. |
| 4 | Structured planner/executor tool calls | Planner sees schemas; executor emits validated tool calls; results appear in events. |
| 5 | Frontend agent picker and execution timeline | User can choose agent; goal detail shows plan/tool/approval/result timeline. |
| 6 | Real Jira read-only E2E | Jira read-only goal fetches real Jira data through MCP and renders result. |
| 7 | Governance-enforced Jira comments/updates | Comments allowed; updates require approval; destructive actions denied. |
| 8 | Agent-bound schedules | Schedules submit agent-bound goals and show run history. |
| 9 | Durable Celery worker execution | API enqueues jobs; workers execute; jobs survive API restart. |
| 10 | Persistent state, checkpointing, restart recovery | Goals, steps, events, checkpoints, artifacts, and approvals recover after restart. |
| 11 | Multi-agent workflows | Complex Jira -> Confluence -> Email workflows execute through multiple agents/connectors. |
| 12 | Production observability/security/scale hardening | Metrics, tracing, secrets, autoscaling, DLQ, rate limits, and prod readiness complete. |
| 13 | RPA automation E2E | Browser/RPA agent executes audited UI workflows with screenshots, replay, and fallback. |

---

## Phase 1: Goal-To-Agent Binding

**Files:**
- Modify: `agent-verse-backend/app/api/goals.py`
- Modify: `agent-verse-backend/app/services/goal_service.py`
- Modify: `agent-verse-backend/app/db/models/goal.py`
- Create: `agent-verse-backend/app/db/migrations/versions/0010_goal_agent_binding.py`
- Test: `agent-verse-backend/tests/api/test_goals.py`
- Test: `agent-verse-backend/tests/services/test_goal_service.py`

- [ ] **Step 1: Add failing API test for `agent_id`**

Add to `tests/api/test_goals.py`:

```python
def test_submit_goal_accepts_agent_id() -> None:
    svc = AsyncMock()
    svc.submit_goal.return_value = {
        "goal_id": "gid-1",
        "status": "planning",
        "goal": "triage Jira",
        "agent_id": "agent-1",
        "workflow_mode": "single_agent",
    }
    client = TestClient(_make_app(svc), raise_server_exceptions=False)

    resp = client.post(
        "/goals",
        json={"goal": "triage Jira", "agent_id": "agent-1"},
        headers={"X-API-Key": _VALID_KEY},
    )

    assert resp.status_code == 202
    assert resp.json()["agent_id"] == "agent-1"
    assert svc.submit_goal.call_args.kwargs["agent_id"] == "agent-1"
```

- [ ] **Step 2: Run failing test**

Run:

```bash
cd agent-verse-backend
uv run pytest tests/api/test_goals.py::test_submit_goal_accepts_agent_id
```

Expected: fail because request/service do not accept `agent_id`.

- [ ] **Step 3: Extend goal API request**

Modify `app/api/goals.py`:

```python
class GoalRequest(BaseModel):
    goal: str = Field(..., min_length=1)
    priority: str = "normal"
    dry_run: bool = False
    agent_id: str | None = None
    workflow_mode: str = "single_agent"
```

Update `submit_goal()`:

```python
result: dict[str, Any] = await svc.submit_goal(
    goal=body.goal,
    priority=body.priority,
    dry_run=body.dry_run,
    tenant_ctx=tenant,
    agent_id=body.agent_id,
    workflow_mode=body.workflow_mode,
)
```

- [ ] **Step 4: Extend `GoalRecord`**

Modify `app/services/goal_service.py`:

```python
@dataclass
class GoalRecord:
    goal_id: str
    goal_text: str
    status: GoalStatus
    tenant_id: str
    priority: str
    dry_run: bool
    created_at: str
    agent_id: str | None = None
    workflow_mode: str = "single_agent"
    execution_context: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
    subscribers: list[asyncio.Queue[dict[str, Any] | None]] = field(default_factory=list)
```

- [ ] **Step 5: Extend `submit_goal()` signature and responses**

Modify `GoalService.submit_goal()`:

```python
async def submit_goal(
    self,
    *,
    goal: str,
    priority: str,
    dry_run: bool,
    tenant_ctx: TenantContext,
    agent_id: str | None = None,
    workflow_mode: str = "single_agent",
) -> dict[str, Any]:
```

Set fields:

```python
agent_id=agent_id,
workflow_mode=workflow_mode,
```

Return fields in submit/get/list:

```python
"agent_id": record.agent_id,
"workflow_mode": record.workflow_mode,
```

- [ ] **Step 6: Add DB migration**

Create `app/db/migrations/versions/0010_goal_agent_binding.py`:

```python
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("goals", sa.Column("agent_id", sa.String(36), nullable=True))
    op.add_column("goals", sa.Column("workflow_mode", sa.String(40), nullable=False, server_default="single_agent"))
    op.add_column("goals", sa.Column("execution_context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_index("ix_goals_tenant_agent", "goals", ["tenant_id", "agent_id"])


def downgrade() -> None:
    op.drop_index("ix_goals_tenant_agent", table_name="goals")
    op.drop_column("goals", "execution_context")
    op.drop_column("goals", "workflow_mode")
    op.drop_column("goals", "agent_id")
```

- [ ] **Step 7: Run tests**

Run:

```bash
uv run pytest tests/api/test_goals.py tests/services/test_goal_service.py tests/db/test_migrations.py
```

Expected: all pass.

---

## Phase 2: Agent-To-Connector Tool Context

**Files:**
- Create: `agent-verse-backend/app/agent/tool_context.py`
- Modify: `agent-verse-backend/app/services/goal_service.py`
- Modify: `agent-verse-backend/app/api/agents.py`
- Test: `agent-verse-backend/tests/agent/test_tool_context.py`
- Test: `agent-verse-backend/tests/services/test_goal_service.py`

- [ ] **Step 1: Create tool context test**

Create `tests/agent/test_tool_context.py`:

```python
from app.agent.tool_context import ToolContext, ToolRef


def test_tool_context_formats_tools_for_planner_prompt() -> None:
    context = ToolContext(
        connectors=[{"server_id": "jira-1", "name": "JIRA"}],
        tools=[
            ToolRef(
                server_id="jira-1",
                server_name="JIRA",
                name="jira_search",
                description="Search Jira issues with JQL",
                input_schema={"type": "object"},
            )
        ],
    )

    prompt = context.to_prompt_block()

    assert "JIRA" in prompt
    assert "jira_search" in prompt
    assert "Search Jira issues with JQL" in prompt
```

- [ ] **Step 2: Implement `tool_context.py`**

Create `app/agent/tool_context.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolRef:
    server_id: str
    server_name: str
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolContext:
    connectors: list[dict[str, Any]] = field(default_factory=list)
    tools: list[ToolRef] = field(default_factory=list)

    def to_prompt_block(self) -> str:
        if not self.tools:
            return "[Available tools]\nNo external tools are available."
        lines = ["[Available tools]"]
        for tool in self.tools:
            lines.append(
                f"- {tool.server_name}.{tool.name} "
                f"(server_id={tool.server_id}): {tool.description}; "
                f"schema={tool.input_schema}"
            )
        return "\n".join(lines)

    def find_tool(self, name: str) -> ToolRef | None:
        target = name.lower()
        for tool in self.tools:
            if tool.name.lower() == target:
                return tool
            if f"{tool.server_name}.{tool.name}".lower() == target:
                return tool
        return None
```

- [ ] **Step 3: Add `AgentStore.require()` helper**

Modify `app/api/agents.py`:

```python
def require(self, agent_id: str, *, tenant_ctx: TenantContext) -> dict[str, Any]:
    agent = self.get(agent_id, tenant_ctx=tenant_ctx)
    if agent is None:
        raise KeyError(agent_id)
    return agent
```

- [ ] **Step 4: Build tool context in `GoalService`**

Add to `app/services/goal_service.py`:

```python
async def _build_tool_context(
    self,
    *,
    agent_id: str | None,
    tenant_ctx: TenantContext,
) -> Any:
    from app.agent.tool_context import ToolContext, ToolRef

    if not agent_id or self._app_state is None:
        return ToolContext()

    agent_store = getattr(self._app_state, "agent_store", None)
    mcp_client = getattr(self._app_state, "mcp_client", None)
    if agent_store is None or mcp_client is None:
        return ToolContext()

    agent = agent_store.get(agent_id, tenant_ctx=tenant_ctx)
    if agent is None:
        return ToolContext()

    tools: list[ToolRef] = []
    for connector_id in agent.get("connector_ids", []):
        discovered = await mcp_client.discover_tools(server_id=connector_id, tenant_ctx=tenant_ctx)
        for tool in discovered:
            tools.append(
                ToolRef(
                    server_id=tool.server_id,
                    server_name=tool.server_name,
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.input_schema,
                )
            )
    return ToolContext(connectors=[agent], tools=tools)
```

- [ ] **Step 5: Run tests**

Run:

```bash
uv run pytest tests/agent/test_tool_context.py tests/services/test_goal_service.py
```

Expected: all pass.

---

## Phase 3: MCP JSON-RPC Runtime `tools/list` And `tools/call`

**Files:**
- Modify: `agent-verse-backend/app/mcp/client.py`
- Test: `agent-verse-backend/tests/mcp/test_mcp_client.py`

- [ ] **Step 1: Add tests for JSON-RPC tools/list and tools/call**

Add tests that assert MCP endpoint URLs use POST JSON-RPC:

```python
async def test_mcp_discover_tools_uses_jsonrpc_tools_list(monkeypatch):
    seen = {}
    class Response:
        def raise_for_status(self):
            return None
        def json(self):
            return {"jsonrpc": "2.0", "result": {"tools": [{"name": "jira_search", "description": "Search Jira", "inputSchema": {}}]}}
    class Client:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            seen["json"] = kwargs["json"]
            return Response()
    monkeypatch.setattr("httpx.AsyncClient", Client)
```

- [ ] **Step 2: Implement `_is_mcp_endpoint()` and `_jsonrpc()`**

Modify `app/mcp/client.py`:

```python
import uuid


def _is_mcp_endpoint(url: str) -> bool:
    return url.rstrip("/").endswith("/mcp")


def _jsonrpc(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params or {}}
```

- [ ] **Step 3: Update `discover_tools()` for MCP JSON-RPC**

Use:

```python
if _is_mcp_endpoint(cfg.url):
    headers = {**headers, "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    resp = await client.post(cfg.url.rstrip("/"), json=_jsonrpc("tools/list"), headers=headers)
else:
    resp = await client.get(f"{cfg.url.rstrip('/')}/tools", headers=headers)
```

Parse both response formats:

```python
data = resp.json()
tools = data.get("result", {}).get("tools", []) if "result" in data else data.get("tools", data if isinstance(data, list) else [])
```

- [ ] **Step 4: Update `call_tool()` for MCP JSON-RPC**

Use:

```python
if _is_mcp_endpoint(cfg.url):
    headers = {**headers, "Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    resp = await client.post(
        cfg.url.rstrip("/"),
        json=_jsonrpc("tools/call", {"name": tool_name, "arguments": arguments}),
        headers=headers,
    )
else:
    resp = await client.post(f"{cfg.url.rstrip('/')}/tools/{tool_name}", json={"arguments": arguments}, headers=headers)
payload = resp.json()
output = payload.get("result", payload)
```

- [ ] **Step 5: Run MCP tests**

Run:

```bash
uv run pytest tests/mcp/test_mcp_client.py
```

Expected: all pass.

---

## Phase 4: Structured Planner/Executor Tool Calls

**Files:**
- Create: `agent-verse-backend/app/agent/tool_calls.py`
- Modify: `agent-verse-backend/app/agent/graph.py`
- Test: `agent-verse-backend/tests/agent/test_tool_calls.py`
- Test: `agent-verse-backend/tests/agent/test_graph_coverage.py`

- [ ] **Step 1: Create structured tool call parser**

Create `app/agent/tool_calls.py`:

```python
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)


def extract_tool_call(text: str) -> ToolCall | None:
    cleaned = re.sub(r"```(?:json)?\n?", "", text).strip()
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    tool = obj.get("tool") or obj.get("tool_name")
    if not tool:
        return None
    args = obj.get("arguments") or {}
    return ToolCall(tool=str(tool), arguments=args if isinstance(args, dict) else {})
```

- [ ] **Step 2: Inject tool prompt into planner**

Modify `AgentGraph._node_plan()`:

```python
tool_prompt = agent_state.context.get("tool_prompt", "")
if tool_prompt:
    extra_parts.append(tool_prompt)
```

- [ ] **Step 3: Execute parsed tool calls**

In `AgentGraph._execute_step()`, after executor response:

```python
from app.agent.tool_calls import extract_tool_call

tool_call = extract_tool_call(raw_output)
if tool_call is not None:
    tool_context = agent_state.context.get("tool_context")
    mcp_client = agent_state.context.get("mcp_client")
    if tool_context is not None and mcp_client is not None:
        tool_ref = tool_context.find_tool(tool_call.tool)
        if tool_ref is not None:
            result = await mcp_client.call_tool(
                server_id=tool_ref.server_id,
                tool_name=tool_ref.name,
                arguments=tool_call.arguments,
                tenant_ctx=tenant_ctx,
            )
            await self._emit({"type": "tool_call_complete", "tool": tool_ref.name, "server_id": tool_ref.server_id, "success": result.success, "output": result.output, "error": result.error})
            raw_output = str(result.output if result.success else result.error)
```

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest tests/agent/test_tool_calls.py tests/agent/test_graph_coverage.py
```

Expected: all pass.

---

## Phase 5: Frontend Agent Picker And Execution Timeline

**Files:**
- Modify: `agent-verse-frontend/src/lib/api/client.ts`
- Modify: `agent-verse-frontend/src/features/goals/GoalsListPage.tsx`
- Modify: `agent-verse-frontend/src/features/goals/GoalDetailPage.tsx`

- [ ] **Step 1: Extend goal request type**

Modify `src/lib/api/client.ts`:

```ts
export interface GoalRequest {
  goal: string;
  priority?: string;
  dry_run?: boolean;
  agent_id?: string;
  workflow_mode?: string;
}
```

- [ ] **Step 2: Add agent dropdown to goal form**

In `GoalsListPage.tsx`, fetch agents and add:

```tsx
<select value={selectedAgentId} onChange={(e) => setSelectedAgentId(e.target.value)}>
  <option value="auto">Auto-select best agent</option>
  {agents.map((agent) => (
    <option key={agent.agent_id} value={agent.agent_id}>{agent.name}</option>
  ))}
</select>
```

Submit:

```ts
goalsApi.submit({
  goal,
  dry_run: dryRun,
  agent_id: selectedAgentId === 'auto' ? undefined : selectedAgentId,
  workflow_mode: selectedAgentId === 'auto' ? 'auto_route' : 'single_agent',
})
```

- [ ] **Step 3: Render tool call events**

In `GoalDetailPage.tsx`, handle `tool_call_complete`:

```tsx
if (event.type === 'tool_call_complete') {
  return <span>{String(event.tool)} · {String(event.success)}</span>;
}
```

- [ ] **Step 4: Run frontend checks**

Run:

```bash
cd agent-verse-frontend
npm run typecheck
npm run test -- src/app/App.test.tsx src/features/auth/AuthPage.test.tsx
```

Expected: pass.

---

## Phase 6: Real Jira Read-Only E2E

**Files:**
- Create: `agent-verse-backend/tests/e2e/test_jira_agent_execution.py`

- [ ] **Step 1: Create mock MCP JSON-RPC E2E server with `respx`**

Use a mock endpoint for CI:

```python
respx_mock.post("https://mock-jira-mcp.local/mcp").mock(
    side_effect=[
        httpx.Response(200, json={"jsonrpc": "2.0", "result": {"tools": [{"name": "jira_search", "description": "Search Jira", "inputSchema": {}}]}}),
        httpx.Response(200, json={"jsonrpc": "2.0", "result": {"issues": [{"key": "BAU-1", "summary": "Open issue"}]}}),
    ]
)
```

- [ ] **Step 2: Add real opt-in Jira smoke test marker**

Add a separate test guarded by env vars:

```python
@pytest.mark.slow
@pytest.mark.integration
async def test_real_atlassian_mcp_read_only_smoke():
    token = os.getenv("ATLASSIAN_MCP_BASIC_TOKEN")
    if not token:
        pytest.skip("ATLASSIAN_MCP_BASIC_TOKEN not set")
```

- [ ] **Step 3: Run E2E tests**

Run:

```bash
uv run pytest tests/e2e/test_jira_agent_execution.py
```

Expected: mock E2E passes; real Jira smoke is skipped unless env is configured.

---

## Phase 7: Governance-Enforced Jira Comments/Updates

**Files:**
- Modify: `agent-verse-backend/app/agent/graph.py`
- Modify: `agent-verse-backend/app/governance/policies.py`
- Modify: `agent-verse-backend/app/api/governance.py`
- Test: `agent-verse-backend/tests/api/test_governance_api.py`
- Test: `agent-verse-backend/tests/e2e/test_jira_agent_execution.py`

- [ ] **Step 1: Classify Jira tools**

Create tool action mapping:

```python
JIRA_TOOL_RISK = {
    "jira_search": "read",
    "jira_get_issue": "read",
    "jira_add_comment": "write_low",
    "jira_update_issue": "write_high",
    "jira_assign_issue": "write_high",
    "jira_transition_issue": "write_high",
    "jira_delete_issue": "destructive",
}
```

- [ ] **Step 2: Deny destructive actions**

Before tool call execution:

```python
if risk == "destructive":
    raise PermissionError(f"Tool {tool_ref.name} is denied by governance policy")
```

- [ ] **Step 3: Create approval for high-risk writes**

```python
if risk == "write_high" and self._hitl_gateway is not None:
    self._hitl_gateway.request_approval(goal_id=agent_state.goal_id, action=tool_ref.name, risk_level="high", tenant_ctx=tenant_ctx)
    await self._emit({"type": "waiting_approval", "tool": tool_ref.name})
    return "Waiting for approval"
```

- [ ] **Step 4: Run governance tests**

Run:

```bash
uv run pytest tests/api/test_governance_api.py tests/e2e/test_jira_agent_execution.py
```

Expected: comment actions can run, field updates create approval, destructive actions fail.

---

## Phase 8: Agent-Bound Schedules

**Files:**
- Modify: `agent-verse-backend/app/api/schedules.py`
- Modify: `agent-verse-backend/app/triggers/store.py`
- Modify: `agent-verse-backend/app/scaling/tasks.py`
- Modify: `agent-verse-frontend/src/features/schedules/SchedulesPage.tsx`
- Test: `agent-verse-backend/tests/api/test_schedules_api.py`

- [ ] **Step 1: Persist `agent_id` in schedules**

Schedule record must contain:

```python
{
    "schedule_id": schedule_id,
    "tenant_id": tenant_ctx.tenant_id,
    "agent_id": agent_id,
    "goal_template": goal_template,
    "spec": spec,
    "paused": False,
}
```

- [ ] **Step 2: Fire due schedules into `GoalService.submit_goal()`**

Worker call:

```python
await goal_service.submit_goal(
    goal=schedule["goal_template"],
    priority="normal",
    dry_run=False,
    tenant_ctx=tenant_ctx,
    agent_id=schedule["agent_id"],
    workflow_mode="single_agent",
)
```

- [ ] **Step 3: Add schedule UI agent picker**

Frontend schedule form must choose agent from `/agents`.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/api/test_schedules_api.py
cd agent-verse-frontend
npm run typecheck
```

Expected: pass.

---

## Phase 9: Durable Celery Worker Execution

**Files:**
- Modify: `agent-verse-backend/app/api/goals.py`
- Modify: `agent-verse-backend/app/services/goal_service.py`
- Modify: `agent-verse-backend/app/scaling/tasks.py`
- Modify: `agent-verse-backend/app/scaling/celery_app.py`
- Test: `agent-verse-backend/tests/e2e/test_goal_worker_execution.py`

- [ ] **Step 1: Replace local `asyncio.create_task` with Celery job in production path**

Goal submission should persist goal, then enqueue:

```python
run_goal.delay(goal_id=goal_id, tenant_id=tenant_ctx.tenant_id)
```

Keep local async execution only for explicit test mode.

- [ ] **Step 2: Worker loads goal from DB**

In `app/scaling/tasks.py`, worker loads `Goal` by `goal_id` and tenant context, runs `GoalService` execution, persists state/events.

- [ ] **Step 3: Add worker E2E test with eager Celery and DB**

Test should verify:

```text
POST /goals -> persisted goal -> worker executes -> GET /goals/{id} shows terminal status
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/e2e/test_goal_worker_execution.py
```

Expected: pass.

---

## Phase 10: Persistent State, Checkpointing, Restart Recovery

**Files:**
- Create: `agent-verse-backend/app/services/event_store.py`
- Create: `agent-verse-backend/app/services/checkpoint_store.py`
- Modify: `agent-verse-backend/app/services/goal_service.py`
- Modify: `agent-verse-backend/app/db/models/goal.py`
- Create migration: `0011_goal_events_checkpoints.py`

- [ ] **Step 1: Add DB tables**

Migration creates:

```text
goal_events(goal_id, tenant_id, sequence, event_type, payload, created_at)
goal_checkpoints(goal_id, tenant_id, checkpoint_key, payload, created_at)
goal_artifacts(goal_id, tenant_id, artifact_type, uri, metadata, created_at)
```

- [ ] **Step 2: Persist every event**

In `_dispatch_event()`:

```python
await self._event_store.append(goal_id=goal_id, tenant_id=record.tenant_id, event=event)
```

- [ ] **Step 3: Replay events from DB**

`get_events()` should return DB events if process memory is empty.

- [ ] **Step 4: Resume goals after restart**

Startup sync should enqueue jobs for goals in `planning`, `executing`, or `waiting_human` unless already leased.

- [ ] **Step 5: Add restart recovery E2E**

Test:

```text
create goal -> simulate service restart -> sync_from_db -> events and status recover
```

---

## Phase 11: Multi-Agent Workflows

**Files:**
- Create: `agent-verse-backend/app/agent/workflow_planner.py`
- Create: `agent-verse-backend/app/agent/workflow_executor.py`
- Modify: `agent-verse-backend/app/services/goal_service.py`
- Test: `agent-verse-backend/tests/agent/test_workflow_planner.py`
- Test: `agent-verse-backend/tests/e2e/test_multi_agent_workflow.py`

- [ ] **Step 1: Define workflow models**

Create:

```python
@dataclass(frozen=True)
class WorkflowStep:
    step_id: str
    agent_id: str | None
    connector_name: str | None
    intent: str
    input_from: list[str]
    requires_approval: bool = False
```

- [ ] **Step 2: Implement deterministic first-pass workflow planner**

For text containing Jira, Confluence, Email:

```text
Jira fetch -> Confluence create page -> Email send
```

- [ ] **Step 3: Implement workflow executor**

Executor runs steps in dependency order and passes outputs:

```python
outputs[step.step_id] = await execute_step(step, inputs)
```

- [ ] **Step 4: Add E2E test**

Complex goal:

```text
Fetch open Jira issues, create Confluence page, email summary.
```

Expected events:

```text
workflow_planned
workflow_step_started: jira
workflow_step_complete: jira
workflow_step_started: confluence
workflow_step_complete: confluence
workflow_step_started: email
workflow_step_complete: email
goal_complete
```

---

## Phase 12: Production Observability, Security, Scale Hardening

**Files:**
- Modify: `agent-verse-backend/app/observability/metrics.py`
- Modify: `agent-verse-backend/app/observability/tracing.py`
- Modify: `agent-verse-backend/app/providers/vault.py`
- Modify: `agent-verse-backend/infra/k8s/*.yaml`
- Modify: `agent-verse-backend/infra/prometheus/*.yml`
- Modify: `agent-verse-backend/infra/grafana/**/*.json`

- [ ] **Step 1: Add core metrics**

Metrics required:

```text
agentverse_goal_duration_seconds
agentverse_goal_total{status,agent_id,tenant_id}
agentverse_tool_call_duration_seconds{tool,connector,status}
agentverse_tool_call_total{tool,connector,status}
agentverse_queue_depth{queue}
agentverse_approval_wait_seconds
agentverse_llm_tokens_total{provider,model,type}
agentverse_cost_usd_total{tenant_id,agent_id}
```

- [ ] **Step 2: Add trace spans**

Trace spans:

```text
goal.submit
goal.execute
planner.call
tool.discover
tool.call
governance.check
approval.wait
verifier.call
```

- [ ] **Step 3: Move connector secrets to vault**

Connector registry must store secret references, not raw credentials:

```json
{
  "auth_config": {
    "username": "user@example.com",
    "password_secret_ref": "vault://tenant/connector/password"
  }
}
```

- [ ] **Step 4: Add K8s production resources**

Add:

```text
migration Job
worker Deployment
beat Deployment
Redis StatefulSet or managed Redis docs
Postgres StatefulSet or managed Postgres docs
HPA based on queue depth
PDBs
NetworkPolicy
sealed/external secrets integration
```

- [ ] **Step 5: Add DLQ and retries**

Celery config:

```python
task_routes = {
    "app.scaling.tasks.run_goal": {"queue": "goals"},
    "app.scaling.tasks.fire_due_schedules": {"queue": "schedules"},
}
task_acks_late = True
task_reject_on_worker_lost = True
task_default_retry_delay = 30
```

- [ ] **Step 6: Add production dashboards**

Dashboards must show:

```text
Goal success rate
Goal latency p50/p95/p99
Tool call failure rate
Connector auth failures
Approval wait time
Queue depth
Worker health
Tenant cost
Schedule fire count
```

---

## Phase 13: RPA Automation E2E

**Files:**
- Modify: `agent-verse-backend/app/perception/browser_agent.py`
- Create: `agent-verse-backend/app/rpa/session.py`
- Create: `agent-verse-backend/app/rpa/tools.py`
- Create: `agent-verse-backend/app/rpa/artifacts.py`
- Create: `agent-verse-backend/app/api/rpa.py`
- Test: `agent-verse-backend/tests/rpa/test_rpa_tools.py`
- Test: `agent-verse-backend/tests/e2e/test_rpa_workflow.py`

- [ ] **Step 1: Define RPA session model**

Create `app/rpa/session.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class RPASession:
    session_id: str
    tenant_id: str
    goal_id: str
    status: str = "created"
    current_url: str = ""
    screenshots: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
```

- [ ] **Step 2: Expose RPA tools through MCP-compatible tool definitions**

Create `app/rpa/tools.py`:

```python
RPA_TOOLS = [
    {"name": "rpa_open_url", "description": "Open a URL in a browser session", "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "rpa_click", "description": "Click an element by selector or text", "input_schema": {"type": "object", "properties": {"selector": {"type": "string"}, "text": {"type": "string"}}}},
    {"name": "rpa_type", "description": "Type text into an element", "input_schema": {"type": "object", "properties": {"selector": {"type": "string"}, "text": {"type": "string"}}, "required": ["selector", "text"]}},
    {"name": "rpa_extract_text", "description": "Extract page text", "input_schema": {"type": "object", "properties": {"selector": {"type": "string"}}}},
    {"name": "rpa_screenshot", "description": "Capture a screenshot artifact", "input_schema": {"type": "object", "properties": {}}},
]
```

- [ ] **Step 3: Implement artifact store**

Create `app/rpa/artifacts.py`:

```python
from __future__ import annotations

from pathlib import Path


class RPAArtifactStore:
    def __init__(self, root: Path = Path("/tmp/agentverse-rpa")) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def write_bytes(self, *, goal_id: str, name: str, data: bytes) -> str:
        goal_dir = self.root / goal_id
        goal_dir.mkdir(parents=True, exist_ok=True)
        path = goal_dir / name
        path.write_bytes(data)
        return str(path)
```

- [ ] **Step 4: Wire Playwright browser agent as tool executor**

Modify `app/perception/browser_agent.py` so each RPA tool returns structured result:

```python
{
  "success": True,
  "output": "...",
  "artifact_uri": "...",
  "current_url": "..."
}
```

- [ ] **Step 5: Add RPA governance classification**

Policy defaults:

```text
rpa_extract_text = read
rpa_screenshot = read
rpa_open_url = low
rpa_click = write_high
rpa_type = write_high
rpa_submit = approval_required
```

- [ ] **Step 6: Add RPA E2E with local test page**

Test scenario:

```text
Open local form page
Type into search field
Click submit
Extract result text
Capture screenshot
Persist artifacts
Render events
```

Expected events:

```text
rpa_session_created
tool_call_complete rpa_open_url
tool_call_complete rpa_type
tool_call_complete rpa_click
tool_call_complete rpa_extract_text
artifact_created screenshot
goal_complete
```

- [ ] **Step 7: Add human takeover hooks**

RPA sessions must support:

```text
pause
resume
takeover_url
cancel
```

- [ ] **Step 8: Run RPA tests**

Run:

```bash
uv run pytest tests/rpa/test_rpa_tools.py tests/e2e/test_rpa_workflow.py
```

Expected: all pass in CI with local browser dependencies installed.

---

## Final Production Acceptance Criteria

The platform is considered world-class only when all of these are true:

- [ ] User can create an agent with selected connectors, knowledge, autonomy mode, and policies.
- [ ] User can submit a goal with explicit agent or auto-route.
- [ ] Planner receives real discovered tool schemas.
- [ ] Executor emits structured tool calls.
- [ ] MCP runtime executes real `tools/list` and `tools/call`.
- [ ] Jira read-only E2E fetches real or mock Jira issues without fake provider success.
- [ ] Jira comments execute only when governance allows them.
- [ ] Jira field updates require approval.
- [ ] Schedules submit durable agent-bound goals.
- [ ] Celery workers execute goals outside the API process.
- [ ] Goal events, steps, artifacts, approvals, and checkpoints survive restart.
- [ ] Multi-agent Jira -> Confluence -> Email workflow executes end-to-end.
- [ ] RPA browser automation executes local E2E with screenshots and artifacts.
- [ ] Connector secrets are never returned in API responses or logs.
- [ ] Metrics/traces prove what happened for every goal.
- [ ] Tests include real Postgres, Redis, worker, MCP mock server, and browser E2E.

## Full Verification Suite

Run before production acceptance:

```bash
cd agent-verse-backend
uv run pytest \
  tests/api/test_goals.py \
  tests/services/test_goal_service.py \
  tests/api/test_agents.py \
  tests/api/test_connectors.py \
  tests/mcp/test_mcp_client.py \
  tests/agent/test_tool_context.py \
  tests/agent/test_tool_calls.py \
  tests/agent/test_router.py \
  tests/agent/test_workflow_planner.py \
  tests/api/test_schedules_api.py \
  tests/e2e/test_jira_agent_execution.py \
  tests/e2e/test_multi_agent_workflow.py \
  tests/e2e/test_goal_worker_execution.py \
  tests/e2e/test_rpa_workflow.py
```

```bash
cd agent-verse-frontend
npm run typecheck
npm run test
npm run test:e2e
```

## Execution Recommendation

Do not implement all phases in one session. Execute in this order:

1. Phases 1-4: make single-agent Jira tool execution real.
2. Phases 5-6: make it usable and prove Jira read-only E2E.
3. Phases 7-8: add governance and schedules.
4. Phases 9-10: make execution durable and restart-safe.
5. Phase 11: add multi-agent workflows.
6. Phase 12: production hardening.
7. Phase 13: RPA E2E automation.

Each phase should be merged only after its tests and manual verification pass.
