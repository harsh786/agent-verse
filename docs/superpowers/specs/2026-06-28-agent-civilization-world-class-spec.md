# Agent Civilization — World-Class Specification

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Transform Agent Civilization from an architecturally-complete but partially-wired system into a fully-operational, visually stunning multi-agent society with real-time animated visualization, live reputation tracking, structured debate transcripts, and true in-agent spawning capability.

**Architecture:** Fix two critical wiring gaps (Settings flag + spawn tool registration), add 4 new DB tables for history/lineage, enhance 15 API endpoints, build 8 new frontend components with CSS/RAF animations, and add a Zustand civilization store.

**Tech Stack:** Python 3.12 · FastAPI · LangGraph · SQLAlchemy · @xyflow/react · D3-hierarchy · Recharts · Zustand · Tailwind · CSS Keyframes · requestAnimationFrame

---

## 1. Vision & Overview

Agent Civilization is the crown jewel of AgentVerse — a self-organizing society of AI agents that spawn, collaborate, debate, learn, and retire autonomously under a constitutional governance framework. The world-class target state:

- **Visual**: a pulsing force-graph where agent nodes glow green when active, orange when debating, red when breaching constitution limits; new agents expand from their parent with a spring animation; bus messages travel as directional particles along graph edges
- **Operational**: users can watch in real-time as the civilization routes goals, governors evaluate spawn requests, agents write to the shared blackboard, and learnings get promoted to long-term memory
- **Controllable**: users can edit the constitution, adjust budgets, kill individual agents, pause/resume the entire civilization, and roll back constitution changes

**Key design principles:**
1. Every state change emits an SSE event — the frontend is event-driven, not polling-dependent
2. Reputation is the civilization's currency — visualized as node border thickness, sparklines, and heatmaps
3. The spawn tool is a first-class agent capability — agents can request new specialists mid-execution
4. Debates produce structured transcripts, not raw bus messages

---

## 2. Current State Assessment

### What is implemented (with evidence)

| Component | File | Status |
|-----------|------|--------|
| Constitution validation | `civilization/constitution.py:14` | ✅ Real, deterministic |
| Governor spawn/retire | `civilization/governor.py:62-216` | ✅ Real, DB-backed |
| Society EWMA reputation | `civilization/society.py:131` | ✅ Real, alpha=0.2 |
| Redis+Postgres bus | `civilization/bus.py:46` | ✅ Real, dual-write |
| Learning pipeline | `civilization/learning.py:91` | ✅ Real, EvalRunner-gated |
| Orchestrator | `civilization/orchestrator.py:65` | ✅ Real |
| 7 DB tables + RLS | `migrations/0045_civilization.py` | ✅ Real |
| 15 REST + SSE + WS | `api/civilization.py` | ✅ Real |
| ReactFlow force graph | `features/civilization/CivilizationMap.tsx` | ✅ Real |
| Agent inspector drawer | `features/civilization/AgentInspectorDrawer.tsx` | ✅ Real |
| Constitution editor | `features/civilization/ConstitutionEditor.tsx` | ✅ Real |

### Critical gaps (must fix first)

**GAP C1**: `civilization_enabled` read via `getattr(settings, "civilization_enabled", False)` in `api/civilization.py:_require_feature_enabled()` — but `civilization_enabled` is NOT in the `Settings` class (`app/core/config.py`). The `getattr` fallback returns `False`, making the entire feature permanently disabled unless the field is added to Settings.

**GAP C2**: `spawn_tool.py:10` defines `SPAWN_TOOL_DEFINITION` and `execute_spawn_tool()` but these are never registered in `AgentGraph.__init__` or any tool list. Agents cannot call `civilization_spawn` during execution — the core automatic spawning capability is unreachable.

### High gaps

- **Debate viewer** (`DebateViewer.tsx:21`): shows raw `bus_messages` with `topic='debate'`, not structured `DebateOrchestrator` round-by-round transcripts
- **Spawn history chart** (`CivilizationMetrics.tsx:61`): `spawnHistory` prop is always `[]` — `CivilizationPage.tsx:296` never passes it — bar chart is dead code
- **Agent node current_step** (`AgentNode.tsx:58`): renders `data.current_step` which `layoutNodes()` never maps from API response — always `undefined`
- **AgentInspectorDrawer**: no auto-refresh — shows stale point-in-time snapshot
- **Reputation history**: no time-series chart, no sparklines
- **Governor datetime bug** (`governor.py:216`): `last_active.replace(tzinfo=None)` — offset-naive comparison against Postgres TIMESTAMPTZ — `auto_retire_idle()` may never fire

---

## 3. Backend Specification

### 3.1 Critical Fix A — Wire civilization_enabled into Settings

**File to modify**: `agent-verse-backend/app/core/config.py`

Add to `Settings` class after the existing `civilization_enabled: bool = False` line (it should already exist but may not — verify and add if missing):

```python
# --- Agent Civilization ---
civilization_enabled: bool = False
civilization_max_agents_per_tenant: int = 50
civilization_max_spawn_depth: int = 5
civilization_default_budget_usd: float = 10.0
civilization_tick_interval_seconds: int = 30
```

**File to modify**: `agent-verse-backend/infra/docker-compose.yml`

Add to backend service environment:
```yaml
CIVILIZATION_ENABLED: "true"
```

### 3.2 Critical Fix B — Register spawn tool in AgentGraph

