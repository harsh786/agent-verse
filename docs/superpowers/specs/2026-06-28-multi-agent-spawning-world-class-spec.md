# Multi-Agent Automatic Spawning — World-Class Specification

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Make multi-agent automatic spawning fully operational end-to-end — from `POST /goals` with `workflow_mode="supervisor"` through parallel sub-agent execution to a real-time Gantt + lineage tree in the UI.

**Architecture:** Wire `workflow_mode` branches in `goals.py`, register `civilization_spawn` in `AgentGraph`, add `goal_lineage` DB table (migration 0050), build `SubAgentGanttView` + `GoalLineageTree` + `MultiAgentLauncher` frontend components with D3-hierarchy and SSE-driven animations.

**Tech Stack:** Python 3.12 · FastAPI · asyncio.gather · SQLAlchemy · D3-hierarchy · @xyflow/react · Recharts · Zustand · CSS Keyframes

---

## 1. Vision

World-class multi-agent orchestration means:
- A user describes a complex goal ("Build and deploy a microservice with full test coverage") and AgentVerse automatically decomposes it into parallel specialist agents: one writes code, one writes tests, one sets up CI, one deploys
- The user watches a real-time Gantt chart showing each agent's progress bar filling, cost counter ticking, and status badge updating
- When any agent spawns a sub-agent (e.g., the code agent spawns a debugging specialist), a new row slides in below it with a connecting line
- The entire spawn tree is navigable: click any node to see that goal's detail page
- After completion, a side-by-side comparison shows which strategy cost the least and ran fastest

---

## 2. Current State & Gaps

### What exists (with evidence)

| Component | File | Status |
|-----------|------|--------|
| SupervisorAgent | `app/agent/supervisor.py:39` | ✅ Real — decomposes + asyncio.gather |
| CivilizationOrchestrator multi_agent | `civilization/orchestrator.py:140` | ✅ Real |
| spawn_tool definition | `civilization/spawn_tool.py:10` | ✅ Real definition |
| spawn_tool execution | `civilization/spawn_tool.py:43` | ✅ Real |
| GoalService parallel submit | `services/goal_service.py:1460-1490` | ✅ Real (multi-agent mode) |
| `workflow_mode` field on GoalRequest | `api/goals.py:40` | ✅ Field exists |

### Critical gaps

**GAP C1**: `workflow_mode="supervisor"` and `workflow_mode="multi_agent"` hit **no branch** in `api/goals.py` — only `"debate"` is branched (lines 78-96). Any non-debate workflow_mode falls through to `"single_agent"` execution silently.

**GAP C2**: `civilization_spawn` tool never registered in `AgentGraph._node_execute()`. Agents cannot autonomously spawn during execution.

### High gaps

- **No SSE events from SupervisorAgent** — sub-task progress invisible to user
- **No `sub_goal_ids` in goal response** — no way for UI to discover children
- **No goal lineage DB table** — parent→child relationships not persisted
- **No UI for parallel execution** — zero Gantt, zero tree, zero visual

---

## 3. Backend Specification

### 3.1 Wire workflow_mode in goals.py

**File to modify**: `agent-verse-backend/app/api/goals.py`

Add these branches inside `submit_goal()` after the existing debate branch:

```python
# ── Supervisor mode: LLM decomposes goal → parallel sub-agents ───────────────
if body.workflow_mode == "supervisor":
    from app.agent.supervisor import SupervisorAgent
    provider = getattr(request.app.state, "_app_provider", None)
    goal_svc = _goal_service(request)
    supervisor = SupervisorAgent(
        provider=provider,
        goal_service=goal_svc,
        max_parallel=body.supervisor_max_parallel if hasattr(body, "supervisor_max_parallel") else 5,
    )
    try:
        result = await supervisor.run(
            goal=body.goal,
            tenant_ctx=tenant,
            execution_context=exec_ctx,
        )
        return {
            "id": result.get("parent_goal_id", ""),
            "goal_id": result.get("parent_goal_id", ""),
            "status": "multi_agent",
            "mode": "supervisor",
            "sub_goal_ids": result.get("sub_goal_ids", []),
            "goal": body.goal,
        }
    except Exception as exc:
        raise HTTPException(500, f"Supervisor execution failed: {exc}") from exc

# ── Multi-agent mode: same goal dispatched to N agents in parallel ────────────
if body.workflow_mode == "multi_agent" and body.agent_ids:
    goal_svc = _goal_service(request)
    tasks = [
        goal_svc.submit_goal(
            goal=body.goal,
            tenant_ctx=tenant,
            agent_id=agent_id,
            priority=body.priority,
            dry_run=body.dry_run,
        )
        for agent_id in body.agent_ids[:5]  # cap at 5
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    valid = [r for r in results if isinstance(r, dict) and "goal_id" in r]
    return {
        "id": valid[0]["goal_id"] if valid else "",
        "goal_id": valid[0]["goal_id"] if valid else "",
        "status": "multi_agent",
        "mode": "multi_agent",
        "sub_goal_ids": [r["goal_id"] for r in valid],
        "goal": body.goal,
    }
```

Also add `agent_ids: list[str] = []` and `supervisor_max_parallel: int = 5` to `GoalRequest`.

### 3.2 SupervisorAgent — SSE Events + Lineage

**File to modify**: `agent-verse-backend/app/agent/supervisor.py`

