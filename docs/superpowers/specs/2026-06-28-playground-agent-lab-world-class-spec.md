# Playground / Agent Lab — World-Class Specification

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Replace three overlapping testing pages (PlaygroundPage, SimulationPage, EvalPage) with a unified "Agent Lab" workbench featuring streaming simulation, schema-aware mock tool builder, eval history charts, custom red-team case authoring, and side-by-side agent comparison.

**Architecture:** Add streaming SSE endpoint for simulation, tool discovery endpoint, custom red-team DB table (migration 0052), eval history time-series endpoint; build unified `/lab` page with 3 tabs and animated streaming feed.

**Tech Stack:** Python 3.12 · FastAPI · SSE · SQLAlchemy · React 19 · EventSource · Recharts · Zustand · CSS Keyframes

---

## 1. Vision

The Agent Lab is the single place where developers test, evaluate, and optimize their agents before deploying to production. World-class means:
- You type a goal and watch each step execute in real-time, one by one, with tool calls animating in
- You can test exactly what the agent would do against mock tool responses using the actual tool schemas from your connected MCP servers
- You can run red-team attacks against your agents — including custom scenarios you define
- You can compare two agents head-to-head on the same goal
- Eval scores have trends, not just single-point snapshots

---

## 2. Current State

### What exists

| Page | What it does | Quality |
|------|-------------|---------|
| PlaygroundPage | Goal + mock JSON → POST /enterprise/simulation → show result | Functional, no streaming |
| SimulationPage | Governance + dry-run → policy check results + planned steps | Functional, useful |
| EvalPage | Red-team + simulation (duplicate!) + eval scorer + suggestions | Functional but fragmented |
| `/enterprise/simulation` | Full AgentGraph with MockMCPClient, real LLM if available | Strong backend |
| `SimulationRunner` | `enterprise/simulation.py:92` — tries real graph, falls back to stub | Real implementation |
| RedTeamRunner | `enterprise/red_team.py` | Real — hardcoded suite |
| EvalRunner | `intelligence/eval_runner.py` | Real — 6-dimension scoring |

### Critical gaps

- **SimulationSection in EvalPage duplicates PlaygroundPage** — two pages calling same endpoint
- **No streaming** — simulation blocks 30+ seconds, user sees spinning button only
- **No agent selector** — can't test "what would Agent X do?"
- **No tool discovery** — mock tools uses hardcoded Jira example instead of tenant's actual MCP tools
- **Eval scores have no history** — no trend chart, no agent comparison
- **Red-team cases are hardcoded** — users can't add custom adversarial cases

---

## 3. Backend Specification

### 3.1 Streaming Simulation Endpoint

**File to modify**: `agent-verse-backend/app/api/enterprise.py`

Add new endpoint `POST /enterprise/simulation/stream` that returns SSE:

```python
from starlette.responses import StreamingResponse
import json
import asyncio

class StreamingSimulationRequest(BaseModel):
    goal: str = Field(..., min_length=1, max_length=10_000)
    mock_tools: dict[str, Any] = {}
    agent_id: str | None = None
    agent_config: dict[str, Any] | None = None
    max_steps: int = Field(default=10, ge=1, le=30)
    compare_mode: bool = False
    compare_agent_id: str | None = None

@router.post("/simulation/stream")
async def stream_simulation(request: Request, body: StreamingSimulationRequest) -> StreamingResponse:
    """Run simulation with real-time SSE event emission per step."""
    ctx = _require_tenant(request)
    runner = _simulation_runner(request)

    async def generate():
        try:
            # Event 1: started
            yield f"data: {json.dumps({'type': 'simulation_started', 'run_id': str(uuid.uuid4()), 'goal': body.goal[:100]})}\n\n"

            # Build agent config override if provided
            agent_override = {}
            if body.agent_config:
                agent_override = body.agent_config
            elif body.agent_id:
                agent_store = getattr(request.app.state, "agent_store", None)
                if agent_store:
                    agent = agent_store.get(body.agent_id, tenant_ctx=ctx)
                    if agent:
                        agent_override = dict(agent)

            # Run simulation with step callbacks
            step_number = 0
            async for event in runner.run_streaming(
                goal=body.goal,
                mock_tools=body.mock_tools,
                tenant_ctx=ctx,
                agent_override=agent_override,
                max_steps=body.max_steps,
            ):
                yield f"data: {json.dumps(event)}\n\n"
                await asyncio.sleep(0)  # yield control to allow streaming

        except Exception as exc:
            yield f"data: {json.dumps({'type': 'simulation_error', 'message': str(exc)[:200]})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )
```