**File to modify**: `agent-verse-backend/app/agent/graph.py`

In `AgentGraph.__init__()`, after the existing tool/MCP initialization, add:

```python
# Register civilization spawn tool if civilization_id is in initial_context
self._civilization_id: str | None = (
    initial_context.get("civilization_id") if isinstance(initial_context, dict) else None
)
self._civilization_spawn_enabled: bool = bool(self._civilization_id)
```

In `_node_execute()`, before the tool dispatch logic, add civilization spawn tool as a callable:

```python
if self._civilization_spawn_enabled and tool_name == "civilization_spawn":
    from app.civilization.spawn_tool import execute_spawn_tool
    from app.civilization.governor import Governor
    governor = Governor(
        civilization_id=self._civilization_id,
        tenant_ctx=self._tenant_ctx,
        db=self._db,
        goal_service=self._goal_service,
    )
    result = await execute_spawn_tool(
        arguments=tool_arguments,
        governor=governor,
        goal_service=self._goal_service,
        tenant_ctx=self._tenant_ctx,
    )
    # Emit SSE event for spawn
    await self._emit_event({
        "type": "child_agent_spawned",
        "parent_agent_id": self._agent_id,
        "child_agent_id": result.get("agent_id"),
        "child_goal_id": result.get("goal_id"),
        "depth": result.get("depth", 0),
        "capability": tool_arguments.get("capability", ""),
    })
    return result
```

**File to modify**: `agent-verse-backend/app/civilization/spawn_tool.py`

Add `civilization_spawn` to `SPAWN_TOOL_DEFINITION` available for agents:
```python
# Make available as a discoverable tool (not just via civilization API)
SPAWN_TOOL_DEFINITION = {
    "name": "civilization_spawn",
    "description": "Request spawning of a new specialist agent within the civilization to handle a specific sub-capability. The Governor will evaluate the request against the Constitution and spawn if approved.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "requested_capability": {"type": "string", "description": "What capability the new agent needs (e.g. 'Jira specialist', 'data analyzer')"},
            "goal_text": {"type": "string", "description": "The goal to assign to the spawned agent"},
            "budget_usd": {"type": "number", "description": "Budget allocation for the spawned agent (default: 1.0)"},
        },
        "required": ["requested_capability", "goal_text"],
    },
}
```

### 3.3 Fix Governor datetime comparison

**File to modify**: `agent-verse-backend/app/civilization/governor.py` (line ~216)

```python
# BEFORE (broken — naive vs aware datetime):
time_since_active = (datetime.now() - agent.last_active_at.replace(tzinfo=None)).total_seconds()

# AFTER (correct — always UTC-aware):
from datetime import timezone as _tz
now_utc = datetime.now(_tz.utc)
last_active = agent.last_active_at
if last_active.tzinfo is None:
    last_active = last_active.replace(tzinfo=_tz.utc)
time_since_active = (now_utc - last_active).total_seconds()
```

### 3.4 New Database Tables — Migration 0049

**File to create**: `agent-verse-backend/app/db/migrations/versions/0049_civilization_history.py`

```python
"""Add civilization history tables: reputation_history, constitution_history, debate_transcripts, spawn_lineage."""
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Reputation time-series (EWMA snapshots every governor tick)
    op.execute("""
        CREATE TABLE IF NOT EXISTS civilization_reputation_history (
            id              TEXT PRIMARY KEY,
            civilization_id TEXT NOT NULL,
            agent_id        TEXT NOT NULL,
            tenant_id       TEXT NOT NULL,
            reputation      FLOAT NOT NULL,
            recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_civ_rep_hist_civ_agent ON civilization_reputation_history (civilization_id, agent_id, recorded_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_civ_rep_hist_tenant ON civilization_reputation_history (tenant_id)")

    # Constitution version history
    op.execute("""
        CREATE TABLE IF NOT EXISTS civilization_constitution_history (
            id              TEXT PRIMARY KEY,
            civilization_id TEXT NOT NULL,
            tenant_id       TEXT NOT NULL,
            constitution    JSONB NOT NULL,
            changed_by      TEXT NOT NULL DEFAULT 'user',
            change_reason   TEXT NOT NULL DEFAULT '',
            changed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_civ_const_hist_civ ON civilization_constitution_history (civilization_id, changed_at DESC)")

    # Structured debate transcripts (round-by-round, not raw bus messages)
    op.execute("""
        CREATE TABLE IF NOT EXISTS civilization_debate_transcripts (
            id               TEXT PRIMARY KEY,
            debate_id        TEXT NOT NULL,
            civilization_id  TEXT NOT NULL,
            tenant_id        TEXT NOT NULL,
            round_number     INTEGER NOT NULL,
            proposer_agent_id TEXT NOT NULL,
            critic_agent_id   TEXT,
            proposal_text    TEXT NOT NULL DEFAULT '',
            critique_text    TEXT NOT NULL DEFAULT '',
            counter_proposal TEXT NOT NULL DEFAULT '',
            consensus_reached BOOLEAN NOT NULL DEFAULT FALSE,
            winning_proposal TEXT NOT NULL DEFAULT '',
            confidence       FLOAT NOT NULL DEFAULT 0.0,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_civ_debate_trans_debate ON civilization_debate_transcripts (debate_id, round_number)")

    # Goal spawn lineage (parent → child relationships across all civilizations)
    op.execute("""
        CREATE TABLE IF NOT EXISTS goal_spawn_lineage (
            id               TEXT PRIMARY KEY,
            root_goal_id     TEXT NOT NULL,
            parent_goal_id   TEXT,
            child_goal_id    TEXT NOT NULL,
            parent_agent_id  TEXT,
            child_agent_id   TEXT,
            civilization_id  TEXT,
            spawn_reason     TEXT NOT NULL DEFAULT '',
            depth            INTEGER NOT NULL DEFAULT 0,
            spawned_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            tenant_id        TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_spawn_lineage_root ON goal_spawn_lineage (root_goal_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_spawn_lineage_tenant ON goal_spawn_lineage (tenant_id)")

    # Apply RLS to all new tables
    for table in ["civilization_reputation_history", "civilization_constitution_history",
                  "civilization_debate_transcripts", "goal_spawn_lineage"]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (tenant_id = current_setting('app.tenant_id', TRUE))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))
        """)

def downgrade() -> None:
    for table in ["goal_spawn_lineage", "civilization_debate_transcripts",
                  "civilization_constitution_history", "civilization_reputation_history"]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
```

