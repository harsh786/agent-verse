# World-Class Agent Platform Missing Glue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the missing execution glue that turns AgentVerse from separate agents, connectors, goals, and schedules into a coherent world-class autonomous agent platform.

**Architecture:** Goals must bind to agents, agents must bind to connectors, planners must see available tools, executors must call MCP tools, schedules must trigger agent-bound goals, and the UI must make these relationships visible. The implementation is phased so each slice is testable and safe: explicit agent selection first, then auto-routing, then multi-agent workflows and full scheduling.

**Tech Stack:** FastAPI, SQLAlchemy async, PostgreSQL RLS, Redis MCP registry, Atlassian MCP JSON-RPC, React/Vite, TanStack Query, Zustand, pytest, Vitest.

---

## Scope Check

This plan covers multiple connected subsystems, but they are not independent products. They are the missing glue for one platform capability: goal execution through selected or auto-routed agents and their connectors. Tasks are ordered to produce working software after each phase.

## Core Answers This Plan Implements

1. **How does a goal know which agent to call?** The goal request gets an optional `agent_id`. If absent, an agent router selects the best matching agent. If routing confidence is low, the UI asks the user to choose.
2. **How does an agent connect to a connector?** Agent records own `connector_ids`. At execution time, the backend loads those connectors, discovers tools, and exposes those tools to the planner/executor.
3. **How do complex goals work?** A workflow planner decomposes the goal into steps, maps each step to a connector/tool, executes in sequence or parallel, and verifies the final result.
4. **How does scheduled autonomy work?** Schedules store `agent_id` plus a goal template. The scheduler submits a normal goal with that agent ID when triggered.

## File Structure

### Backend Files To Modify

- `agent-verse-backend/app/api/goals.py`  
  Add `agent_id`, `workflow_mode`, and list support to the public goal API.

- `agent-verse-backend/app/services/goal_service.py`  
  Load selected agent config, pass connector context into execution, persist/list goal metadata.

- `agent-verse-backend/app/api/agents.py`  
  Extend `AgentStore` with stable lookup helpers and optional DB-backed interface.

- `agent-verse-backend/app/db/models/agent.py`  
  Confirm agent model stores `connector_ids`, `autonomy_mode`, `trigger_config`, and permissions.

- `agent-verse-backend/app/db/models/goal.py`  
  Add `agent_id` and execution metadata to goals if absent.

- `agent-verse-backend/app/db/migrations/versions/0010_goal_agent_binding.py`  
  Persist `agent_id`, `workflow_mode`, `execution_context`, and indexes.

- `agent-verse-backend/app/mcp/client.py`  
  Add JSON-RPC MCP support for Atlassian remote MCP: initialize, tools/list, tools/call.

- `agent-verse-backend/app/agent/tool_context.py`  
  New file. Represents discovered tools and available connector metadata for a run.

- `agent-verse-backend/app/agent/router.py`  
  New file. Selects an agent for a goal when `agent_id` is not provided.

- `agent-verse-backend/app/agent/workflow_planner.py`  
  New file. Converts a complex goal into connector/tool execution steps.

- `agent-verse-backend/app/agent/graph.py`  
  Inject tool context into planner prompts and execute structured tool calls.

- `agent-verse-backend/app/api/schedules.py`  
  Add `agent_id` to schedule creation and generated schedule records.

- `agent-verse-backend/app/scaling/tasks.py`  
  Fire due schedules by submitting agent-bound goals.

### Backend Tests To Modify/Create

- `agent-verse-backend/tests/api/test_goals.py`
- `agent-verse-backend/tests/services/test_goal_service.py`
- `agent-verse-backend/tests/api/test_agents.py`
- `agent-verse-backend/tests/mcp/test_mcp_client.py`
- `agent-verse-backend/tests/agent/test_router.py`
- `agent-verse-backend/tests/agent/test_workflow_planner.py`
- `agent-verse-backend/tests/api/test_schedules_api.py`
- `agent-verse-backend/tests/e2e/test_jira_agent_execution.py`

### Frontend Files To Modify

- `agent-verse-frontend/src/features/goals/GoalsListPage.tsx`  
  Add agent dropdown and auto-select option.

- `agent-verse-frontend/src/features/goals/GoalDetailPage.tsx`  
  Show selected agent, connector/tool events, and workflow step output.

- `agent-verse-frontend/src/features/agents/AgentsListPage.tsx`  
  Show connector names and agent readiness state.

- `agent-verse-frontend/src/features/schedules/SchedulesPage.tsx`  
  Add agent selection for schedules.

- `agent-verse-frontend/src/lib/api/client.ts`  
  Add typed request/response shapes for `agent_id`, routing, and workflow events.