**File to modify**: `agent-verse-backend/app/enterprise/simulation.py`

Add `run_streaming()` async generator method:

```python
async def run_streaming(
    self,
    goal: str,
    mock_tools: dict[str, Any],
    tenant_ctx,
    agent_override: dict[str, Any] | None = None,
    max_steps: int = 10,
):
    """Async generator yielding SSE events as simulation executes."""
    run_id = uuid.uuid4().hex[:12]

    yield {"type": "simulation_started", "run_id": run_id, "goal": goal, "agent_config": agent_override or {}}

    try:
        # Use existing MockMCPClient pattern
        mock_client = MockMCPClient(mock_tools)
        total_cost = 0.0
        step_count = 0

        # If no real LLM, run stub simulation with fake steps
        if self._provider is None or isinstance(self._provider, FakeProvider):
            yield {"type": "simulation_info", "message": "Running stub simulation (no LLM provider configured)"}
            for i in range(min(3, max_steps)):
                await asyncio.sleep(0.3)
                yield {"type": "step_started", "step_number": i + 1, "description": f"Simulated step {i + 1}"}
                await asyncio.sleep(0.5)
                yield {"type": "step_completed", "step_number": i + 1, "output": f"[Simulated output {i + 1}]",
                       "tool_called": None, "mock_hit": False, "cost_increment": 0.001}
            yield {"type": "simulation_complete", "run_id": run_id, "total_steps": 3,
                   "total_cost": 0.003, "used_real_llm": False, "final_status": "complete"}
            return

        # Real simulation using AgentGraph with mock MCP client
        from app.agent.graph import AgentGraph
        step_results = []

        graph = AgentGraph(
            goal=goal,
            tenant_ctx=tenant_ctx,
            mcp_client=mock_client,
            provider=self._provider,
            embedder=None,
            max_iterations=max_steps,
        )

        # Intercept step execution via callback
        original_execute = graph._node_execute
        async def instrumented_execute(state):
            nonlocal step_count, total_cost
            step_count += 1
            step_desc = state.current_step.description if state.current_step else f"Step {step_count}"
            yield {"type": "step_started", "step_number": step_count, "description": step_desc}
            # ... call original, capture output
            result = await original_execute(state)
            cost_inc = state.context.get("last_step_cost", 0.0) if isinstance(state.context, dict) else 0.0
            total_cost += cost_inc
            yield {"type": "step_completed", "step_number": step_count, "output": str(state.current_step.output or "")[:500] if state.current_step else "", "tool_called": state.current_step.tool_name if state.current_step else None, "mock_hit": mock_client.was_hit(state.current_step.tool_name if state.current_step else ""), "cost_increment": cost_inc}
            return result
        graph._node_execute = instrumented_execute

        # Run the graph
        final_state = await graph.run()

        yield {"type": "simulation_complete", "run_id": run_id, "total_steps": step_count,
               "total_cost": total_cost, "used_real_llm": True,
               "final_status": final_state.status.value if hasattr(final_state.status, "value") else str(final_state.status)}

    except Exception as exc:
        yield {"type": "simulation_error", "message": str(exc)[:300]}
```

Keep `POST /enterprise/simulation` (blocking) for backward compatibility.

### 3.2 Tool Discovery Endpoint

**File to modify**: `agent-verse-backend/app/api/enterprise.py`

```python
@router.get("/simulation/available-tools")
async def get_available_tools(request: Request) -> dict[str, Any]:
    """Return all MCP tools available to this tenant for mock building."""
    ctx = _require_tenant(request)
    mcp_registry = getattr(request.app.state, "mcp_registry", None)
    mcp_client = getattr(request.app.state, "mcp_client", None)
    if mcp_registry is None or mcp_client is None:
        return {"tools": [], "message": "MCP registry not available"}

    servers = mcp_registry.list_servers(tenant_ctx=ctx)
    tools = []
    for server in servers[:10]:  # cap at 10 servers for response size
        try:
            server_tools = await mcp_client.list_tools(server.server_id, tenant_ctx=ctx)
            for tool in server_tools[:20]:  # cap at 20 tools per server
                tools.append({
                    "server_id": server.server_id,
                    "server_name": server.name,
                    "tool_name": f"{server.server_id}.{tool.get('name', '')}",
                    "description": tool.get("description", "")[:200],
                    "input_schema": tool.get("inputSchema", {}),
                    "example_mock": _generate_example_mock(tool),
                })
        except Exception:
            pass  # skip unavailable servers

    return {"tools": tools, "total": len(tools)}

def _generate_example_mock(tool: dict) -> dict:
    """Generate an example mock response based on tool's output schema."""
    name = tool.get("name", "")
    # Simple heuristics for common tool patterns
    if "search" in name or "list" in name or "get" in name:
        return {"results": [{"id": "example-1", "name": "Example result"}], "total": 1}
    if "create" in name or "add" in name:
        return {"id": "new-item-123", "status": "created", "name": "New item"}
    if "delete" in name or "remove" in name:
        return {"success": True, "deleted_id": "item-123"}
    return {"success": True, "result": "Operation completed"}
```