```python
# In SupervisorAgent.run(), after decomposing into subtasks:

async def run(self, goal: str, tenant_ctx, execution_context: dict | None = None) -> dict:
    # 1. Decompose via LLM (existing logic)
    subtasks = await self._decompose(goal)

    # 2. Emit decomposition plan
    parent_goal_id = execution_context.get("parent_goal_id", "") if execution_context else ""
    await self._emit_event(parent_goal_id, {
        "type": "supervisor_plan_ready",
        "subtasks": [{"description": t.description, "index": i} for i, t in enumerate(subtasks)],
        "total": len(subtasks),
        "goal": goal,
    })

    # 3. Submit all subtasks
    sub_goal_ids = []
    for i, task in enumerate(subtasks):
        result = await self._goal_service.submit_goal(
            goal=task.description,
            tenant_ctx=tenant_ctx,
            execution_context={**( execution_context or {}), "parent_goal_id": parent_goal_id, "supervisor_index": i},
        )
        task_id = result.get("goal_id") or result.get("id", "")
        task.task_id = task_id
        sub_goal_ids.append(task_id)

        # Write lineage record
        await self._write_lineage(
            parent_goal_id=parent_goal_id,
            child_goal_id=task_id,
            spawn_reason=task.description[:200],
            tenant_id=tenant_ctx.tenant_id,
        )

    # 4. Poll for completion with SSE emission
    completed = []
    while not all(t.status in ("complete", "failed", "cancelled") for t in subtasks):
        await asyncio.sleep(3)
        for task in subtasks:
            if task.status not in ("complete", "failed", "cancelled") and task.task_id:
                goal_data = await self._goal_service.get_goal(task.task_id, tenant_ctx)
                new_status = goal_data.get("status", "planning") if goal_data else task.status
                if new_status != task.status:
                    task.status = new_status
                    task.result = goal_data.get("steps", []) if goal_data else []
                    await self._emit_event(parent_goal_id, {
                        "type": "subtask_status_changed",
                        "task_id": task.task_id,
                        "index": subtasks.index(task),
                        "status": new_status,
                        "cost_usd": goal_data.get("cost_usd", 0) if goal_data else 0,
                    })

    # 5. Synthesize result via LLM (existing logic)
    synthesis = await self._synthesize([t.result for t in subtasks], goal)

    return {
        "parent_goal_id": parent_goal_id,
        "sub_goal_ids": sub_goal_ids,
        "synthesis": synthesis,
        "total_subtasks": len(subtasks),
        "completed": sum(1 for t in subtasks if t.status == "complete"),
        "failed": sum(1 for t in subtasks if t.status == "failed"),
    }
```

### 3.3 New Database Table — Migration 0050

**File to create**: `agent-verse-backend/app/db/migrations/versions/0050_goal_lineage.py`

```python
"""Create goal_lineage table for parent→child goal relationships."""
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS goal_lineage (
            id              TEXT PRIMARY KEY,
            root_goal_id    TEXT NOT NULL,
            parent_goal_id  TEXT,
            child_goal_id   TEXT NOT NULL UNIQUE,
            parent_agent_id TEXT,
            child_agent_id  TEXT,
            civilization_id TEXT,
            spawn_reason    TEXT NOT NULL DEFAULT '',
            depth           INTEGER NOT NULL DEFAULT 0,
            spawned_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            tenant_id       TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_goal_lineage_root ON goal_lineage (root_goal_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_goal_lineage_parent ON goal_lineage (parent_goal_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_goal_lineage_tenant ON goal_lineage (tenant_id)")
    op.execute("ALTER TABLE goal_lineage ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE goal_lineage FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY goal_lineage_tenant_isolation ON goal_lineage
        USING (tenant_id = current_setting('app.tenant_id', TRUE))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))
    """)

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS goal_lineage CASCADE")
```

### 3.4 New API Endpoints

#### GET /goals/{id}/lineage
```
Response: {
  "root_goal_id": str,
  "nodes": [
    {
      "goal_id": str,
      "agent_id": str | null,
      "agent_name": str | null,
      "depth": int,
      "status": str,
      "cost_usd": float,
      "iterations": int,
      "spawned_at": ISO8601,
      "spawn_reason": str
    }
  ],
  "edges": [{"parent": str, "child": str}],
  "total_cost_usd": float,
  "total_goals": int,
  "completed": int,
  "failed": int
}
Business logic:
  SELECT gl.*, g.status, g.cost_usd, g.iterations
  FROM goal_lineage gl
  LEFT JOIN goals g ON g.goal_id = gl.child_goal_id
  WHERE gl.root_goal_id = :root_id AND gl.tenant_id = :tid
  UNION
  SELECT null, null, :root_id, null, null, null, null, 0, NOW(), '' FROM DUAL -- root node
```

#### GET /goals/{id}/sub-goals
```
Response: {
  "parent_goal_id": str,
  "sub_goals": [{"goal_id": str, "status": str, "description": str, "cost_usd": float, "agent_id": str}]
}
Business logic: SELECT child_goal_id FROM goal_lineage WHERE parent_goal_id=? JOIN goals
```

#### POST /goals/multi-agent  (dedicated endpoint)
```python
class MultiAgentRequest(BaseModel):
    goal: str = Field(..., min_length=1, max_length=10_000)
    mode: Literal["supervisor", "parallel", "civilization"] = "supervisor"
    max_agents: int = Field(default=5, ge=1, le=10)
    max_depth: int = Field(default=3, ge=1, le=5)
    budget_usd: float = Field(default=5.0, ge=0.1, le=100.0)
    agent_ids: list[str] = []  # for parallel mode
    priority: str = "normal"
```

### 3.5 GoalService Enhancement

**File to modify**: `agent-verse-backend/app/services/goal_service.py`

When a goal is created with `parent_goal_id` in `execution_context`, write to `goal_lineage`:

```python
# In _db_persist_goal(), after the INSERT into goals:
parent_goal_id = execution_context.get("parent_goal_id") if isinstance(execution_context, dict) else None
if parent_goal_id and db:
    import uuid
    # Determine root_goal_id (chase up lineage recursively, or use parent if depth=0)
    depth = execution_context.get("spawn_depth", 0) + 1
    await session.execute(
        _t("""
            INSERT INTO goal_lineage (id, root_goal_id, parent_goal_id, child_goal_id, tenant_id, depth, spawn_reason, spawned_at)
            VALUES (:id, COALESCE((SELECT root_goal_id FROM goal_lineage WHERE child_goal_id=:parent LIMIT 1), :parent),
                    :parent, :child, :tenant, :depth, :reason, NOW())
            ON CONFLICT (child_goal_id) DO NOTHING
        """),
        {"id": str(uuid.uuid4()), "parent": parent_goal_id, "child": goal_id,
         "tenant": tenant_id, "depth": depth, "reason": execution_context.get("supervisor_task_description", "")}
    )
```

---

## 4. Frontend Specification

### 4.1 GoalsListPage — Multi-Agent Submit Form

**File to modify**: `agent-verse-frontend/src/features/goals/GoalsListPage.tsx`

Add "Execution Mode" selector below the existing agent dropdown:
```typescript
type ExecutionMode = "single" | "supervisor" | "parallel" | "multi_agent_launcher";

// Mode selector with icon cards (not just a dropdown):
<div className="grid grid-cols-4 gap-2 mt-3">
  {[
    { mode: "single", icon: Bot, label: "Single Agent", desc: "One agent handles everything" },
    { mode: "supervisor", icon: GitBranch, label: "Supervisor", desc: "AI decomposes into parallel tasks" },
    { mode: "parallel", icon: LayoutGrid, label: "Parallel", desc: "Same goal, multiple agents" },
    { mode: "multi_agent_launcher", icon: Rocket, label: "Advanced", desc: "Full configuration" },
  ].map(({ mode, icon: Icon, label, desc }) => (
    <button
      key={mode}
      onClick={() => setExecutionMode(mode as ExecutionMode)}
      className={`flex flex-col items-center p-3 rounded-xl border text-center transition-all
        ${executionMode === mode
          ? "border-primary bg-primary/5 ring-1 ring-primary"
          : "border-border hover:border-primary/30 hover:bg-muted/30"
        }`}
    >
      <Icon className="h-5 w-5 mb-1" />
      <span className="text-xs font-medium">{label}</span>
      <span className="text-[10px] text-muted-foreground mt-0.5 hidden sm:block">{desc}</span>
    </button>
  ))}
</div>

{/* Supervisor options */}
{executionMode === "supervisor" && (
  <div className="flex gap-3 items-center mt-2 p-3 bg-muted/30 rounded-lg">
    <label className="text-xs text-muted-foreground flex items-center gap-2">
      Max agents
      <input type="range" min={2} max={10} value={maxAgents} onChange={e => setMaxAgents(+e.target.value)} className="w-20 accent-primary" />
      <span className="w-4 text-center font-mono">{maxAgents}</span>
    </label>
    <label className="text-xs text-muted-foreground flex items-center gap-2">
      Max depth
      <select value={maxDepth} onChange={e => setMaxDepth(+e.target.value)} className="text-xs border rounded px-1 bg-background">
        {[1,2,3,4,5].map(d => <option key={d}>{d}</option>)}
      </select>
    </label>
  </div>
)}

{/* Advanced launcher redirect */}
{executionMode === "multi_agent_launcher" && (
  <button onClick={() => navigate("/goals/multi-agent")} className="w-full py-2 text-sm text-primary border border-primary/30 rounded-lg hover:bg-primary/5 transition-colors mt-2">
    Open Multi-Agent Launcher →
  </button>
)}
```

### 4.2 GoalDetailPage — SubAgentGanttView

**File to create**: `agent-verse-frontend/src/features/goals/components/SubAgentGanttView.tsx`

Show when `goal.sub_goal_ids?.length > 0`:

```typescript
import { useQuery } from "@tanstack/react-query";
import { goalsApi } from "@/lib/api/client";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { LiveCostTicker } from "@/components/live/LiveCostTicker";
import { ExternalLink } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useState, useEffect } from "react";

interface SubAgentGanttViewProps {
  parentGoalId: string;
  subGoalIds: string[];
}

export function SubAgentGanttView({ parentGoalId, subGoalIds }: SubAgentGanttViewProps) {
  const navigate = useNavigate();
  const [newIds, setNewIds] = useState<Set<string>>(new Set());

  // Poll all sub-goals every 3s
  const { data: subGoals = [] } = useQuery({
    queryKey: ["sub-goals", parentGoalId],
    queryFn: async () => {
      const results = await Promise.allSettled(subGoalIds.map(id => goalsApi.get(id)));
      return results
        .filter((r): r is PromiseFulfilledResult<any> => r.status === "fulfilled")
        .map(r => r.value);
    },
    refetchInterval: 3000,
    enabled: subGoalIds.length > 0,
  });

  // Track new arrivals for animation
  useEffect(() => {
    const ids = new Set(subGoals.map((g: any) => g.goal_id || g.id));
    setNewIds(prev => {
      const added = new Set([...ids].filter(id => !prev.has(id) && prev.size > 0));
      if (added.size > 0) setTimeout(() => setNewIds(ids), 800);
      return ids;
    });
  }, [subGoals.length]);

  const LIVE = new Set(["planning", "executing", "verifying"]);
  const totalCost = subGoals.reduce((sum: number, g: any) => sum + (g.cost_usd ?? 0), 0);
  const completed = subGoals.filter((g: any) => ["complete", "completed"].includes(g.status)).length;

  return (
    <div className="space-y-3">
      {/* Summary bar */}
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{completed}/{subGoals.length} agents complete</span>
        <LiveCostTicker
          currentCost={totalCost}
          isRunning={subGoals.some((g: any) => LIVE.has(g.status))}
        />
        <button
          onClick={() => navigate(`/goals/${parentGoalId}/lineage`)}
          className="flex items-center gap-1 text-primary hover:underline"
        >
          View Tree <ExternalLink className="h-3 w-3" />
        </button>
      </div>

      {/* Gantt rows */}
      <div className="space-y-2">
        {subGoals.map((goal: any, i: number) => {
          const goalId = goal.goal_id || goal.id;
          const isLive = LIVE.has(goal.status);
          const isNew = newIds.has(goalId) && i === subGoals.length - 1;
          return (
            <div
              key={goalId}
              className={`flex items-center gap-3 p-2.5 rounded-lg border border-border bg-card hover:border-primary/20 cursor-pointer transition-all
                ${isNew ? "animate-slide-in-from-left" : ""}
              `}
              onClick={() => navigate(`/goals/${goalId}`)}
              role="button"
              tabIndex={0}
              aria-label={`View sub-agent goal ${i + 1}`}
            >
              {/* Index */}
              <span className="text-xs text-muted-foreground w-5 text-center shrink-0">{i + 1}</span>

              {/* Goal description */}
              <p className="flex-1 text-xs truncate font-medium">{goal.goal}</p>

              {/* Live progress bar (for executing goals) */}
              {isLive && (
                <div className="w-24 h-1.5 bg-muted rounded-full overflow-hidden shrink-0">
                  <div
                    className="h-full bg-primary rounded-full"
                    style={{
                      width: `${Math.min(100, ((goal.iterations ?? 0) / 15) * 100)}%`,
                      transition: "width 800ms ease-out",
                    }}
                    role="progressbar"
                    aria-valuenow={Math.min(100, ((goal.iterations ?? 0) / 15) * 100)}
                    aria-valuemin={0}
                    aria-valuemax={100}
                  />
                </div>
              )}

              {/* Cost */}
              <LiveCostTicker
                currentCost={goal.cost_usd ?? 0}
                isRunning={isLive}
                className="text-[10px]"
              />

              {/* Status badge */}
              <StatusBadge status={goal.status} size="sm" />

              {/* Link icon */}
              <ExternalLink className="h-3.5 w-3.5 text-muted-foreground opacity-0 group-hover:opacity-100" aria-hidden="true" />
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

Add to `GoalDetailPage.tsx`: when `(goal as any).sub_goal_ids?.length > 0`, render `<SubAgentGanttView>` as a new "Sub-Agents" tab in the tab bar.

### 4.3 New: GoalLineageTree at /goals/:goalId/lineage

**File to create**: `agent-verse-frontend/src/features/goals/GoalLineageTree.tsx`

D3-hierarchy collapsible tree visualization:

```typescript
import { useEffect, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { insightsApi } from "@/lib/api/client";  // will add getGoalLineage
import { StatusBadge } from "@/components/ui/StatusBadge";
import { Skeleton } from "@/components/ui/Skeleton";
import { ArrowLeft, Download, ZoomIn, ZoomOut } from "lucide-react";

// Uses d3-hierarchy for layout (no new dependency — d3-force already installed)
// import { hierarchy, tree } from "d3-hierarchy";  // add to d3-force bundle or install separately

export function GoalLineageTree() {
  const { goalId } = useParams<{ goalId: string }>();
  const navigate = useNavigate();
  const svgRef = useRef<SVGSVGElement>(null);
  const [zoom, setZoom] = useState(1);

  const { data: lineage, isLoading } = useQuery({
    queryKey: ["goal-lineage", goalId],
    queryFn: () => fetch(`/goals/${goalId}/lineage`).then(r => r.json()),  // add to goalsApi
    enabled: !!goalId,
    refetchInterval: 5000,
  });

  // D3 tree layout rendered via SVG imperatively (avoids React reconciliation on each RAF)
  useEffect(() => {
    if (!lineage || !svgRef.current) return;
    // Build hierarchy from nodes + edges
    // Apply d3.hierarchy(root, children) → d3.tree().size([width, height])
    // Render as <g> elements with <rect> nodes and <path> edges
    // Each node: 180px wide × 60px tall, rounded rect
    // Node fill: green=complete, blue=executing, red=failed, gray=pending
    // Edge: cubic bezier
    // Click handler: navigate to /goals/:id
  }, [lineage, zoom]);

  return (
    <div className="flex flex-col h-screen">
      {/* Header */}
      <div className="flex items-center gap-3 p-4 border-b border-border bg-card">
        <button onClick={() => navigate(`/goals/${goalId}`)} className="p-1.5 rounded-lg hover:bg-muted/60 text-muted-foreground" aria-label="Back to goal">
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div>
          <h1 className="text-lg font-bold">Goal Lineage Tree</h1>
          {lineage && (
            <p className="text-xs text-muted-foreground">
              {lineage.total_goals} agents · ${lineage.total_cost_usd?.toFixed(4)} total · {lineage.completed} complete
            </p>
          )}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button onClick={() => setZoom(z => Math.max(0.3, z - 0.1))} className="p-1.5 rounded hover:bg-muted" aria-label="Zoom out"><ZoomOut className="h-4 w-4" /></button>
          <span className="text-xs w-10 text-center">{Math.round(zoom * 100)}%</span>
          <button onClick={() => setZoom(z => Math.min(2, z + 0.1))} className="p-1.5 rounded hover:bg-muted" aria-label="Zoom in"><ZoomIn className="h-4 w-4" /></button>
          <button onClick={() => { /* download SVG as PNG */ }} className="flex items-center gap-1 px-3 py-1.5 text-xs border rounded-lg hover:bg-muted">
            <Download className="h-3.5 w-3.5" /> Export
          </button>
        </div>
      </div>

      {/* Tree canvas */}
      {isLoading ? (
        <div className="flex-1 p-6 grid grid-cols-3 gap-4">
          {Array.from({length: 6}).map((_,i) => <Skeleton key={i} className="h-16 rounded-lg" />)}
        </div>
      ) : (
        <div className="flex-1 overflow-auto bg-background" style={{ cursor: "grab" }}>
          <svg
            ref={svgRef}
            style={{ transform: `scale(${zoom})`, transformOrigin: "top center", transition: "transform 200ms" }}
            aria-label="Goal spawn lineage tree"
            role="img"
          />
        </div>
      )}
    </div>
  );
}
```

Add route to `App.tsx`: `<Route path="goals/:goalId/lineage" element={<GoalLineageTree />} />`

### 4.4 New: MultiAgentLauncherPage at /goals/multi-agent

**File to create**: `agent-verse-frontend/src/features/goals/MultiAgentLauncherPage.tsx`

5-step wizard:

**Step 1 — Goal**: large textarea + VoiceGoalInput + CostEstimateWidget preview
**Step 2 — Strategy**: animated card selector
```typescript
const STRATEGIES = [
  {
    mode: "supervisor",
    title: "AI Supervisor",
    subtitle: "Best for complex, multi-part goals",
    icon: "🧠",
    color: "border-violet-400 bg-violet-50 dark:bg-violet-950/30",
    pros: ["Automatic decomposition", "Parallel execution", "AI synthesis"],
    cons: ["Higher cost", "Less predictable"],
    recommended: true,
  },
  {
    mode: "parallel",
    title: "Parallel Agents",
    subtitle: "Same goal, multiple agents compete",
    icon: "⚡",
    color: "border-blue-400 bg-blue-50 dark:bg-blue-950/30",
    pros: ["Fast execution", "Best result wins"],
    cons: ["Duplicate cost", "Requires agent selection"],
  },
  {
    mode: "civilization",
    title: "Civilization",
    subtitle: "Self-organizing agent society",
    icon: "🌍",
    color: "border-green-400 bg-green-50 dark:bg-green-950/30",
    pros: ["Self-organizing", "Learns over time"],
    cons: ["Requires civilization setup"],
  },
];
```

**Step 3 — Configuration**: mode-specific sliders
**Step 4 — Pre-flight**: calls `/insights/estimate` + `/governance/simulate` in parallel; show results
**Step 5 — Launch**: "Deploy N Agents" button with animation (robot icons fly upward)

Post-launch: immediate redirect to `/goals/:id/lineage` for the submitted goal.

### 4.5 Dashboard — Multi-Agent Widget

**File to modify**: `agent-verse-frontend/src/features/dashboard/DashboardPage.tsx`

Add a "Multi-Agent Activity" card when active goals have sub_goal_ids:
```typescript
const multiAgentGoals = goalsArr.filter((g: any) => g.sub_goal_ids?.length > 0);
const totalSubAgents = multiAgentGoals.reduce((sum: number, g: any) => sum + (g.sub_goal_ids?.length ?? 0), 0);

{multiAgentGoals.length > 0 && (
  <div className="bg-card border border-border rounded-xl p-4">
    <div className="flex items-center justify-between mb-3">
      <h2 className="text-sm font-semibold flex items-center gap-2">
        <GitBranch className="h-4 w-4 text-violet-500" />
        Multi-Agent Activity
      </h2>
      <span className="text-xs text-muted-foreground">{totalSubAgents} sub-agents</span>
    </div>
    {multiAgentGoals.slice(0, 3).map((goal: any) => (
      <div key={goal.id} className="flex items-center gap-2 mb-2">
        <StatusBadge status={goal.status} size="sm" />
        <span className="text-xs truncate flex-1">{goal.goal}</span>
        <span className="text-xs text-muted-foreground">{goal.sub_goal_ids?.length} agents</span>
        <button onClick={() => navigate(`/goals/${goal.id}/lineage`)} className="text-xs text-primary hover:underline">Tree</button>
      </div>
    ))}
  </div>
)}
```

### 4.6 Sidebar Addition

**File to modify**: `agent-verse-frontend/src/components/ui/Sidebar.tsx`

Add under Goals section or Core:
```typescript
{ to: "/goals/multi-agent", icon: GitBranch, label: "Multi-Agent" },
```

### 4.7 Animations

**1. Sub-agent spawn particle burst**: When a new sub-agent row appears in SubAgentGanttView:
```css
@keyframes slideInFromLeft {
  from { transform: translateX(-20px); opacity: 0; }
  to   { transform: translateX(0); opacity: 1; }
}
.animate-slide-in-from-left {
  animation: slideInFromLeft 300ms ease-out forwards;
}
```

**2. Gantt bar fill animation**:
```css
/* Applied to the progress bar div via CSS transition */
transition: width 800ms ease-out;
/* Width starts at 0 on mount, then updated to actual progress */
```

**3. Strategy card selection**:
```css
/* Hover state */
transform: translateY(-4px);
box-shadow: 0 10px 25px rgba(0,0,0,0.12);
transition: all 200ms ease-out;

/* Selected state */
transform: scale(1.02);
ring: 2px solid hsl(var(--primary));
transition: all 150ms ease-out;
```

**4. "Deploy N agents" button animation**: On click, spawn N `<Bot>` icons (Lucide SVG) that fly upward and fade out:
```typescript
const [launching, setLaunching] = useState(false);
// On click: setLaunching(true), create N absolute-positioned Bot icons, animate with CSS
// @keyframes flyUp { 0% { transform: translateY(0) scale(1); opacity: 1 } 100% { transform: translateY(-120px) scale(0.5); opacity: 0 } }
```

**5. Lineage tree node entrance**: New nodes added during polling slide in from parent position using CSS transform.

---

## 5. TypeScript Interfaces

```typescript
// Add to agent-verse-frontend/src/lib/api/client.ts:

export interface GoalLineageNode {
  goal_id: string;
  agent_id: string | null;
  agent_name: string | null;
  depth: number;
  status: string;
  cost_usd: number;
  iterations: number;
  spawned_at: string;
  spawn_reason: string;
  goal_text?: string;
}

export interface GoalLineage {
  root_goal_id: string;
  nodes: GoalLineageNode[];
  edges: Array<{ parent: string; child: string }>;
  total_cost_usd: number;
  total_goals: number;
  completed: number;
  failed: number;
}

export interface MultiAgentGoalResponse extends GoalResponse {
  mode: "supervisor" | "parallel" | "multi_agent";
  sub_goal_ids: string[];
}

// Add to goalsApi:
// getLineage: (id: string) => request<GoalLineage>(`/goals/${id}/lineage`),
// getSubGoals: (id: string) => request<{sub_goals: GoalResponse[]}>(`/goals/${id}/sub-goals`),
// submitMultiAgent: (req: MultiAgentRequest) => request<MultiAgentGoalResponse>("/goals/multi-agent", {method:"POST", body:JSON.stringify(req)}),
```

---

## 6. Testing Strategy

```python
# tests/api/test_multi_agent.py
def test_workflow_mode_supervisor_returns_sub_goal_ids():
    resp = client.post("/goals", json={"goal": "Build app", "workflow_mode": "supervisor"}, headers=_HEADERS)
    assert resp.status_code == 202
    data = resp.json()
    assert data["mode"] == "supervisor"
    assert len(data["sub_goal_ids"]) > 0

def test_workflow_mode_multi_agent_with_agent_ids():
    resp = client.post("/goals", json={"goal": "Analyze", "workflow_mode": "multi_agent", "agent_ids": ["a1","a2"]}, headers=_HEADERS)
    assert resp.status_code == 202
    assert resp.json()["mode"] == "multi_agent"

def test_goal_lineage_endpoint():
    # Create parent + child in goal_lineage table, then query
    resp = client.get("/goals/parent-id/lineage", headers=_HEADERS)
    assert resp.status_code == 200
    assert "nodes" in resp.json()
    assert "edges" in resp.json()

def test_spawn_tool_writes_lineage():
    # Execute spawn tool → assert goal_lineage record created
    pass
```

```typescript
// e2e/multi-agent-gantt.spec.ts
test("submit supervisor goal → Gantt renders with sub-agent rows", async ({ page }) => {
  await setupAuth(page);
  await page.route("**/goals", route => route.fulfill({
    status: 202,
    body: JSON.stringify({ id: "root-1", mode: "supervisor", sub_goal_ids: ["sub-1", "sub-2"], status: "multi_agent" }),
  }));
  await page.goto("/goals");
  // ... submit form, verify Gantt renders
});
```

---

## 7. Add to Sidebar and App.tsx

```typescript
// Sidebar.tsx — add after existing Goals items:
{ to: "/goals/multi-agent", icon: GitBranch, label: "Multi-Agent" },

// App.tsx — add lazy routes:
const MultiAgentLauncherPage = lazy(() => import("@/features/goals/MultiAgentLauncherPage").then(m => ({ default: m.MultiAgentLauncherPage })));
const GoalLineageTree = lazy(() => import("@/features/goals/GoalLineageTree").then(m => ({ default: m.GoalLineageTree })));

// Routes:
<Route path="goals/multi-agent" element={<Suspense fallback={<LoadingSpinner/>}><MultiAgentLauncherPage /></Suspense>} />
<Route path="goals/:goalId/lineage" element={<Suspense fallback={<LoadingSpinner/>}><GoalLineageTree /></Suspense>} />
```

---

## 8. Amendments — World-Class Completeness Fixes

### 8.1 PostgreSQL FROM DUAL Bug Fix (Section 3.4)

The `GET /goals/{id}/lineage` query in Section 3.4 uses `FROM DUAL`, which is Oracle syntax.
PostgreSQL does not have a `DUAL` table. Replace the UNION query:

```sql
-- WRONG (Oracle / MySQL syntax — will fail on PostgreSQL):
-- SELECT null, null, :root_id, null, null, null, null, 0, NOW(), '' FROM DUAL

-- CORRECT (PostgreSQL uses VALUES for literal rows):
SELECT
    gl.id, gl.root_goal_id, gl.parent_goal_id, gl.child_goal_id,
    gl.parent_agent_id, gl.child_agent_id, gl.civilization_id,
    gl.spawn_reason, gl.depth, gl.spawned_at, gl.tenant_id
FROM goal_lineage gl
WHERE gl.root_goal_id = :root_id AND gl.tenant_id = :tid

UNION ALL

-- Inject the root node as a synthetic row so the tree always has a root:
SELECT
    'root' AS id,
    :root_id AS root_goal_id,
    NULL AS parent_goal_id,
    :root_id AS child_goal_id,
    NULL AS parent_agent_id,
    NULL AS child_agent_id,
    NULL AS civilization_id,
    '' AS spawn_reason,
    0 AS depth,
    NOW() AS spawned_at,
    :tid AS tenant_id

ORDER BY depth ASC
```

The root node row is filtered out of the `nodes` array in Python (where `id = 'root'`) and used
only to ensure the lineage graph always has a visible root even if `goal_lineage` contains no
rows yet (e.g., for goals submitted before migration 0050).

---

### 8.2 GoalLineageTree — Concrete D3 Implementation (replaces Section 4.3 comment-only useEffect)

Replace the `// Build hierarchy... // Apply d3.hierarchy...` comment block in `GoalLineageTree`
with a working imperative SVG renderer. This avoids the `d3-hierarchy` import dependency by
doing the layout manually:

```typescript
useEffect(() => {
  if (!lineage || !svgRef.current || lineage.nodes.length === 0) return;

  // Build adjacency map for simple parent-child traversal
  const nodeMap = new Map(
    lineage.nodes.map(n => [n.goal_id, { ...n, children: [] as any[] }])
  );
  const roots: any[] = [];

  lineage.edges.forEach(e => {
    const parent = nodeMap.get(e.parent);
    const child = nodeMap.get(e.child);
    if (parent && child) parent.children.push(child);
  });

  lineage.nodes.forEach(n => {
    if (!lineage.edges.some(e => e.child === n.goal_id)) {
      roots.push(nodeMap.get(n.goal_id));
    }
  });

  const rootData = roots[0] ?? { goal_id: lineage.root_goal_id, children: [] };

  const svg = svgRef.current;
  svg.innerHTML = "";

  const NODE_W = 180, NODE_H = 60, H_GAP = 40, V_GAP = 80;

  // Manual recursive tree layout (avoids d3-hierarchy import complexity)
  const positioned: Array<{ node: any; x: number; y: number }> = [];

  function layout(node: any, depth: number, xOffset: number): number {
    const children = node.children ?? [];
    if (children.length === 0) {
      positioned.push({ node, x: xOffset, y: depth * (NODE_H + V_GAP) });
      return xOffset + NODE_W + H_GAP;
    }
    const startX = xOffset;
    let nextX = xOffset;
    children.forEach((child: any) => {
      nextX = layout(child, depth + 1, nextX);
    });
    const centerX = (startX + nextX - H_GAP - NODE_W) / 2;
    positioned.push({ node, x: centerX, y: depth * (NODE_H + V_GAP) });
    return nextX;
  }

  layout(rootData, 0, 0);

  const totalW = Math.max(...positioned.map(p => p.x + NODE_W)) + 40;
  const totalH = Math.max(...positioned.map(p => p.y + NODE_H)) + 40;
  svg.setAttribute("width", String(totalW));
  svg.setAttribute("height", String(totalH));
  svg.setAttribute("viewBox", `0 0 ${totalW} ${totalH}`);

  const posMap = new Map(positioned.map(p => [p.node.goal_id, p]));

  const STATUS_FILL: Record<string, string> = {
    complete: "#dcfce7",
    completed: "#dcfce7",
    executing: "#dbeafe",
    planning: "#fef3c7",
    failed: "#fee2e2",
    cancelled: "#f3f4f6",
  };

  // Draw edges first (so nodes render on top)
  lineage.edges.forEach(e => {
    const parent = posMap.get(e.parent);
    const child = posMap.get(e.child);
    if (!parent || !child) return;

    const px = parent.x + NODE_W / 2;
    const py = parent.y + NODE_H;
    const cx = child.x + NODE_W / 2;
    const cy = child.y;

    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute(
      "d",
      `M${px},${py} C${px},${py + V_GAP / 2} ${cx},${cy - V_GAP / 2} ${cx},${cy}`
    );
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "hsl(var(--border))");
    path.setAttribute("stroke-width", "1.5");
    svg.appendChild(path);
  });

  // Draw nodes
  positioned.forEach(({ node, x, y }) => {
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.style.cursor = "pointer";
    g.addEventListener("click", () => navigate(`/goals/${node.goal_id}`));

    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", String(x));
    rect.setAttribute("y", String(y));
    rect.setAttribute("width", String(NODE_W));
    rect.setAttribute("height", String(NODE_H));
    rect.setAttribute("rx", "8");
    rect.setAttribute("fill", STATUS_FILL[node.status] ?? "#f9fafb");
    rect.setAttribute("stroke", "hsl(var(--border))");
    rect.setAttribute("stroke-width", "1.5");

    const agentText = document.createElementNS("http://www.w3.org/2000/svg", "text");
    agentText.setAttribute("x", String(x + 10));
    agentText.setAttribute("y", String(y + 20));
    agentText.setAttribute("font-size", "11");
    agentText.setAttribute("font-weight", "600");
    agentText.setAttribute("fill", "#111827");
    agentText.textContent = (node.agent_name ?? node.goal_id).slice(0, 22);

    const costText = document.createElementNS("http://www.w3.org/2000/svg", "text");
    costText.setAttribute("x", String(x + 10));
    costText.setAttribute("y", String(y + 38));
    costText.setAttribute("font-size", "10");
    costText.setAttribute("fill", "#6b7280");
    costText.textContent = `$${(node.cost_usd ?? 0).toFixed(4)} · ${node.status}`;

    g.appendChild(rect);
    g.appendChild(agentText);
    g.appendChild(costText);
    svg.appendChild(g);
  });
}, [lineage, zoom, navigate]);
```

---

### 8.3 SupervisorAgent — Define `_emit_event` and `_write_lineage` (Section 3.2)

Section 3.2 calls `await self._emit_event(...)` and `await self._write_lineage(...)` but does
not define them. Add these methods to `SupervisorAgent`:

**`_emit_event`:**
```python
async def _emit_event(self, goal_id: str, event: dict) -> None:
    """Emit an SSE event to the parent goal's stream via GoalService pub/sub.
    Non-critical — supervisor continues if this fails.
    """
    if not goal_id or self._goal_service is None:
        return
    try:
        # Delegates to GoalService's internal SSE broadcast mechanism
        await self._goal_service._broadcast_event(goal_id=goal_id, event=event)
    except Exception:
        pass
```

**`_write_lineage`:**
```python
async def _write_lineage(
    self,
    parent_goal_id: str,
    child_goal_id: str,
    spawn_reason: str,
    tenant_id: str,
) -> None:
    """Persist parent→child relationship to goal_lineage DB table.
    Resolves root_goal_id by chasing the lineage chain upward one level.
    Non-critical — supervisor continues if this fails.
    """
    db = getattr(self._goal_service, "_db", None)
    if db is None:
        return
    import uuid
    from sqlalchemy import text as _t
    try:
        async with db() as session:
            await session.execute(_t("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id})
            await session.execute(_t("""
                INSERT INTO goal_lineage
                    (id, root_goal_id, parent_goal_id, child_goal_id,
                     tenant_id, depth, spawn_reason)
                VALUES (
                    :id,
                    COALESCE(
                        (SELECT root_goal_id FROM goal_lineage
                         WHERE child_goal_id = :parent AND tenant_id = :tid
                         LIMIT 1),
                        :parent
                    ),
                    :parent, :child, :tid,
                    COALESCE(
                        (SELECT depth + 1 FROM goal_lineage
                         WHERE child_goal_id = :parent AND tenant_id = :tid
                         LIMIT 1),
                        1
                    ),
                    :reason
                )
                ON CONFLICT (child_goal_id) DO NOTHING
            """), {
                "id": str(uuid.uuid4()),
                "parent": parent_goal_id,
                "child": child_goal_id,
                "tid": tenant_id,
                "reason": spawn_reason[:200],
            })
            await session.commit()
    except Exception:
        pass
```

`_goal_service` must be passed to `SupervisorAgent.__init__` and stored as `self._goal_service`.
If the supervisor is constructed without a goal_service (e.g., in tests), both helpers are
no-ops.

---

### 8.4 N+1 Polling Fix — SubAgentGanttView Batch Fetch (Section 4.2)

The current `SubAgentGanttView` implementation issues one HTTP request per sub-goal ID:
```typescript
// Current — N individual requests:
await Promise.allSettled(subGoalIds.map(id => goalsApi.get(id)))
```

This is N+1 for large supervisor plans. Replace with a single batch call when the endpoint
exists, falling back to bounded parallel fetches:

```typescript
queryFn: async () => {
  // Prefer the batch sub-goals endpoint (single DB query, no N+1):
  if (goalsApi.getSubGoals) {
    const data = await goalsApi.getSubGoals(parentGoalId);
    return data.sub_goals ?? [];
  }
  // Fallback: bounded parallel fetches (cap at 10 to limit blast radius)
  const results = await Promise.allSettled(
    subGoalIds.slice(0, 10).map(id => goalsApi.get(id).catch(() => null))
  );
  return results
    .filter((r): r is PromiseFulfilledResult<any> => r.status === "fulfilled" && r.value !== null)
    .map(r => r.value);
},
```

The `GET /goals/{id}/sub-goals` endpoint defined in Section 3.4 resolves all children in a
single `JOIN goals ON goal_lineage.child_goal_id = goals.goal_id WHERE parent_goal_id = ?`
query, eliminating the N+1 entirely once the backend is deployed.

---

### 8.5 Mobile Responsiveness, Error States, and Motion

#### Mobile Responsiveness

**SubAgentGanttView:**
```typescript
// Cost column hidden on small screens:
<LiveCostTicker
  currentCost={goal.cost_usd ?? 0}
  isRunning={isLive}
  className="text-[10px] hidden sm:block"  // hide on mobile
/>
// Summary bar switches to column layout:
<div className="flex flex-col sm:flex-row sm:items-center justify-between text-xs text-muted-foreground gap-1">
```

**GoalLineageTree SVG:**
```typescript
// Set preserveAspectRatio so the tree scales on narrow viewports:
svg.setAttribute("preserveAspectRatio", "xMidYMid meet");

// Wrap the SVG in a scrollable container:
<div className="flex-1 overflow-auto bg-background" style={{ cursor: "grab" }}>
  <svg ref={svgRef} ... />
</div>
```

#### Error / Empty States

**SubAgentGanttView — no sub-agents yet:**
```typescript
if (subGoals.length === 0 && !isLoading) {
  return (
    <EmptyState
      title="No sub-agents yet"
      description="Sub-agents will appear here once the supervisor decomposes the goal"
    />
  );
}
```

**GoalLineageTree — no lineage data:**
```typescript
if (!lineage || lineage.nodes.length === 0) {
  return (
    <EmptyState
      icon={GitBranch}
      title="No lineage data"
      description="This goal has no spawn lineage recorded"
    />
  );
}
```

**MultiAgentLauncherPage — network error on submit:**
```typescript
onError: (e) => toast({ kind: "error", message: "Failed to submit multi-agent goal" }),
```

#### prefers-reduced-motion

Add to `agent-verse-frontend/src/index.css` (same block as civilization spec Section 9.7):
```css
@media (prefers-reduced-motion: reduce) {
  .animate-slide-in-from-left,
  .strategy-card-hover,
  .gantt-bar-fill,
  .lineage-node-entrance {
    animation: none !important;
    transition: none !important;
  }
}
```