---

### Task 1: Add Goal-To-Agent Binding To The API

**Files:**
- Modify: `agent-verse-backend/app/api/goals.py`
- Modify: `agent-verse-backend/app/services/goal_service.py`
- Test: `agent-verse-backend/tests/api/test_goals.py`
- Test: `agent-verse-backend/tests/services/test_goal_service.py`

- [ ] **Step 1: Write failing API test for explicit `agent_id`**

Add to `agent-verse-backend/tests/api/test_goals.py`:

```python
def test_submit_goal_accepts_agent_id() -> None:
    svc = AsyncMock()
    svc.submit_goal.return_value = {
        "goal_id": "gid-1",
        "status": "planning",
        "goal": "triage Jira",
        "agent_id": "agent-1",
    }
    client = TestClient(_make_app(svc), raise_server_exceptions=False)

    resp = client.post(
        "/goals",
        json={"goal": "triage Jira", "agent_id": "agent-1"},
        headers={"X-API-Key": _VALID_KEY},
    )

    assert resp.status_code == 202
    assert resp.json()["agent_id"] == "agent-1"
    svc.submit_goal.assert_called_once()
    assert svc.submit_goal.call_args.kwargs["agent_id"] == "agent-1"
```

- [ ] **Step 2: Run API test and verify failure**

Run:

```bash
cd agent-verse-backend
uv run pytest tests/api/test_goals.py::test_submit_goal_accepts_agent_id
```

Expected: fails because `GoalRequest` does not contain `agent_id` and `submit_goal()` does not receive it.

- [ ] **Step 3: Extend request model**

Modify `agent-verse-backend/app/api/goals.py`:

```python
class GoalRequest(BaseModel):
    goal: str = Field(..., min_length=1)
    priority: str = "normal"
    dry_run: bool = False
    agent_id: str | None = None
    workflow_mode: str = "single_agent"
```

Update `submit_goal()` call:

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

- [ ] **Step 4: Extend service signature and record state**

Modify `agent-verse-backend/app/services/goal_service.py`:

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
    events: list[dict[str, Any]] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
    subscribers: list[asyncio.Queue[dict[str, Any] | None]] = field(default_factory=list)
```

Update `submit_goal()` signature:

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

Set fields when creating `GoalRecord`:

```python
agent_id=agent_id,
workflow_mode=workflow_mode,
```

Include fields in response and `get_goal()`/`list_goals()`:

```python
"agent_id": record.agent_id,
"workflow_mode": record.workflow_mode,
```

- [ ] **Step 5: Run goal API and service tests**

Run:

```bash
uv run pytest tests/api/test_goals.py tests/services/test_goal_service.py
```

Expected: all tests pass.

---

### Task 2: Load Agent Configuration During Goal Execution

**Files:**
- Modify: `agent-verse-backend/app/services/goal_service.py`
- Modify: `agent-verse-backend/app/api/agents.py`
- Test: `agent-verse-backend/tests/services/test_goal_service.py`

- [ ] **Step 1: Write failing service test for selected agent config**

Add to `agent-verse-backend/tests/services/test_goal_service.py`:

```python
async def test_submit_goal_with_agent_id_stores_agent_context() -> None:
    svc = GoalService()
    created = await svc.submit_goal(
        goal="Use Jira",
        priority="normal",
        dry_run=True,
        tenant_ctx=_CTX_A,
        agent_id="agent-123",
        workflow_mode="single_agent",
    )

    fetched = await svc.get_goal(goal_id=created["goal_id"], tenant_ctx=_CTX_A)

    assert fetched["agent_id"] == "agent-123"
    assert fetched["workflow_mode"] == "single_agent"
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
uv run pytest tests/services/test_goal_service.py::test_submit_goal_with_agent_id_stores_agent_context
```

Expected: fails until Task 1 fields are implemented.

- [ ] **Step 3: Add agent store lookup helper**

Modify `agent-verse-backend/app/api/agents.py` `AgentStore`:

```python
def require(self, agent_id: str, *, tenant_ctx: TenantContext) -> dict[str, Any]:
    rec = self.get(agent_id, tenant_ctx=tenant_ctx)
    if rec is None:
        raise KeyError(agent_id)
    return rec
```

- [ ] **Step 4: Add private resolver in `GoalService`**

Modify `agent-verse-backend/app/services/goal_service.py`:

```python
def _resolve_agent_config(
    self, agent_id: str | None, tenant_ctx: TenantContext
) -> dict[str, Any] | None:
    if not agent_id or self._app_state is None:
        return None
    store = getattr(self._app_state, "agent_store", None)
    if store is None:
        return None
    return store.get(agent_id, tenant_ctx=tenant_ctx)