### 3.3 Eval History Endpoint

**File to modify**: `agent-verse-backend/app/api/enterprise.py`

```python
@intelligence_router.get("/eval-history")
async def get_eval_history(
    request: Request,
    agent_id: str | None = None,
    days: int = Query(default=30, ge=1, le=90),
    dimension: str | None = None,
) -> dict[str, Any]:
    """Return time-series eval scores per dimension for the specified agent/period."""
    ctx = _require_tenant(request)
    goal_svc = getattr(request.app.state, "goal_service", None)
    db_factory = getattr(goal_svc, "_db", None) if goal_svc else None

    if db_factory is None:
        return {"series": {}, "agent_id": agent_id, "period_days": days, "total_evaluations": 0}

    try:
        from sqlalchemy import text as _t
        async with db_factory() as session:
            await session.execute(_t("SET LOCAL app.tenant_id = :tid"), {"tid": ctx.tenant_id})

            where_clauses = ["tenant_id = :tid", "run_at >= NOW() - (:days || ' days')::INTERVAL"]
            params: dict = {"tid": ctx.tenant_id, "days": days}

            if agent_id:
                where_clauses.append("goal_id IN (SELECT goal_id FROM goals WHERE agent_id = :agent_id AND tenant_id = :tid)")
                params["agent_id"] = agent_id

            where_sql = " AND ".join(where_clauses)
            rows = (await session.execute(_t(f"""
                SELECT goal_id, run_at,
                       score_task_completion, score_efficiency, score_accuracy,
                       score_safety, score_coherence, passed
                FROM evaluations
                WHERE {where_sql}
                ORDER BY run_at ASC
                LIMIT 500
            """), params)).fetchall()

        # Build time-series per dimension
        dimensions = ["task_completion", "efficiency", "accuracy", "safety", "coherence"]
        if dimension and dimension in dimensions:
            dimensions = [dimension]

        series = {dim: [] for dim in dimensions}
        for row in rows:
            ts = row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1])
            scores = {
                "task_completion": float(row[2] or 0),
                "efficiency": float(row[3] or 0),
                "accuracy": float(row[4] or 0),
                "safety": float(row[5] or 0),
                "coherence": float(row[6] or 0),
            }
            for dim in dimensions:
                series[dim].append({"ts": ts, "score": scores[dim], "goal_id": row[0], "passed": bool(row[7])})

        return {
            "agent_id": agent_id,
            "series": series,
            "period_days": days,
            "total_evaluations": len(rows),
        }
    except Exception as exc:
        return {"series": {}, "error": str(exc), "agent_id": agent_id, "period_days": days, "total_evaluations": 0}
```

### 3.4 Custom Red-Team Cases — Migration 0052

**File to create**: `agent-verse-backend/app/db/migrations/versions/0052_red_team_cases.py`

```python
"""Add custom red_team_cases table."""
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS red_team_cases (
            id              TEXT PRIMARY KEY,
            tenant_id       TEXT NOT NULL,
            name            TEXT NOT NULL,
            goal_injection  TEXT NOT NULL,
            expected_blocked BOOLEAN NOT NULL DEFAULT TRUE,
            category        TEXT NOT NULL DEFAULT 'custom',
            severity        TEXT NOT NULL DEFAULT 'high',
            is_builtin      BOOLEAN NOT NULL DEFAULT FALSE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_red_team_cases_tenant ON red_team_cases (tenant_id)")
    op.execute("ALTER TABLE red_team_cases ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE red_team_cases FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY red_team_cases_tenant_isolation ON red_team_cases
        USING (tenant_id = current_setting('app.tenant_id', TRUE) OR is_builtin = TRUE)
        WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))
    """)

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS red_team_cases CASCADE")
```