### 3.5 New/Enhanced API Endpoints

All new endpoints are added to `agent-verse-backend/app/api/civilization.py` under the existing `router = APIRouter(prefix="/civilizations")`.

#### GET /civilizations/{civ_id}/reputation-history
```
Query params: agent_id (optional), hours (default=24, max=168)
Response: {
  "civilization_id": str,
  "series": {
    "<agent_id>": [
      {"ts": "ISO8601", "reputation": 0.73, "agent_id": str},
      ...
    ]
  },
  "agents": [{"agent_id": str, "name": str, "current_reputation": float}],
  "period_hours": int
}
Business logic: SELECT from civilization_reputation_history WHERE civilization_id=? AND recorded_at >= NOW() - (hours || ' hours')::INTERVAL ORDER BY recorded_at ASC
```

#### GET /civilizations/{civ_id}/debates/{debate_id}/transcript
```
Response: {
  "debate_id": str,
  "civilization_id": str,
  "rounds": [
    {
      "round_number": 1,
      "proposer_agent_id": str,
      "proposer_name": str,
      "critic_agent_id": str,
      "critic_name": str,
      "proposal_text": str,
      "critique_text": str,
      "counter_proposal": str,
      "consensus_reached": bool
    }
  ],
  "final_decision": str,
  "winning_confidence": float,
  "total_rounds": int
}
Business logic: SELECT from civilization_debate_transcripts WHERE debate_id=? ORDER BY round_number
Fallback: if no structured transcript exists, return raw bus messages formatted as fake rounds
```

#### GET /civilizations/{civ_id}/constitution/history
```
Response: {
  "history": [
    {
      "id": str,
      "constitution": {...},
      "changed_by": str,
      "change_reason": str,
      "changed_at": "ISO8601",
      "diff_summary": "max_agents: 5→8, max_depth: 3→5"
    }
  ]
}
Business logic: SELECT from civilization_constitution_history ORDER BY changed_at DESC LIMIT 50
```

#### POST /civilizations/{civ_id}/constitution/rollback/{history_id}
```
Request body: {"reason": "Reverting due to budget overrun"}
Business logic:
  1. Fetch constitution from history record
  2. Save current constitution to history (for audit)
  3. UPDATE civilizations SET constitution=history.constitution WHERE id=civ_id
  4. Emit SSE event {type: "constitution_rolled_back", from_id: current_history_id, to_id: history_id}
Response: {success: true, constitution: {...}}
```

#### GET /civilizations/{civ_id}/budget/burn-rate
```
Response: {
  "spent_usd": float,
  "total_budget_usd": float,
  "utilization_pct": float,
  "burn_rate_usd_per_hour": float,
  "eta_exhaustion_hours": float | null,
  "top_spenders": [{"agent_id": str, "name": str, "spent_usd": float}]
}
Business logic:
  - burn_rate: last 1h of goal costs / 1h
  - eta: (total_budget - spent) / burn_rate (null if burn_rate == 0)
  - top_spenders: from civilization_agents.budget_spent_usd ORDER BY DESC LIMIT 5
```

#### GET /civilizations/{civ_id}/spawn-lineage
```
Query params: depth (default=5), agent_id (filter to subtree)
Response: {
  "nodes": [{"id": str, "agent_id": str, "name": str, "depth": int, "status": str, "goal_success_count": int, "goal_fail_count": int}],
  "edges": [{"parent": str, "child": str, "spawned_at": "ISO8601", "spawn_reason": str}]
}
Business logic: recursive CTE over goal_spawn_lineage + civilization_agents JOIN
```

#### Enhanced GET /civilizations/{civ_id}/stream (SSE)
Add these new event types to the existing stream:
```python
# In civilization/events.py — add new event type constants:
EVT_AGENT_SPAWNED = "agent_spawned"
EVT_CONSTITUTION_VIOLATED = "constitution_violated"
EVT_CONSTITUTION_ROLLED_BACK = "constitution_rolled_back"
EVT_BUDGET_WARNING = "budget_warning"          # at 75% and 90% consumed
EVT_REPUTATION_MILESTONE = "reputation_milestone"  # agent crosses 0.8 or drops below 0.3
EVT_DEBATE_STARTED = "debate_started"
EVT_DEBATE_CONCLUDED = "debate_concluded"
EVT_LEARNING_PROMOTED = "learning_promoted"
EVT_LEARNING_REJECTED = "learning_rejected"
EVT_AGENT_RETIRED = "agent_retired"
```