```

- [ ] **Step 5: Pass agent config into background execution**

Change `_run_agent_loop()` signature:

```python
async def _run_agent_loop(
    self,
    goal_id: str,
    goal_text: str,
    tenant_ctx: TenantContext,
    agent_config: dict[str, Any] | None = None,
) -> None:
```

When creating task in `submit_goal()`:

```python
agent_config = self._resolve_agent_config(agent_id, tenant_ctx)
task = asyncio.create_task(
    self._run_agent_loop(goal_id, goal, tenant_ctx, agent_config),
    name=f"goal-{goal_id}",
)
```

- [ ] **Step 6: Run tests**

Run:

```bash
uv run pytest tests/services/test_goal_service.py tests/api/test_goals.py
```

Expected: all pass.

---

### Task 3: Build Tool Context From Agent Connectors

**Files:**
- Create: `agent-verse-backend/app/agent/tool_context.py`
- Modify: `agent-verse-backend/app/services/goal_service.py`
- Test: `agent-verse-backend/tests/agent/test_tool_context.py`

- [ ] **Step 1: Create failing tool context test**

Create `agent-verse-backend/tests/agent/test_tool_context.py`:

```python
from app.agent.tool_context import ToolContext, ToolRef


def test_tool_context_formats_tools_for_prompt() -> None:
    ctx = ToolContext(
        connectors=[{"server_id": "jira-1", "name": "JIRA"}],
        tools=[
            ToolRef(
                server_id="jira-1",
                server_name="JIRA",
                name="search",
                description="Search Jira issues",
                input_schema={"type": "object"},
            )
        ],
    )

    prompt = ctx.to_prompt_block()

    assert "JIRA" in prompt
    assert "search" in prompt
    assert "Search Jira issues" in prompt
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
uv run pytest tests/agent/test_tool_context.py
```

Expected: fails because `tool_context.py` does not exist.

- [ ] **Step 3: Implement tool context**

Create `agent-verse-backend/app/agent/tool_context.py`:

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
            return "[Available tools]\nNo external tools are available for this goal."
        lines = ["[Available tools]"]
        for tool in self.tools:
            lines.append(
                f"- {tool.server_name}.{tool.name} "
                f"(server_id={tool.server_id}): {tool.description}"
            )
        return "\n".join(lines)

    def find_tool(self, name: str) -> ToolRef | None:
        normalized = name.lower()
        for tool in self.tools:
            if tool.name.lower() == normalized:
                return tool
            if f"{tool.server_name}.{tool.name}".lower() == normalized:
                return tool
        return None
```

- [ ] **Step 4: Run test**

Run:

```bash
uv run pytest tests/agent/test_tool_context.py
```

Expected: pass.

---

### Task 4: Add MCP JSON-RPC Tool Discovery And Calls

**Files:**
- Modify: `agent-verse-backend/app/mcp/client.py`
- Test: `agent-verse-backend/tests/mcp/test_mcp_client.py`

- [ ] **Step 1: Write failing test for Atlassian MCP tools/list**

Add to `agent-verse-backend/tests/mcp/test_mcp_client.py`:

```python
async def test_discover_tools_uses_jsonrpc_for_mcp_url(monkeypatch):
    seen = {}

    class Response:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return {
                "jsonrpc": "2.0",
                "result": {
                    "tools": [
                        {
                            "name": "jira_search",
                            "description": "Search Jira issues",
                            "inputSchema": {"type": "object"},
                        }
                    ]
                },
            }

    class Client:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            seen["url"] = url
            seen["json"] = kwargs["json"]
            return Response()

    monkeypatch.setattr("httpx.AsyncClient", Client)
    registry = FakeRegistry(
        server_id="srv-jira",
        config=MCPServerConfig(
            name="JIRA",
            url="https://mcp.atlassian.com/v1/mcp",
            auth_type="basic",
            auth_config={"username": "u", "password": "p"},
        ),
    )
    client = MCPClient(registry=registry)
    tools = await client.discover_tools(server_id="srv-jira", tenant_ctx=T)

    assert tools[0].name == "jira_search"
    assert seen["json"]["method"] == "tools/list"
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
uv run pytest tests/mcp/test_mcp_client.py::test_discover_tools_uses_jsonrpc_for_mcp_url
```

Expected: fails because `MCPClient.discover_tools()` currently calls REST `GET /tools`.

- [ ] **Step 3: Implement URL check and JSON-RPC payloads**

Modify `agent-verse-backend/app/mcp/client.py`:

```python
def _is_mcp_endpoint(url: str) -> bool:
    return url.rstrip("/").endswith("/mcp")


def _jsonrpc(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": method,
        "params": params or {},
    }
```

Add import:

```python
import uuid
```

- [ ] **Step 4: Update `discover_tools()`**

In `MCPClient.discover_tools()`:

```python
if _is_mcp_endpoint(cfg.url):
    headers = {
        **headers,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    resp = await client.post(
        cfg.url.rstrip("/"),
        json=_jsonrpc("tools/list"),
        headers=headers,
    )
else:
    resp = await client.get(f"{cfg.url.rstrip('/')}/tools", headers=headers)
```

Parse result:

```python
data = resp.json()
if "result" in data:
    tools = data.get("result", {}).get("tools", [])
elif isinstance(data, list):
    tools = data
else:
    tools = data.get("tools", [])
```

- [ ] **Step 5: Update `call_tool()` for JSON-RPC**

In `MCPClient.call_tool()`:

```python
if _is_mcp_endpoint(cfg.url):
    headers = {
        **headers,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    resp = await client.post(
        cfg.url.rstrip("/"),
        json=_jsonrpc("tools/call", {"name": tool_name, "arguments": arguments}),
        headers=headers,
    )
else:
    resp = await client.post(
        f"{cfg.url.rstrip('/')}/tools/{tool_name}",
        json={"arguments": arguments},
        headers=headers,
    )
```

Return `result` when JSON-RPC response contains it:

```python
payload = resp.json()
output = payload.get("result", payload)
```

- [ ] **Step 6: Run MCP tests**

Run:

```bash
uv run pytest tests/mcp/test_mcp_client.py
```

Expected: all pass.

---

### Task 5: Build Tool Context In GoalService

**Files:**
- Modify: `agent-verse-backend/app/services/goal_service.py`
- Test: `agent-verse-backend/tests/services/test_goal_service.py`

- [ ] **Step 1: Write failing test for connector tool discovery**

Add to `agent-verse-backend/tests/services/test_goal_service.py`:

```python
async def test_goal_service_builds_tool_context_from_agent_connectors() -> None:
    class Store:
        def get(self, agent_id, *, tenant_ctx):
            return {"agent_id": agent_id, "connector_ids": ["srv-jira"], "name": "jira-agent"}

    class MCPClient:
        async def discover_tools(self, *, server_id, tenant_ctx):
            assert server_id == "srv-jira"
            return [
                type(
                    "Tool",
                    (),
                    {
                        "server_id": "srv-jira",
                        "server_name": "JIRA",
                        "name": "jira_search",
                        "description": "Search Jira issues",
                        "input_schema": {},
                    },
                )()
            ]

    app_state = type("AppState", (), {"agent_store": Store(), "mcp_client": MCPClient()})()
    svc = GoalService(app_state=app_state)

    tool_context = await svc._build_tool_context(
        agent_id="agent-1",
        tenant_ctx=_CTX_A,
    )

    assert tool_context.tools[0].name == "jira_search"
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
uv run pytest tests/services/test_goal_service.py::test_goal_service_builds_tool_context_from_agent_connectors
```

Expected: fails because `_build_tool_context()` does not exist.

- [ ] **Step 3: Implement `_build_tool_context()`**

Modify `agent-verse-backend/app/services/goal_service.py`:

```python
async def _build_tool_context(
    self, *, agent_id: str | None, tenant_ctx: TenantContext
) -> ToolContext:
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

    connector_ids = list(agent.get("connector_ids", []))
    tools: list[ToolRef] = []
    for connector_id in connector_ids:
        discovered = await mcp_client.discover_tools(
            server_id=connector_id,
            tenant_ctx=tenant_ctx,
        )
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

Add import only under type checking or inside function to avoid cycles.

- [ ] **Step 4: Pass tool context into loop**

In `submit_goal()` before task creation:

```python
tool_context = await self._build_tool_context(agent_id=agent_id, tenant_ctx=tenant_ctx)
```

Pass to `_run_agent_loop()`:

```python
self._run_agent_loop(goal_id, goal, tenant_ctx, agent_config, tool_context)
```

- [ ] **Step 5: Run service tests**

Run:

```bash
uv run pytest tests/services/test_goal_service.py
```

Expected: all pass.

---

### Task 6: Inject Tool Context Into Planning

**Files:**
- Modify: `agent-verse-backend/app/agent/graph.py`
- Test: `agent-verse-backend/tests/agent/test_graph_coverage.py`

- [ ] **Step 1: Write failing planner prompt test**

Add to `agent-verse-backend/tests/agent/test_graph_coverage.py`:

```python
async def test_planner_receives_available_tool_context():
    seen = {}

    class Planner(FakeProvider):
        async def complete(self, req):
            seen["content"] = req.messages[-1].content
            return await super().complete(req)

    graph = AgentGraph(
        planner=Planner(responses=['{"steps":["call jira_search"]}']),
        executor=FakeProvider(responses=["done"]),
        verifier=FakeProvider(responses=['{"success":true,"reason":"ok"}']),
    )

    await graph.run(
        goal="Inspect Jira",
        tenant_ctx=T,
        initial_context={"tool_prompt": "[Available tools]\n- JIRA.jira_search: Search Jira issues"},
    )

    assert "JIRA.jira_search" in seen["content"]
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
uv run pytest tests/agent/test_graph_coverage.py::test_planner_receives_available_tool_context
```

Expected: fails because graph planner ignores `tool_prompt`.

- [ ] **Step 3: Include tool prompt in `_node_plan()`**

Modify `agent-verse-backend/app/agent/graph.py` in `_node_plan()`:

```python
tool_prompt = agent_state.context.get("tool_prompt", "")
if tool_prompt:
    extra_parts.append(tool_prompt)