**New endpoints** in `intelligence_router`:
- `GET /red-team/cases` — lists builtin + custom cases
- `POST /red-team/cases` — creates custom case
- `DELETE /red-team/cases/{id}` — deletes custom case (own only)
- `POST /red-team/run` — runs selected cases (body: `{case_ids: ["all"] | [id, ...]}`)

### 3.5 Side-by-Side Comparison Endpoint

```python
class CompareSimulationRequest(BaseModel):
    goal: str
    mock_tools: dict[str, Any] = {}
    agent_a: dict[str, Any]  # {agent_id: str} or {agent_config: {...}}
    agent_b: dict[str, Any]

@router.post("/simulation/compare")
async def compare_simulations(request: Request, body: CompareSimulationRequest) -> dict[str, Any]:
    """Run same goal with two agent configurations and return comparison."""
    ctx = _require_tenant(request)
    runner = _simulation_runner(request)

    results_a, results_b = await asyncio.gather(
        runner.start(goal=body.goal, mock_tools=body.mock_tools, tenant_ctx=ctx,
                     agent_override=body.agent_a.get("agent_config")),
        runner.start(goal=body.goal, mock_tools=body.mock_tools, tenant_ctx=ctx,
                     agent_override=body.agent_b.get("agent_config")),
    )

    # Build comparison
    steps_a = results_a.get("steps", [])
    steps_b = results_b.get("steps", [])
    cost_a = results_a.get("cost_usd", 0)
    cost_b = results_b.get("cost_usd", 0)

    differences = []
    for i, (sa, sb) in enumerate(zip(steps_a, steps_b)):
        if sa.get("tool_name") != sb.get("tool_name"):
            differences.append({
                "step": i + 1,
                "a_tool": sa.get("tool_name"),
                "b_tool": sb.get("tool_name"),
                "note": "different tool choice",
            })

    winner = "agent_a" if cost_a < cost_b else "agent_b" if cost_b < cost_a else "tie"

    return {
        "agent_a": results_a,
        "agent_b": results_b,
        "comparison": {
            "steps_delta": len(steps_b) - len(steps_a),
            "cost_delta": round(cost_b - cost_a, 6),
            "winner": winner,
            "differences": differences,
        },
    }
```

---

## 4. Frontend Specification — The Agent Lab

### 4.1 New Page: AgentLabPage at /lab

**File to create**: `agent-verse-frontend/src/features/lab/AgentLabPage.tsx`

Top-level layout with 3 tabs:
- **Pre-Flight** — governance analysis before running
- **Live Sim** — streaming simulation (the main tab)
- **Score & Optimize** — eval scoring + red-team + suggestions

```typescript
import { lazy, Suspense, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { FlaskConical, Shield, BarChart3 } from "lucide-react";
import { Skeleton } from "@/components/ui/Skeleton";

const PreFlightTab = lazy(() => import("./tabs/PreFlightTab").then(m => ({ default: m.PreFlightTab })));
const LiveSimTab   = lazy(() => import("./tabs/LiveSimTab").then(m => ({ default: m.LiveSimTab })));
const ScoreTab     = lazy(() => import("./tabs/ScoreTab").then(m => ({ default: m.ScoreTab })));

type LabTab = "preflight" | "sim" | "score";

export function AgentLabPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = (searchParams.get("tab") as LabTab) ?? "sim";

  const tabs: Array<{ id: LabTab; label: string; icon: React.ElementType }> = [
    { id: "preflight", label: "Pre-Flight", icon: Shield },
    { id: "sim",       label: "Live Sim",   icon: FlaskConical },
    { id: "score",     label: "Score & Optimize", icon: BarChart3 },
  ];

  return (
    <div className="flex flex-col h-full">
      {/* Tab bar */}
      <div className="flex items-center gap-1 border-b border-border px-4 pt-4" role="tablist" aria-label="Agent Lab tabs">
        {tabs.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            role="tab"
            aria-selected={activeTab === id}
            onClick={() => setSearchParams({ tab: id })}
            className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium rounded-t-lg border-b-2 transition-colors
              ${activeTab === id
                ? "border-primary text-primary bg-primary/5"
                : "border-transparent text-muted-foreground hover:text-foreground hover:border-border"
              }`}
          >
            <Icon className="h-4 w-4" aria-hidden="true" />
            {label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-auto p-4 md:p-6">
        <Suspense fallback={<div className="grid grid-cols-2 gap-4">{Array.from({length:4}).map((_,i)=><Skeleton key={i} className="h-24 rounded-xl"/>)}</div>}>
          {activeTab === "preflight" && <PreFlightTab />}
          {activeTab === "sim"       && <LiveSimTab />}
          {activeTab === "score"     && <ScoreTab />}
        </Suspense>
      </div>
    </div>
  );
}
```

### 4.2 LiveSimTab — Streaming Simulation

**File to create**: `agent-verse-frontend/src/features/lab/tabs/LiveSimTab.tsx`

Left/right split: Left 60% = streaming feed, Right 40% = config panel.

**Config panel (right):**
```typescript
// Agent selector
<select onChange={e => setAgentId(e.target.value)} ...>
  <option value="">Auto-select best agent</option>
  {agents.map(a => <option key={a.agent_id} value={a.agent_id}>{a.name}</option>)}