Each event payload schema:
```python
# agent_spawned
{"type": "agent_spawned", "agent_id": str, "parent_agent_id": str, "name": str, "capability": str, "depth": int, "budget_usd": float, "ts": ISO8601}

# reputation_milestone
{"type": "reputation_milestone", "agent_id": str, "name": str, "old_reputation": float, "new_reputation": float, "direction": "up"|"down", "milestone": "excellent"|"warning"|"critical", "ts": ISO8601}

# learning_promoted
{"type": "learning_promoted", "learning_id": str, "eval_score": float, "memory_id": str, "preview": str, "ts": ISO8601}

# budget_warning
{"type": "budget_warning", "threshold_pct": 75|90, "spent_usd": float, "total_usd": float, "eta_hours": float, "ts": ISO8601}
```

### 3.6 Reputation History Persistence

**File to modify**: `agent-verse-backend/app/civilization/society.py`

In `update_reputation()` method, after the EWMA calculation and DB update, write to `civilization_reputation_history`:
```python
async def _persist_reputation_snapshot(self, agent_id: str, reputation: float, db) -> None:
    """Write reputation snapshot to history table for time-series charts."""
    import uuid
    from sqlalchemy import text as _t
    async with db() as session:
        await session.execute(
            _t("SET LOCAL app.tenant_id = :tid"), {"tid": self._tenant_id}
        )
        await session.execute(
            _t("""
               INSERT INTO civilization_reputation_history
               (id, civilization_id, agent_id, tenant_id, reputation, recorded_at)
               VALUES (:id, :civ, :agent, :tenant, :rep, NOW())
            """),
            {"id": str(uuid.uuid4()), "civ": self._civilization_id,
             "agent": agent_id, "tenant": self._tenant_id, "rep": reputation}
        )
        await session.commit()
```

### 3.7 Constitution History Persistence

**File to modify**: `agent-verse-backend/app/api/civilization.py`

In `update_constitution()` endpoint, before applying the new constitution, save old one to history:
```python
# Before UPDATE:
await session.execute(_t("""
    INSERT INTO civilization_constitution_history
    (id, civilization_id, tenant_id, constitution, changed_by, change_reason, changed_at)
    VALUES (:id, :civ, :tenant, :old_const::jsonb, :by, :reason, NOW())
"""), {
    "id": str(uuid.uuid4()),
    "civ": civ_id,
    "tenant": tenant.tenant_id,
    "old_const": json.dumps(current_constitution),
    "by": f"api:{tenant.api_key_id}",
    "reason": body.get("reason", ""),
})
```

### 3.8 Society Cache on app.state

**File to modify**: `agent-verse-backend/app/main.py`

Add civilization society cache to app.state to avoid O(N) DB scan on every graph request:
```python
# In create_app(), after existing service binding:
app.state.civilization_societies: dict[str, Any] = {}  # (tenant_id, civ_id) → Society

# Helper used by civilization API:
def get_or_create_society(app, tenant_id: str, civ_id: str) -> "Society":
    key = f"{tenant_id}:{civ_id}"
    if key not in app.state.civilization_societies:
        from app.civilization.society import Society
        app.state.civilization_societies[key] = Society(
            civilization_id=civ_id, tenant_id=tenant_id
        )
    return app.state.civilization_societies[key]
```

---

## 4. Frontend Specification

### 4.1 New Zustand Store

**File to create**: `agent-verse-frontend/src/stores/civilizationStore.ts`

```typescript
import { create } from "zustand";

interface TimeSeriesPoint { ts: string; value: number; }
interface CivEvent { id: string; type: string; payload: Record<string, unknown>; ts: string; }
interface AgentHealthSnapshot { agentId: string; status: string; reputation: number; currentStep?: string; }

interface CivilizationStore {
  activeCivilizationId: string | null;
  liveEvents: CivEvent[];                              // capped at 500
  agentHealthMap: Record<string, AgentHealthSnapshot>;
  reputationHistory: Record<string, TimeSeriesPoint[]>; // agentId → time series
  breachScore: number;                                 // 0.0 → 1.0
  spawnHistory: { ts: string; count: number }[];       // 60-second buckets

  setActiveCivilization: (id: string | null) => void;
  addEvent: (event: CivEvent) => void;
  updateAgentHealth: (agentId: string, snapshot: AgentHealthSnapshot) => void;
  setBreachScore: (score: number) => void;
  addReputationPoint: (agentId: string, point: TimeSeriesPoint) => void;
  recordSpawn: () => void;
}

export const useCivilizationStore = create<CivilizationStore>((set) => ({
  activeCivilizationId: null,
  liveEvents: [],
  agentHealthMap: {},
  reputationHistory: {},
  breachScore: 0,
  spawnHistory: [],

  setActiveCivilization: (id) => set({ activeCivilizationId: id }),

  addEvent: (event) => set((s) => ({
    liveEvents: [...s.liveEvents.slice(-499), event],
    // Update breach score from constitution_violated events
    breachScore: event.type === "constitution_violated"
      ? Math.min(1, s.breachScore + 0.1)
      : event.type === "constitution_restored"
      ? 0
      : s.breachScore,
  })),

  updateAgentHealth: (agentId, snapshot) => set((s) => ({
    agentHealthMap: { ...s.agentHealthMap, [agentId]: snapshot },
  })),

  setBreachScore: (score) => set({ breachScore: score }),

  addReputationPoint: (agentId, point) => set((s) => ({
    reputationHistory: {
      ...s.reputationHistory,
      [agentId]: [...(s.reputationHistory[agentId] ?? []).slice(-288), point], // 24h at 5min intervals
    },
  })),

  recordSpawn: () => set((s) => {
    const now = Math.floor(Date.now() / 60000) * 60000;
    const existing = s.spawnHistory.findIndex(b => new Date(b.ts).getTime() === now);
    if (existing >= 0) {
      const updated = [...s.spawnHistory];
      updated[existing] = { ...updated[existing], count: updated[existing].count + 1 };
      return { spawnHistory: updated };
    }
    return { spawnHistory: [...s.spawnHistory.slice(-60), { ts: new Date(now).toISOString(), count: 1 }] };
  }),
}));
```