```

- [ ] **Step 4: Add tool context to `GoalService` initial context**

When calling `loop.run()` in `_run_agent_loop()`:

```python
initial_context = {}
if tool_context is not None:
    initial_context["tool_prompt"] = tool_context.to_prompt_block()
await loop.run(
    goal=goal_text,
    tenant_ctx=tenant_ctx,
    initial_context=initial_context,
    event_callback=callback,
)
```

- [ ] **Step 5: Run graph tests**

Run:

```bash
uv run pytest tests/agent/test_graph_coverage.py
```

Expected: all pass.

---

### Task 7: Execute Structured Tool Calls

**Files:**
- Create: `agent-verse-backend/app/agent/tool_calls.py`
- Modify: `agent-verse-backend/app/agent/graph.py`
- Test: `agent-verse-backend/tests/agent/test_tool_calls.py`

- [ ] **Step 1: Write failing parser tests**

Create `agent-verse-backend/tests/agent/test_tool_calls.py`:

```python
from app.agent.tool_calls import extract_tool_call


def test_extract_tool_call_from_json_text():
    text = '{"tool":"jira_search","arguments":{"jql":"project = BAU"}}'

    call = extract_tool_call(text)

    assert call is not None
    assert call.tool == "jira_search"
    assert call.arguments == {"jql": "project = BAU"}


def test_extract_tool_call_returns_none_for_plain_text():
    assert extract_tool_call("summarize manually") is None
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
uv run pytest tests/agent/test_tool_calls.py
```

Expected: fails because `tool_calls.py` does not exist.

- [ ] **Step 3: Implement parser**

Create `agent-verse-backend/app/agent/tool_calls.py`:

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
    args = obj.get("arguments") or obj.get("args") or {}
    if not isinstance(args, dict):
        args = {}
    return ToolCall(tool=str(tool), arguments=args)
```

- [ ] **Step 4: Execute parsed tool call in graph**

In `AgentGraph._execute_step()` after executor LLM returns `raw_output`, add:

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
            await self._emit(
                {
                    "type": "tool_call_complete",
                    "tool": tool_ref.name,
                    "server_id": tool_ref.server_id,
                    "success": result.success,
                    "output": result.output,
                    "error": result.error,
                }
            )
            raw_output = str(result.output if result.success else result.error)
```

- [ ] **Step 5: Pass `tool_context` and `mcp_client` from GoalService**

In `_run_agent_loop()` initial context:

```python
initial_context["tool_context"] = tool_context
initial_context["mcp_client"] = getattr(self._app_state, "mcp_client", None)
```

- [ ] **Step 6: Run tests**

Run:

```bash
uv run pytest tests/agent/test_tool_calls.py tests/agent/test_graph_coverage.py
```

Expected: all pass.

---

### Task 8: Add Agent Dropdown To Goal UI

**Files:**
- Modify: `agent-verse-frontend/src/features/goals/GoalsListPage.tsx`
- Modify: `agent-verse-frontend/src/lib/api/client.ts`
- Test: `agent-verse-frontend/src/features/goals/GoalsListPage.test.tsx`

- [ ] **Step 1: Add request type support**

Modify `agent-verse-frontend/src/lib/api/client.ts`:

```ts
export interface GoalRequest {
  goal: string;
  priority?: string;
  dry_run?: boolean;
  agent_id?: string;
  workflow_mode?: string;
}
```

- [ ] **Step 2: Add agents fetch helper**

In `GoalsListPage.tsx`, add:

```ts
const API_BASE = (import.meta as any).env?.VITE_API_URL ?? 'http://localhost:8000';

interface AgentOption {
  agent_id: string;
  name: string;
  autonomy_mode: string;
  connector_ids: string[];
}