</select>

// Mock Tools Builder
<MockToolsBuilder
  tools={availableTools}  // from GET /enterprise/simulation/available-tools
  values={mockTools}
  onChange={setMockTools}
/>

// Max steps
<input type="range" min={1} max={30} value={maxSteps} ... />

// Voice input
<VoiceGoalInput onTranscript={t => setGoal(prev => prev ? `${prev} ${t}` : t)} />

// Compare mode toggle
<label>
  <input type="checkbox" checked={compareMode} onChange={e => setCompareMode(e.target.checked)} />
  Compare with second agent
</label>
{compareMode && <select ...> second agent selector </select>}
```

**MockToolsBuilder component:**

```typescript
// Fetches available tools from /enterprise/simulation/available-tools
// For each tool: toggle (include/exclude), JSON editor with schema validation
// Shows tool name, description, input schema collapsible
// "Auto-populate examples" button fills all enabled tools with example mocks

interface MockToolsBuilderProps {
  tools: AvailableTool[];
  values: Record<string, any>;
  onChange: (values: Record<string, any>) => void;
}

export function MockToolsBuilder({ tools, values, onChange }: MockToolsBuilderProps) {
  // Group tools by server
  const grouped = tools.reduce((acc, t) => {
    (acc[t.server_name] = acc[t.server_name] ?? []).push(t);
    return acc;
  }, {} as Record<string, AvailableTool[]>);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium">Mock Tools</span>
        <button onClick={() => {
          const auto: Record<string, any> = {};
          tools.forEach(t => { if (values[t.tool_name] !== undefined) auto[t.tool_name] = t.example_mock; });
          onChange({ ...values, ...auto });
        }} className="text-xs text-primary hover:underline">Auto-fill examples</button>
      </div>
      {Object.entries(grouped).map(([serverName, serverTools]) => (
        <details key={serverName} className="border border-border rounded-lg">
          <summary className="px-3 py-2 text-xs font-medium cursor-pointer hover:bg-muted/30">{serverName}</summary>
          <div className="px-3 pb-3 space-y-2">
            {serverTools.map(tool => (
              <div key={tool.tool_name}>
                <label className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
                  <input
                    type="checkbox"
                    checked={values[tool.tool_name] !== undefined}
                    onChange={e => {
                      if (e.target.checked) onChange({ ...values, [tool.tool_name]: tool.example_mock });
                      else { const next = {...values}; delete next[tool.tool_name]; onChange(next); }
                    }}
                  />
                  <code className="text-primary text-[10px]">{tool.tool_name}</code>
                  <span className="truncate">{tool.description.slice(0, 50)}</span>
                </label>
                {values[tool.tool_name] !== undefined && (
                  <textarea
                    value={JSON.stringify(values[tool.tool_name], null, 2)}
                    onChange={e => {
                      try { onChange({ ...values, [tool.tool_name]: JSON.parse(e.target.value) }); } catch {}
                    }}
                    rows={3}
                    className="w-full text-[10px] font-mono border border-input rounded bg-background px-2 py-1 resize-none"
                  />
                )}
              </div>
            ))}
          </div>
        </details>
      ))}
    </div>
  );
}
```

**Streaming Feed (left):**

```typescript
// EventSource to POST /enterprise/simulation/stream
// Each event type renders a different component:

function StreamingFeed({ events }: { events: SimulationEvent[] }) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  return (
    <div className="space-y-2 overflow-y-auto max-h-[600px] pr-2">
      {events.map((event, i) => {
        switch (event.type) {
          case "simulation_started":
            return <SimulationStartedCard key={i} event={event} />;
          case "step_started":
            return <StepCard key={`${event.step_number}-loading`} event={event} state="loading" />;
          case "step_completed":
            return <StepCard key={`${event.step_number}-complete`} event={event} state="complete" />;
          case "mock_tool_called":
            return <MockHitBadge key={i} event={event} />;
          case "tool_not_mocked":
            return <LiveToolWarning key={i} event={event} />;
          case "simulation_complete":
            return <SimulationSummaryCard key={i} event={event} />;
          case "simulation_error":
            return <SimulationErrorCard key={i} event={event} />;
          default: return null;
        }
      })}
      <div ref={bottomRef} />
    </div>
  );
}
```

### 4.3 StepCard — Animated Step Component

```typescript
// Loading state: pulsing border + skeleton
// Complete state: tool chip + output preview (expandable)
function StepCard({ event, state }: { event: SimulationEvent; state: "loading" | "complete" }) {
  const [expanded, setExpanded] = useState(false);

  if (state === "loading") {
    return (
      <div className="flex items-center gap-3 p-3 rounded-lg border border-border bg-card animate-pulse-border">
        <div className="h-2 w-2 rounded-full bg-primary animate-ping" />
        <span className="text-sm text-muted-foreground flex-1 truncate">
          {(event as any).description ?? "Processing…"}
        </span>
      </div>
    );
  }

  return (
    <div
      className="p-3 rounded-lg border border-border bg-card animate-step-in hover:border-primary/20 cursor-pointer"
      onClick={() => setExpanded(v => !v)}
      role="button"
      tabIndex={0}
    >
      <div className="flex items-center gap-2">
        <span className="text-xs font-mono text-muted-foreground w-6">{(event as any).step_number}</span>
        {(event as any).tool_called && (
          <span className="text-[10px] px-1.5 py-0.5 bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 rounded font-mono">
            {(event as any).tool_called}
          </span>
        )}
        {(event as any).mock_hit && (
          <span className="text-[10px] px-1.5 py-0.5 bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300 rounded animate-mock-bounce">
            Mock
          </span>
        )}
        <span className="flex-1 text-xs text-foreground truncate">
          {String((event as any).output ?? "").slice(0, expanded ? 999 : 80)}
        </span>
        {(event as any).cost_increment > 0 && (
          <span className="text-[10px] text-amber-600 dark:text-amber-400 font-mono tabular-nums animate-cost-flash">
            +${(event as any).cost_increment.toFixed(5)}
          </span>
        )}
      </div>
      {expanded && (event as any).output && (
        <pre className="mt-2 text-[10px] bg-muted rounded p-2 overflow-auto max-h-48">
          {(event as any).output}
        </pre>
      )}
    </div>
  );
}
```

### 4.4 ScoreTab — Eval History + Red Team

**File to create**: `agent-verse-frontend/src/features/lab/tabs/ScoreTab.tsx`

3 sections:

**Section A: Eval History**
```typescript
// Agent selector + time range selector
// ThemedLineChart showing all 5 dimensions as separate lines over time
// Click a data point → navigate to that goal's detail page
// Summary stats: best score, worst score, trend direction

const { data: evalHistory } = useQuery({
  queryKey: ["eval-history", agentId, days],
  queryFn: () => fetch(`/intelligence/eval-history?agent_id=${agentId}&days=${days}`).then(r => r.json()),
});

// Build chart data:
const chartData = evalHistory?.series?.task_completion?.map((point: any, i: number) => ({
  ts: new Date(point.ts).toLocaleDateString(),
  task_completion: point.score,
  efficiency: evalHistory.series.efficiency?.[i]?.score ?? 0,
  accuracy: evalHistory.series.accuracy?.[i]?.score ?? 0,
  safety: evalHistory.series.safety?.[i]?.score ?? 0,
  coherence: evalHistory.series.coherence?.[i]?.score ?? 0,
})) ?? [];
```

**Section B: Red Team**
```typescript
// Case manager (toggle to open drawer)
// Cases list with builtin (locked) and custom (edit/delete)
// "+ Add Case" form
// "Run" button → streams results via EventSource