### 4.2 CivilizationMap — World-Class Animated Visualization

**File to modify**: `agent-verse-frontend/src/features/civilization/CivilizationMap.tsx`

#### Spawn animation (CSS + ReactFlow)
When an `agent_spawned` SSE event arrives, the new node needs an entrance animation:

```css
/* In a <style> tag or global CSS */
@keyframes agentSpawn {
  0%   { transform: scale(0) translateY(-20px); opacity: 0; }
  60%  { transform: scale(1.15) translateY(0); opacity: 0.9; }
  100% { transform: scale(1) translateY(0); opacity: 1; }
}
.agent-node-entering {
  animation: agentSpawn 400ms cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
}

@keyframes agentRetire {
  0%   { transform: scale(1); opacity: 1; }
  100% { transform: scale(0); opacity: 0; }
}
.agent-node-retiring {
  animation: agentRetire 300ms ease-in forwards;
}
```

Track new node IDs in a Set with a 500ms TTL:
```typescript
const [newNodeIds, setNewNodeIds] = useState<Set<string>>(new Set());

useEffect(() => {
  const lastEvent = liveEvents[liveEvents.length - 1];
  if (lastEvent?.type === "agent_spawned") {
    const agentId = lastEvent.payload.agent_id as string;
    setNewNodeIds(prev => new Set([...prev, agentId]));
    setTimeout(() => setNewNodeIds(prev => {
      const next = new Set(prev);
      next.delete(agentId);
      return next;
    }), 500);
    // Fit view to include new node
    setTimeout(() => reactFlowInstance?.fitView({ padding: 0.15, duration: 600 }), 100);
  }
}, [liveEvents]);
```

Pass `className={newNodeIds.has(node.data.agent_id) ? "agent-node-entering" : ""}` to each node.

#### Bus message particles (requestAnimationFrame)
```typescript
const particlesRef = useRef<Array<{
  id: string; sourceId: string; targetId: string;
  progress: number; speed: number; topic: string;
}>>([]);

const PARTICLE_COLORS: Record<string, string> = {
  spawn: "#6366f1", debate: "#a855f7", findings: "#22c55e", coordination: "#3b82f6", lifecycle: "#f59e0b",
};

// On bus message events, spawn a particle:
useEffect(() => {
  const lastEvent = liveEvents[liveEvents.length - 1];
  if (lastEvent?.type === "bus_message") {
    const { source_agent_id, target_agent_id, topic } = lastEvent.payload as any;
    particlesRef.current.push({
      id: crypto.randomUUID(),
      sourceId: source_agent_id,
      targetId: target_agent_id,
      progress: 0,
      speed: 0.008 + Math.random() * 0.004,
      topic,
    });
  }
}, [liveEvents]);

// RAF loop to animate particles as SVG circles overlaid on the ReactFlow canvas:
useEffect(() => {
  let animId: number;
  const canvas = svgOverlayRef.current; // SVG element positioned absolute over ReactFlow
  function animate() {
    particlesRef.current = particlesRef.current.filter(p => p.progress < 1);
    particlesRef.current.forEach(p => { p.progress += p.speed; });
    // Redraw all particles
    if (canvas) {
      const circles = canvas.querySelectorAll(".bus-particle");
      circles.forEach(c => c.remove());
      particlesRef.current.forEach(p => {
        const source = nodePositions[p.sourceId];
        const target = nodePositions[p.targetId];
        if (!source || !target) return;
        const x = source.x + (target.x - source.x) * p.progress;
        const y = source.y + (target.y - source.y) * p.progress;
        const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        circle.setAttribute("cx", String(x));
        circle.setAttribute("cy", String(y));
        circle.setAttribute("r", "4");
        circle.setAttribute("fill", PARTICLE_COLORS[p.topic] ?? "#9ca3af");
        circle.setAttribute("opacity", String(1 - p.progress * 0.5));
        circle.classList.add("bus-particle");
        canvas.appendChild(circle);
      });
    }
    animId = requestAnimationFrame(animate);
  }
  animId = requestAnimationFrame(animate);
  return () => cancelAnimationFrame(animId);
}, [nodePositions]);
```

#### Breach ambient glow
```typescript
// SVG radialGradient overlay on the ReactFlow wrapper div
const breachScore = useCivilizationStore(s => s.breachScore);

// In JSX, wrap ReactFlow in:
<div className="relative">
  <div
    className="absolute inset-0 pointer-events-none rounded-xl transition-opacity"
    style={{
      background: `radial-gradient(circle at center, rgba(239,68,68,${breachScore * 0.25}) 0%, transparent 70%)`,
      transition: "background 2s ease",
    }}
    aria-hidden="true"
  />
  <ReactFlow ... />
</div>
```