async function fetchAgents(apiKey: string): Promise<AgentOption[]> {
  const res = await fetch(`${API_BASE}/agents`, { headers: { 'X-API-Key': apiKey } });
  if (!res.ok) throw new Error(`Failed to fetch agents: ${res.statusText}`);
  return res.json();
}
```

- [ ] **Step 3: Add selected agent state and query**

In `GoalsListPage()`:

```ts
const apiKey = useAuthStore((s) => s.apiKey);
const [selectedAgentId, setSelectedAgentId] = useState('auto');

const { data: agents = [] } = useQuery({
  queryKey: ['agents-for-goals'],
  queryFn: () => fetchAgents(apiKey),
  enabled: !!apiKey,
});
```

- [ ] **Step 4: Render dropdown above goal textarea**

Add inside submit form before `<textarea>`:

```tsx
<label className="block text-sm font-medium">
  Agent
  <select
    value={selectedAgentId}
    onChange={(e) => setSelectedAgentId(e.target.value)}
    className="mt-1 w-full px-3 py-2 text-sm border border-input rounded-md bg-background"
  >
    <option value="auto">Auto-select best agent</option>
    {agents.map((agent) => (
      <option key={agent.agent_id} value={agent.agent_id}>
        {agent.name} · {agent.autonomy_mode}
      </option>
    ))}
  </select>
</label>
```

- [ ] **Step 5: Submit selected agent ID**

Change mutation call:

```ts
mutationFn: (goal: string) => goalsApi.submit({
  goal,
  dry_run: dryRun,
  agent_id: selectedAgentId === 'auto' ? undefined : selectedAgentId,
  workflow_mode: selectedAgentId === 'auto' ? 'auto_route' : 'single_agent',
}),
```

- [ ] **Step 6: Run frontend typecheck**

Run:

```bash
cd agent-verse-frontend
npm run typecheck
```

Expected: pass.

---

### Task 9: Add Auto Agent Router

**Files:**
- Create: `agent-verse-backend/app/agent/router.py`
- Modify: `agent-verse-backend/app/services/goal_service.py`
- Test: `agent-verse-backend/tests/agent/test_router.py`

- [ ] **Step 1: Create router tests**

Create `agent-verse-backend/tests/agent/test_router.py`:

```python
from app.agent.router import select_agent


def test_select_agent_prefers_jira_agent_for_jira_goal():
    agents = [
        {"agent_id": "jira-1", "name": "jira-triage-agent", "connector_ids": ["srv-jira"]},
        {"agent_id": "mail-1", "name": "mail-agent", "connector_ids": ["srv-mail"]},
    ]

    selected = select_agent("Fetch open Jira issues from BAU", agents)

    assert selected is not None
    assert selected["agent_id"] == "jira-1"


def test_select_agent_returns_none_for_unclear_goal():
    agents = [{"agent_id": "jira-1", "name": "jira-triage-agent", "connector_ids": ["srv-jira"]}]

    assert select_agent("Do something useful", agents) is None
```

- [ ] **Step 2: Implement deterministic router**

Create `agent-verse-backend/app/agent/router.py`:

```python
from __future__ import annotations

from typing import Any


def select_agent(goal: str, agents: list[dict[str, Any]]) -> dict[str, Any] | None:
    text = goal.lower()
    scored: list[tuple[int, dict[str, Any]]] = []
    for agent in agents:
        name = str(agent.get("name", "")).lower()
        score = 0
        if "jira" in text and "jira" in name:
            score += 10
        if "confluence" in text and "confluence" in name:
            score += 10
        if "mail" in text and ("mail" in name or "email" in name):
            score += 10
        if score:
            scored.append((score, agent))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]
```

- [ ] **Step 3: Use router when `workflow_mode == auto_route`**

In `GoalService.submit_goal()`:

```python
if agent_id is None and workflow_mode == "auto_route":
    store = getattr(self._app_state, "agent_store", None) if self._app_state else None
    if store is not None:
        from app.agent.router import select_agent
        candidates = store.list_all(tenant_ctx=tenant_ctx)
        selected = select_agent(goal, candidates)
        if selected is not None:
            agent_id = selected.get("agent_id")
```

- [ ] **Step 4: Run router and goal tests**

Run:

```bash
uv run pytest tests/agent/test_router.py tests/services/test_goal_service.py
```

Expected: pass.

---

### Task 10: Support Complex Multi-Connector Workflows

**Files:**
- Create: `agent-verse-backend/app/agent/workflow_planner.py`
- Test: `agent-verse-backend/tests/agent/test_workflow_planner.py`

- [ ] **Step 1: Write workflow planner tests**

Create `agent-verse-backend/tests/agent/test_workflow_planner.py`:

```python
from app.agent.workflow_planner import build_static_workflow