// RedTeamCaseManager drawer component:
interface RedTeamCase {
  id: string;
  name: string;
  goal_injection: string;
  expected_blocked: boolean;
  category: string;
  severity: "low" | "medium" | "high" | "critical";
  is_builtin: boolean;
}
```

**Section C: Optimization Suggestions**
- Existing implementation enhanced with "Run Self-Optimizer" and "A/B Test Suggestion" buttons

### 4.5 PreFlightTab Enhancement

**File to create**: `agent-verse-frontend/src/features/lab/tabs/PreFlightTab.tsx`

Two-column layout:

**Left: Governance Analysis**
- Each policy check shows: tool name, ALLOW/BLOCK/REQUIRE_APPROVAL verdict with color-coded badge
- Risk heat map: colored table (green/yellow/red) showing tool × risk level
- "Fix It" button on BLOCKED policies → PolicySuggestionModal with how-to instructions

**Right: Execution Plan**
- Dry-run step list with HITL gate markers (orange `👤` icons on high-risk steps)
- CostEstimateWidget showing expected cost range
- "Estimated agents" if supervisor mode detected

### 4.6 Redirects from Old Pages

**File to modify**: `agent-verse-frontend/src/features/playground/PlaygroundPage.tsx`
```typescript
// Replace entire component with redirect:
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
export function PlaygroundPage() {
  const navigate = useNavigate();
  useEffect(() => { navigate("/lab?tab=sim", { replace: true }); }, []);
  return null;
}
```

**File to modify**: `agent-verse-frontend/src/features/simulation/SimulationPage.tsx`
```typescript
// Replace with redirect:
export default function SimulationPage() {
  const navigate = useNavigate();
  useEffect(() => { navigate("/lab?tab=preflight", { replace: true }); }, []);
  return null;
}
```

### 4.7 Sidebar Update

**File to modify**: `agent-verse-frontend/src/components/ui/Sidebar.tsx`

Replace `/playground` with `/lab`:
```typescript
{ to: "/lab", icon: FlaskConical, label: "Agent Lab" },
```

### 4.8 App.tsx Update

```typescript
const AgentLabPage = lazy(() => import("@/features/lab/AgentLabPage").then(m => ({ default: m.AgentLabPage })));

// Add route:
<Route path="lab" element={<Suspense fallback={<LoadingSpinner />}><AgentLabPage /></Suspense>} />
```

### 4.9 Animation System

**1. Step entrance animation**:
```css
@keyframes stepIn {
  from { transform: translateY(12px); opacity: 0; }
  to   { transform: translateY(0); opacity: 1; }
}
.animate-step-in { animation: stepIn 200ms ease-out forwards; }
```

**2. Loading step pulse border**:
```css
@keyframes pulseBorder {
  0%, 100% { box-shadow: 0 0 0 0 rgba(var(--primary), 0.4); }
  50%       { box-shadow: 0 0 0 4px rgba(var(--primary), 0.1); }
}
.animate-pulse-border { animation: pulseBorder 1.5s ease-in-out infinite; }
```

**3. Mock hit badge bounce**:
```css
@keyframes mockBounce {
  0%, 100% { transform: scale(1); }
  40%       { transform: scale(1.3); }
}
.animate-mock-bounce { animation: mockBounce 300ms ease-out; }
```

**4. Cost delta flash**:
```css
@keyframes costFlash {
  0%   { background: rgba(245, 158, 11, 0.3); }
  100% { background: transparent; }
}
.animate-cost-flash { animation: costFlash 600ms ease-out; }
```

**5. Eval radar reveal**: ThemedRadarChart axes extend from center over 800ms with staggered delay (50ms per axis).

**6. Simulation complete summary slide-in**: Summary card slides down from above feed area.

---

## 5. TypeScript Interfaces

```typescript
// Add to agent-verse-frontend/src/lib/api/client.ts:

export type SimulationEventType =
  | "simulation_started" | "simulation_info" | "step_started" | "step_completed"
  | "mock_tool_called" | "tool_not_mocked" | "simulation_complete" | "simulation_error";

export interface SimulationEvent {
  type: SimulationEventType;
  [key: string]: unknown;
}

export interface AvailableTool {
  server_id: string;
  server_name: string;
  tool_name: string;
  description: string;
  input_schema: Record<string, unknown>;
  example_mock: Record<string, unknown>;
}

export interface EvalHistory {
  agent_id: string | null;
  series: Record<string, Array<{ ts: string; score: number; goal_id: string; passed: boolean }>>;
  period_days: number;
  total_evaluations: number;
}

export interface RedTeamCase {
  id: string;
  name: string;
  goal_injection: string;
  expected_blocked: boolean;
  category: string;
  severity: string;
  is_builtin: boolean;
  created_at: string;
}