### 4.3 AgentNode — World-Class Custom Node

**File to modify**: `agent-verse-frontend/src/features/civilization/AgentNode.tsx`

```typescript
// Add pulse animation on current_step change:
const [stepChanged, setStepChanged] = useState(false);
const prevStep = useRef(data.current_step);

useEffect(() => {
  if (data.current_step && data.current_step !== prevStep.current) {
    setStepChanged(true);
    prevStep.current = data.current_step;
    setTimeout(() => setStepChanged(false), 600);
  }
}, [data.current_step]);

// Border width scales with reputation (2px = 0.0, 6px = 1.0):
const borderWidth = Math.round(2 + (data.reputation ?? 0.5) * 4);

// Debate ring (when agent is in debate_started state):
const isDebating = data.status === "debating";

// Full node JSX:
return (
  <div
    className={`
      relative rounded-xl px-3 py-2 min-w-[140px] max-w-[200px] cursor-pointer
      transition-all duration-300
      ${stepChanged ? "ring-2 ring-primary ring-offset-1" : ""}
      ${isDebating ? "ring-2 ring-purple-400 animate-pulse" : ""}
    `}
    style={{
      background: STATUS_BG[data.status] ?? "hsl(var(--card))",
      border: `${borderWidth}px solid ${STATUS_BORDER[data.status] ?? "hsl(var(--border))"}`,
    }}
  >
    {/* Active indicator dot */}
    {(data.status === "active" || data.status === "executing") && (
      <span className="absolute -top-1 -right-1 flex h-3 w-3">
        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
        <span className="relative inline-flex rounded-full h-3 w-3 bg-green-500" />
      </span>
    )}

    {/* Reputation bar (bottom of node) */}
    <div className="mt-2 w-full bg-muted/50 rounded-full h-1">
      <div
        className="h-1 rounded-full transition-all duration-500"
        style={{
          width: `${(data.reputation ?? 0.5) * 100}%`,
          background: data.reputation > 0.7 ? "#22c55e" : data.reputation > 0.4 ? "#f59e0b" : "#ef4444",
        }}
        role="progressbar"
        aria-valuenow={Math.round((data.reputation ?? 0.5) * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`Reputation: ${Math.round((data.reputation ?? 0.5) * 100)}%`}
      />
    </div>

    {/* Current step (truncated) */}
    {data.current_step && (
      <p className="text-[9px] text-muted-foreground mt-1 truncate">{data.current_step}</p>
    )}
  </div>
);
```

### 4.4 New: ReputationTimelineChart Component

**File to create**: `agent-verse-frontend/src/features/civilization/components/ReputationTimelineChart.tsx`

```typescript
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ThemedLineChart } from "@/components/charts";
import { Skeleton } from "@/components/ui/Skeleton";
import { apiFetch } from "@/lib/api/client";

interface ReputationSeries {
  agentId: string;
  name: string;
  data: Array<{ ts: string; reputation: number }>;
}

interface Props { civilizationId: string; }

const TIME_RANGES = [
  { label: "1h", hours: 1 },
  { label: "6h", hours: 6 },
  { label: "24h", hours: 24 },
  { label: "7d", hours: 168 },
];

export function ReputationTimelineChart({ civilizationId }: Props) {
  const [hours, setHours] = useState(24);

  const { data, isLoading } = useQuery({
    queryKey: ["civ-reputation-history", civilizationId, hours],
    queryFn: () => apiFetch<{ series: Record<string, any[]>; agents: any[] }>(
      `/civilizations/${civilizationId}/reputation-history?hours=${hours}`
    ),
    refetchInterval: 30_000,
  });

  const lines = Object.entries(data?.series ?? {}).map(([agentId, points], i) => ({
    key: agentId,
    label: data?.agents.find(a => a.agent_id === agentId)?.name ?? agentId.slice(0, 8),
  }));

  const chartData = Object.entries(data?.series ?? {}).reduce((acc, [agentId, points]) => {
    points.forEach((p, i) => {
      if (!acc[i]) acc[i] = { ts: new Date(p.ts).toLocaleTimeString() };
      acc[i][agentId] = p.reputation;
    });
    return acc;
  }, [] as Record<string, any>[]);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">Reputation History</span>
        <div className="flex gap-1">
          {TIME_RANGES.map(r => (
            <button
              key={r.hours}
              onClick={() => setHours(r.hours)}
              className={`px-2 py-0.5 text-xs rounded ${hours === r.hours ? "bg-primary text-primary-foreground" : "bg-muted hover:bg-muted/60"}`}
            >
              {r.label}
            </button>
          ))}
        </div>
      </div>
      {isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : (
        <ThemedLineChart
          data={chartData}
          lines={lines}
          xKey="ts"
          height={160}
          formatValue={(v) => v.toFixed(2)}
        />
      )}
    </div>
  );
}
```

### 4.5 New: ConstitutionHistoryModal

**File to create**: `agent-verse-frontend/src/features/civilization/components/ConstitutionHistoryModal.tsx`