def test_build_static_workflow_for_jira_confluence_email_goal():
    workflow = build_static_workflow(
        "Fetch all open Jira issues, create a Confluence page, and send email"
    )

    assert [step.connector for step in workflow.steps] == ["jira", "confluence", "email"]
    assert workflow.steps[0].intent == "fetch_open_issues"
    assert workflow.steps[1].intent == "create_summary_page"
    assert workflow.steps[2].intent == "send_summary_email"
```

- [ ] **Step 2: Implement static workflow model**

Create `agent-verse-backend/app/agent/workflow_planner.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WorkflowStep:
    connector: str
    intent: str
    depends_on: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class WorkflowPlan:
    steps: list[WorkflowStep]


def build_static_workflow(goal: str) -> WorkflowPlan:
    text = goal.lower()
    steps: list[WorkflowStep] = []
    if "jira" in text:
        steps.append(WorkflowStep(connector="jira", intent="fetch_open_issues"))
    if "confluence" in text:
        steps.append(
            WorkflowStep(
                connector="confluence",
                intent="create_summary_page",
                depends_on=[0] if steps else [],
            )
        )
    if "mail" in text or "email" in text:
        steps.append(
            WorkflowStep(
                connector="email",
                intent="send_summary_email",
                depends_on=list(range(len(steps))) if steps else [],
            )
        )
    return WorkflowPlan(steps=steps)
```

- [ ] **Step 3: Run workflow planner tests**

Run:

```bash
uv run pytest tests/agent/test_workflow_planner.py
```

Expected: pass.

- [ ] **Step 4: Wire workflow mode into goal context**

In `GoalService.submit_goal()`, when `workflow_mode == "multi_agent"`:

```python
from app.agent.workflow_planner import build_static_workflow
workflow_plan = build_static_workflow(goal)
```

Store `workflow_plan` in `GoalRecord` context or event:

```python
await self._dispatch_event(
    goal_id,
    {"type": "workflow_planned", "steps": [step.__dict__ for step in workflow_plan.steps]},
)
```

- [ ] **Step 5: Run goal service tests**

Run:

```bash
uv run pytest tests/services/test_goal_service.py tests/agent/test_workflow_planner.py
```

Expected: pass.

---

### Task 11: Add Agent-Bound Schedules

**Files:**
- Modify: `agent-verse-backend/app/api/schedules.py`
- Modify: `agent-verse-backend/app/triggers/store.py`
- Modify: `agent-verse-backend/app/scaling/tasks.py`
- Modify: `agent-verse-frontend/src/features/schedules/SchedulesPage.tsx`
- Test: `agent-verse-backend/tests/api/test_schedules_api.py`

- [ ] **Step 1: Add schedule API test for `agent_id`**

Add to `agent-verse-backend/tests/api/test_schedules_api.py`:

```python
def test_create_schedule_accepts_agent_id() -> None:
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.post(
        "/schedules",
        json={
            "trigger_type": "interval",
            "interval_seconds": 1800,
            "agent_id": "jira-agent-1",
            "goal_template": "Triage BAU issues",
            "name": "Jira triage every 30 minutes",
        },
        headers={"X-API-Key": _VALID_KEY},
    )

    assert resp.status_code == 201
    assert resp.json()["agent_id"] == "jira-agent-1"
```

- [ ] **Step 2: Extend schedule request/record**

In `app/api/schedules.py`, `CreateScheduleRequest` already has `agent_id`; ensure `_record_to_dict()` returns it and `store.create()` receives it.

In `app/triggers/store.py`, store records with:

```python
"agent_id": agent_id,
"goal_template": goal_template,
```

- [ ] **Step 3: Submit scheduled goal with agent ID**

In `app/scaling/tasks.py`, when firing a due schedule, call:

```python
await goal_service.submit_goal(
    goal=schedule["goal_template"],
    priority="normal",
    dry_run=False,
    tenant_ctx=tenant_ctx,
    agent_id=schedule.get("agent_id"),
    workflow_mode="single_agent",
)
```

- [ ] **Step 4: Add frontend agent dropdown in schedules**

In `SchedulesPage.tsx`, mirror the goal agent dropdown:

```tsx
<select
  value={form.agent_id}
  onChange={(e) => setForm((f) => ({ ...f, agent_id: e.target.value }))}
>
  {agents.map((agent) => (
    <option key={agent.agent_id} value={agent.agent_id}>{agent.name}</option>
  ))}