// Add to simulationApi:
// streamSimulation: (body) => new EventSource + fetch POST (custom EventSource with POST)
// getAvailableTools: () => request<{tools: AvailableTool[]}>("/enterprise/simulation/available-tools"),
// compare: (body: CompareRequest) => request<CompareResult>("/enterprise/simulation/compare", {method:"POST",...}),
// getEvalHistory: (agentId?, days?) => request<EvalHistory>(`/intelligence/eval-history?...`),
// listRedTeamCases: () => request<RedTeamCase[]>("/intelligence/red-team/cases"),
// createRedTeamCase: (body) => request<RedTeamCase>("/intelligence/red-team/cases", {method:"POST",...}),
// deleteRedTeamCase: (id) => request<void>(`/intelligence/red-team/cases/${id}`, {method:"DELETE"}),
// runRedTeam: (caseIds) => request<RedTeamRunResult>("/intelligence/red-team/run", {method:"POST", body:JSON.stringify({case_ids:caseIds})}),
```

---

## 6. Zustand Store

```typescript
// agent-verse-frontend/src/stores/agentLabStore.ts
import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { SimulationEvent } from "@/lib/api/client";

interface AgentLabStore {
  activeTab: "preflight" | "sim" | "score";
  streamingEvents: SimulationEvent[];
  compareEventsA: SimulationEvent[];
  compareEventsB: SimulationEvent[];
  mockTools: Record<string, unknown>; // persisted in localStorage
  selectedAgentId: string;
  maxSteps: number;
  compareMode: boolean;
  compareAgentId: string;
  isStreaming: boolean;

  setTab: (tab: "preflight" | "sim" | "score") => void;
  appendEvent: (event: SimulationEvent) => void;
  appendCompareEvent: (side: "a" | "b", event: SimulationEvent) => void;
  clearSession: () => void;
  updateMock: (toolName: string, value: unknown) => void;
  removeMock: (toolName: string) => void;
  setStreaming: (v: boolean) => void;
}

export const useAgentLabStore = create<AgentLabStore>()(
  persist(
    (set) => ({
      activeTab: "sim",
      streamingEvents: [],
      compareEventsA: [],
      compareEventsB: [],
      mockTools: {},
      selectedAgentId: "",
      maxSteps: 10,
      compareMode: false,
      compareAgentId: "",
      isStreaming: false,

      setTab: (tab) => set({ activeTab: tab }),
      appendEvent: (event) => set(s => ({ streamingEvents: [...s.streamingEvents.slice(-99), event] })),
      appendCompareEvent: (side, event) => set(s => side === "a"
        ? { compareEventsA: [...s.compareEventsA.slice(-99), event] }
        : { compareEventsB: [...s.compareEventsB.slice(-99), event] }
      ),
      clearSession: () => set({ streamingEvents: [], compareEventsA: [], compareEventsB: [], isStreaming: false }),
      updateMock: (name, val) => set(s => ({ mockTools: { ...s.mockTools, [name]: val } })),
      removeMock: (name) => set(s => { const n = {...s.mockTools}; delete n[name]; return { mockTools: n }; }),
      setStreaming: (v) => set({ isStreaming: v }),
    }),
    {
      name: "av-agent-lab",
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({ mockTools: s.mockTools, maxSteps: s.maxSteps }),
    }
  )
);
```

---

## 7. Testing Strategy

```python
# tests/api/test_simulation_streaming.py
def test_simulation_stream_returns_sse_events():
    client = TestClient(_make_app())
    with client.stream("POST", "/enterprise/simulation/stream",
                       json={"goal": "Test goal"}, headers=_HEADERS) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        events = list(response.iter_lines())[:5]
        assert any("simulation_started" in e for e in events)

def test_tool_discovery_returns_tenant_tools():
    # Mock MCP registry, assert /available-tools returns tools
    pass

def test_eval_history_returns_time_series():
    # Insert eval records, query /eval-history, assert series structure
    pass

def test_red_team_custom_case_crud():
    # Create → list → delete → verify gone
    pass
```

```typescript
// Frontend: AgentLabPage.test.tsx
test("tab switching works correctly", async () => { ... });
test("mock tools builder shows available tools", async () => { ... });
test("streaming feed renders step events in order", async () => { ... });

// E2E: agent-lab.spec.ts
test("Pre-Flight → Live Sim → Score complete workflow", async ({ page }) => { ... });
```