Full component with:
- Modal dialog (role="dialog", aria-modal)
- Timeline list of constitution changes (changed_at, changed_by, change_reason)
- Diff viewer: two-column side-by-side JSON with line-level coloring (added=green, removed=red, unchanged=gray)
- "Rollback" button on each entry → ConfirmModal → POST /civilizations/{civ_id}/constitution/rollback/{history_id}
- "Close" button

### 4.6 CivilizationPage — New Tabs

**File to modify**: `agent-verse-frontend/src/features/civilization/CivilizationPage.tsx`

Add 3 new tabs to the right panel (after existing 7):

**"Reputation" tab:**
```typescript
<ReputationTimelineChart civilizationId={civ.id} />
```

**"Budget" tab:**
```typescript
// BudgetBurnTab component (inline):
// - Circular progress gauge showing spent/total (SVG arc)
// - Projected exhaustion ETA badge
// - ThemedBarChart of per-agent spending
// - "Adjust Budget" button → PATCH /civilizations/{id} with new total_budget_usd
```

**"Spawn Tree" tab:**
```typescript
// Lazy load the SpawnTreePage component
<Suspense fallback={<Skeleton className="h-96" />}>
  <SpawnTreeInline civilizationId={civ.id} />
</Suspense>
```

Also **fix `spawnHistory` prop**: accumulate `agent_spawned` events from `liveEvents` into 60-second buckets and pass to `CivilizationMetrics`:
```typescript
const spawnHistory = useMemo(() => {
  const buckets: Record<number, number> = {};
  liveEvents.filter(e => e.type === "agent_spawned").forEach(e => {
    const bucket = Math.floor(new Date(e.ts).getTime() / 60000) * 60000;
    buckets[bucket] = (buckets[bucket] ?? 0) + 1;
  });
  return Object.entries(buckets)
    .map(([ts, count]) => ({ ts: new Date(Number(ts)).toISOString(), count }))
    .sort((a, b) => a.ts.localeCompare(b.ts));
}, [liveEvents]);
```

**Fix `current_step` mapping**: in `layoutNodes()`, map `member.current_step` from the API response:
```typescript
data: {
  ...member,
  current_step: member.current_step ?? member.last_goal_description ?? undefined,
  // ... other existing mappings
}
```

### 4.7 New: SpawnTreePage at /civilization/:id/spawn-tree

**File to create**: `agent-verse-frontend/src/features/civilization/SpawnTreePage.tsx`

Full D3 collapsible hierarchy tree:
- Use `d3-hierarchy` (already imported transitively through d3-force) for tree layout
- Node colors: green=active, gray=retired/idle, red=failed/retired-with-failures
- Node size proportional to `goal_success_count`
- Edge labels: spawn_reason (truncated)
- Click node → opens AgentInspectorDrawer
- Zoom/pan with CSS transform (no d3-zoom dependency needed for simple cases)
- Depth filter slider (1-5)
- "Export PNG" button using `html-to-image` or canvas approach

### 4.8 AgentInspectorDrawer Enhancements

**File to modify**: `agent-verse-frontend/src/features/civilization/AgentInspectorDrawer.tsx`

1. **Auto-refresh**: add `useQuery` with `refetchInterval: 3000` when drawer is open
2. **Reputation sparkline**: `<ThemedLineChart>` showing last 6h reputation (from reputation history endpoint)
3. **Kill button**: `<ConfirmModal>` → `POST /civilizations/{civ_id}/agents/{agent_id}/kill`
4. **Budget adjustment**: inline slider + save button → `PATCH /civilizations/{civ_id}/agents/{agent_id}/budget`
5. **Elapsed timer**: `requestAnimationFrame` timer showing "Running for Xm Ys" when status=executing

### 4.9 ControlBar Enhancements

**File to modify**: `agent-verse-frontend/src/features/civilization/ControlBar.tsx`

1. **Spawn Agent button**: opens `SpawnAgentModal` with: capability text, goal text, budget slider → `POST /civilizations/{civ_id}/goals`
2. **Budget burn gauge**: circular mini-gauge showing spend/total; updates every 30s via `GET /civilizations/{civ_id}/budget/burn-rate`
3. **Pause/Resume**: add ConfirmModal for pause ("Reason?" textarea) — reason stored in audit log

### 4.10 Animation Specifications

All animations below are to be implemented without external libraries (CSS keyframes + requestAnimationFrame only):

**1. Agent spawn entrance** (`@keyframes agentSpawn`):
```css
@keyframes agentSpawn {
  0%   { transform: scale(0) translateY(-20px); opacity: 0; filter: blur(4px); }
  60%  { transform: scale(1.15) translateY(2px); opacity: 0.85; filter: blur(0); }
  100% { transform: scale(1) translateY(0); opacity: 1; filter: blur(0); }
}
```
Duration: 400ms, easing: cubic-bezier(0.34, 1.56, 0.64, 1) (spring)

**2. Agent retire exit** (`@keyframes agentRetire`):
```css
@keyframes agentRetire {
  0%   { transform: scale(1); opacity: 1; }
  40%  { transform: scale(1.1); opacity: 0.8; }
  100% { transform: scale(0); opacity: 0; filter: blur(8px); }
}
```
Duration: 350ms, easing: ease-in. Plus: 10 SVG circle particles expanding outward radially.

**3. Bus message particles**: See Section 4.2 above — RAF-based, `4px` circles, 120px/s, topic-colored.