</select>
```

- [ ] **Step 5: Run tests and typecheck**

Run:

```bash
uv run pytest tests/api/test_schedules_api.py
cd ../agent-verse-frontend
npm run typecheck
```

Expected: backend tests and frontend typecheck pass.

---

### Task 12: Add Goal Detail Tool Output Rendering

**Files:**
- Modify: `agent-verse-frontend/src/features/goals/GoalDetailPage.tsx`

- [ ] **Step 1: Render tool call events explicitly**

In `StepRow`, derive event label:

```tsx
const type = event.type as string;
const step = (event.step || event.tool || type) as string;
```

Then for `tool_call_complete`, show:

```tsx
{type === 'tool_call_complete' && (
  <div className="px-4 pb-3 text-xs text-muted-foreground">
    Tool: {String(event.tool)} · Success: {String(event.success)}
  </div>
)}
```

- [ ] **Step 2: Run frontend typecheck**

Run:

```bash
npm run typecheck
```

Expected: pass.

---

### Task 13: End-To-End Jira Goal Validation

**Files:**
- Create: `agent-verse-backend/tests/e2e/test_jira_agent_execution.py`

- [ ] **Step 1: Create mock MCP server fixture**

Add a local test fixture that responds to JSON-RPC `tools/list` and `tools/call`:

```python
@pytest.fixture
def jira_mcp_server(respx_mock):
    respx_mock.post("https://mock-jira-mcp.local/mcp").mock(
        side_effect=[
            httpx.Response(200, json={"jsonrpc": "2.0", "result": {"tools": [{"name": "jira_search", "description": "Search Jira", "inputSchema": {}}]}}),
            httpx.Response(200, json={"jsonrpc": "2.0", "result": {"issues": [{"key": "BAU-1", "summary": "Bug"}]}}),
        ]
    )
```

- [ ] **Step 2: Create E2E test**

```python
async def test_jira_agent_goal_calls_jira_tool(client_and_key, jira_mcp_server):
    client, key = client_and_key
    connector = await client.post(
        "/connectors",
        json={"name": "jira", "url": "https://mock-jira-mcp.local/mcp", "auth_type": "basic", "auth_config": {"username": "u", "password": "p"}},
        headers={"X-API-Key": key},
    )
    connector_id = connector.json()["server_id"]
    agent = await client.post(
        "/agents",
        json={"name": "jira-agent", "goal_template": "Use Jira", "connector_ids": [connector_id]},
        headers={"X-API-Key": key},
    )
    agent_id = agent.json()["agent_id"]
    goal = await client.post(
        "/goals",
        json={"agent_id": agent_id, "goal": "Fetch BAU open issues", "dry_run": False},
        headers={"X-API-Key": key},
    )

    assert goal.status_code == 202
```

- [ ] **Step 3: Run E2E test**

Run:

```bash
uv run pytest tests/e2e/test_jira_agent_execution.py
```

Expected: goal is accepted and mock MCP server receives tool calls.

---

## Final Verification Commands

Run all of these before calling the platform slice complete:

```bash
cd agent-verse-backend
uv run pytest \
  tests/api/test_goals.py \
  tests/services/test_goal_service.py \
  tests/api/test_agents.py \
  tests/mcp/test_mcp_client.py \
  tests/agent/test_tool_context.py \
  tests/agent/test_tool_calls.py \
  tests/agent/test_router.py \
  tests/agent/test_workflow_planner.py \
  tests/api/test_schedules_api.py \
  tests/e2e/test_jira_agent_execution.py
```

```bash
cd agent-verse-frontend
npm run typecheck
npm run test -- src/app/App.test.tsx src/features/auth/AuthPage.test.tsx
```

Manual live verification:

```bash
curl -sS -X POST "$AGENTVERSE_URL/connectors/$JIRA_CONNECTOR_ID/test" \
  -H "X-API-Key: $AGENTVERSE_API_KEY"

curl -sS -X POST "$AGENTVERSE_URL/goals" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $AGENTVERSE_API_KEY" \
  -d "{\"agent_id\":\"$JIRA_SUPERVISED_AGENT_ID\",\"goal\":\"Inspect the 1 newest unresolved Jira issue in project BAU and summarize only. Do not write to Jira.\",\"dry_run\":false}"
```

Expected: Jira connector test is healthy, the goal emits tool discovery/execution events, and the goal detail page shows Jira output.

## Execution Handoff

Plan complete. Recommended implementation sequence:

1. Task 1: Goal-to-agent binding.
2. Task 2: Load selected agent configuration.
3. Task 3: Tool context.
4. Task 4: MCP JSON-RPC tools/list and tools/call.
5. Task 7: Structured tool calls.
6. Task 8: Goal UI agent dropdown.
7. Task 11: Agent-bound schedules.
8. Task 13: End-to-end Jira validation.

Do not enable full autonomous Jira writes until the E2E Jira validation and governance approval tests pass.