**4. Reputation change flash**:
```css
@keyframes repUp   { 0%,100% { box-shadow: none; } 50% { box-shadow: 0 0 12px 4px rgba(34,197,94,0.7); } }
@keyframes repDown { 0%,100% { box-shadow: none; } 50% { box-shadow: 0 0 12px 4px rgba(239,68,68,0.7); } }
```
Applied for 600ms when reputation increases/decreases (tracked in AgentNode by comparing prev/current).

**5. Breach ambient glow**: CSS transition on `background` property of the overlay div — `2s ease` transition makes the glow grow/shrink smoothly.

**6. Debate pulse ring**: when `debate_started` SSE arrives, add CSS class `animate-ping ring-2 ring-purple-400` to both participating agent nodes for 3 seconds.

**7. Learning promotion confetti**: when `learning_promoted` SSE arrives, render 40 SVG stars (5-pointed path) from the LearningLedger row, each with random velocity + fade animation. Use `document.createElementNS` to append directly to body SVG overlay.

**8. Blackboard slide-in**: new `BlackboardEntry` components use `@keyframes slideInDown { from { transform: translateY(-12px); opacity: 0 } to { transform: translateY(0); opacity: 1 } }` over 250ms.

---

## 5. TypeScript Interfaces

```typescript
// agent-verse-frontend/src/lib/api/client.ts — add to civilizationApi section:

export interface CivilizationReputationHistory {
  civilization_id: string;
  series: Record<string, Array<{ ts: string; reputation: number; agent_id: string }>>;
  agents: Array<{ agent_id: string; name: string; current_reputation: number }>;
  period_hours: number;
}

export interface ConstitutionHistoryEntry {
  id: string;
  constitution: Record<string, unknown>;
  changed_by: string;
  change_reason: string;
  changed_at: string;
  diff_summary: string;
}

export interface DebateTranscript {
  debate_id: string;
  civilization_id: string;
  rounds: Array<{
    round_number: number;
    proposer_agent_id: string;
    proposer_name: string;
    critic_agent_id: string;
    critic_name: string;
    proposal_text: string;
    critique_text: string;
    counter_proposal: string;
    consensus_reached: boolean;
  }>;
  final_decision: string;
  winning_confidence: number;
  total_rounds: number;
}

export interface BudgetBurnRate {
  spent_usd: number;
  total_budget_usd: number;
  utilization_pct: number;
  burn_rate_usd_per_hour: number;
  eta_exhaustion_hours: number | null;
  top_spenders: Array<{ agent_id: string; name: string; spent_usd: number }>;
}

export interface SpawnLineageGraph {
  nodes: Array<{
    id: string;
    agent_id: string;
    name: string;
    depth: number;
    status: string;
    goal_success_count: number;
    goal_fail_count: number;
  }>;
  edges: Array<{
    parent: string;
    child: string;
    spawned_at: string;
    spawn_reason: string;
  }>;
}
```

---

## 6. Testing Strategy

```python
# agent-verse-backend/tests/civilization/test_spawn_tool_wiring.py
def test_spawn_tool_callable_from_graph():
    """AgentGraph with civilization_id in context can call civilization_spawn tool."""
    graph = AgentGraph(initial_context={"civilization_id": "civ-123"}, ...)
    assert graph._civilization_spawn_enabled is True
    result = asyncio.run(graph._dispatch_tool("civilization_spawn", {
        "requested_capability": "data analyst",
        "goal_text": "Analyze sales data",
    }))
    assert result.get("success") is True

# agent-verse-backend/tests/civilization/test_reputation_history.py
def test_reputation_persisted_on_update():
    """EWMA update writes snapshot to civilization_reputation_history."""
    # ... create society, call update_reputation(), query DB, assert record exists

# agent-verse-backend/tests/civilization/test_constitution_rollback.py
def test_constitution_rollback():
    """Update → save history → rollback → constitution matches pre-update state."""
    # POST new constitution → GET history → POST rollback → GET constitution → assert equal

# agent-verse-backend/tests/civilization/test_governor_datetime.py
def test_auto_retire_idle_with_tz_aware_datetime():
    """auto_retire_idle() correctly handles timezone-aware TIMESTAMPTZ from Postgres."""
    # ... mock datetime with tzinfo=UTC → assert comparison works
```

```typescript
// agent-verse-frontend/src/features/civilization/CivilizationMap.test.tsx
test("spawn animation class applied for 500ms on agent_spawned SSE event", async () => { ... });
test("breach glow opacity increases with breach score", () => { ... });
test("reputation history prop updates sparkline data", async () => { ... });

// agent-verse-frontend/e2e/civilization-full.spec.ts
test("create civilization → submit goal → spawn tree shows child agents", async ({ page }) => { ... });
test("edit constitution → history shows change → rollback restores original", async ({ page }) => { ... });
```

---

## 7. Docker & Infrastructure

```yaml
# agent-verse-backend/infra/docker-compose.yml — backend environment:
CIVILIZATION_ENABLED: "true"
CIVILIZATION_MAX_AGENTS_PER_TENANT: "50"
```

Grafana dashboard additions (`infra/grafana/dashboards/agentverse-civilization.json`):
- `civ_agents_active` gauge
- `civ_spawns_total` counter (rate)
- `civ_spawn_denied_total` counter (rate)
- `civ_budget_spent_usd` time series per tenant
- `civ_debates_total` counter
- `civ_learnings_promoted_total` / `civ_learnings_rejected_total` counters
