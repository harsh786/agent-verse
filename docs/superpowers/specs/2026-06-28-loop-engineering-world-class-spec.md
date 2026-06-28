# Loop Engineering — World-Class Specification

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Build a complete Loop Engineering system — persisting attempt history to DB, making DECOMPOSE/HUMAN_GUIDANCE strategies genuinely functional, adding a real-time Loop Control panel in the UI, and providing an interactive Loop Playground.

**Architecture:** Add `goal_attempts` + `goal_step_loops` DB tables (migration 0051), wrap persistence in Celery, build 4 new frontend components (PersistenceStatusPanel, PersistenceControlsPanel, StepLoopDebugger, LoopEngineeringPlayground) with animated counters and strategy timelines.

**Tech Stack:** Python 3.12 · FastAPI · Celery · Redis · SQLAlchemy · requestAnimationFrame · CSS Keyframes · Zustand · TanStack Query

---

## 1. Vision

Loop engineering is what separates a toy AI assistant from a production agent OS. World-class means:
- An agent that fails doesn't silently die — it retries with escalating intelligence: first with different tools, then by simplifying the goal, then by decomposing it into sub-goals, then by asking the human
- The user can watch this happen in real-time: an attempt counter rings up, the strategy badge changes color, a countdown timer shows when the next retry fires
- The user can intervene: inject guidance, skip to a different strategy, or abort
- After completion, a timeline shows exactly which strategy succeeded and at what cost

---

## 2. Current State (Evidence)

### What works (A+ backend)

| Feature | File | Evidence |
|---------|------|----------|
| 6 retry strategies | `persistence.py:26` | `RetryStrategy` enum: SAME_APPROACH, DIFFERENT_TOOLS, DECOMPOSE, SIMPLIFY, HUMAN_GUIDANCE, ESCALATE |
| Exponential backoff + jitter | `persistence.py:123` | `base * 2^(attempt-1)` + 0–20% jitter |
| Strategy enrichment prompts | `persistence.py:132` | `_build_enriched_goal()` for each strategy |
| Per-attempt SSE events | `persistence.py:174` | 6 distinct event types |
| Step-level loop_until | `graph.py:790` | `_execute_step_with_loop()` with condition eval |
| LangGraph checkpointing | `graph.py:1767` | `_write_checkpoint()` after each step |
| Goal-level persistence | `service.py:1186` | `_run_agent_loop_persistent()` |
| PersistenceConfigRequest | `api/goals.py:20` | 8 configurable fields exposed in API |

### Critical gaps (D frontend)

- **Zero UI** for persistence mode in GoalDetailPage — `GoalDetailPage.tsx` has no attempt counter, no strategy display, no backoff timer, no retry history
- **In-memory state** — `AttemptRecord` list lives only in `GoalPersistenceEngine` instance; server restart loses all progress
- **`asyncio.create_task()`** wraps persistence — no Celery task, no restart recovery

### High gaps

- **DECOMPOSE** strategy calls `_build_enriched_goal()` with text "Break it into the smallest possible first step" — just prompt injection, not real goal decomposition
- **HUMAN_GUIDANCE** strategy injects text but doesn't actually pause for human input or create a HITL request
- No `goal_step_loops` persistence for step-level loop_until tracking

---

## 3. Backend Specification

### 3.1 New Database Tables — Migration 0051

**File to create**: `agent-verse-backend/app/db/migrations/versions/0051_loop_engineering.py`

```python
"""Add goal_attempts and goal_step_loops tables for loop engineering persistence."""
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Goal-level attempt history
    op.execute("""
        CREATE TABLE IF NOT EXISTS goal_attempts (
            id              TEXT PRIMARY KEY,
            goal_id         TEXT NOT NULL,
            tenant_id       TEXT NOT NULL,
            attempt_number  INTEGER NOT NULL,
            strategy        TEXT NOT NULL,
            enriched_goal   TEXT NOT NULL DEFAULT '',
            started_at      TIMESTAMPTZ NOT NULL,
            ended_at        TIMESTAMPTZ,
            succeeded       BOOLEAN,
            failure_reason  TEXT,
            iterations_used INTEGER,
            cost_usd        NUMERIC(10, 6),
            backoff_seconds INTEGER NOT NULL DEFAULT 0
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_goal_attempts_goal ON goal_attempts (goal_id, attempt_number)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_goal_attempts_tenant ON goal_attempts (tenant_id)")

    # Step-level loop_until iteration history
    op.execute("""
        CREATE TABLE IF NOT EXISTS goal_step_loops (
            id               TEXT PRIMARY KEY,
            goal_id          TEXT NOT NULL,
            tenant_id        TEXT NOT NULL,
            step_index       INTEGER NOT NULL,
            step_description TEXT NOT NULL DEFAULT '',
            loop_condition   TEXT NOT NULL DEFAULT '',
            iteration_number INTEGER NOT NULL,
            condition_result BOOLEAN,
            output_snapshot  TEXT NOT NULL DEFAULT '',
            created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_goal_step_loops_goal ON goal_step_loops (goal_id, step_index)")

    for table in ["goal_attempts", "goal_step_loops"]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (tenant_id = current_setting('app.tenant_id', TRUE))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))
        """)

def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS goal_step_loops CASCADE")
    op.execute("DROP TABLE IF EXISTS goal_attempts CASCADE")
```

### 3.2 PersistenceEngine DB Persistence

**File to modify**: `agent-verse-backend/app/agent/persistence.py`

Add DB write calls around each attempt:

```python
import uuid
from datetime import datetime, timezone

class GoalPersistenceEngine:
    def __init__(self, ..., db=None):  # add db parameter
        self._db = db
        # ... existing init

    async def _write_attempt_start(self, goal_id: str, tenant_id: str, attempt: int, strategy: str, enriched_goal: str, backoff: int) -> str:
        """Write attempt start record to DB. Returns attempt record ID."""
        attempt_id = str(uuid.uuid4())
        if self._db is None:
            return attempt_id
        try:
            from sqlalchemy import text as _t
            async with self._db() as session:
                await session.execute(_t("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id})
                await session.execute(_t("""
                    INSERT INTO goal_attempts (id, goal_id, tenant_id, attempt_number, strategy, enriched_goal, started_at, backoff_seconds)
                    VALUES (:id, :goal, :tenant, :num, :strat, :goal_text, NOW(), :backoff)
                """), {"id": attempt_id, "goal": goal_id, "tenant": tenant_id, "num": attempt,
                       "strat": strategy, "goal_text": enriched_goal[:2000], "backoff": backoff})
                await session.commit()
        except Exception as exc:
            self._logger.warning("attempt_write_failed", error=str(exc))
        return attempt_id

    async def _write_attempt_end(self, attempt_id: str, tenant_id: str, succeeded: bool, failure_reason: str, iterations: int, cost_usd: float) -> None:
        """Update attempt record with completion data."""
        if self._db is None or not attempt_id:
            return
        try:
            from sqlalchemy import text as _t
            async with self._db() as session:
                await session.execute(_t("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id})
                await session.execute(_t("""
                    UPDATE goal_attempts
                    SET ended_at=NOW(), succeeded=:ok, failure_reason=:reason,
                        iterations_used=:iters, cost_usd=:cost
                    WHERE id=:id
                """), {"id": attempt_id, "ok": succeeded, "reason": failure_reason[:500],
                       "iters": iterations, "cost": cost_usd})
                await session.commit()
        except Exception as exc:
            self._logger.warning("attempt_update_failed", error=str(exc))

    # In run() loop, add calls:
    #   attempt_id = await self._write_attempt_start(goal_id, tenant_id, attempt_num, strategy, enriched_goal, backoff)
    #   ... run the attempt ...
    #   await self._write_attempt_end(attempt_id, tenant_id, succeeded, failure_reason, iterations, cost)
```

### 3.3 True DECOMPOSE Strategy

**File to modify**: `agent-verse-backend/app/agent/persistence.py`

When strategy is `RetryStrategy.DECOMPOSE`, instead of just enriching the prompt:

```python
async def _handle_decompose_strategy(self, goal: str, tenant_ctx, goal_service) -> dict | None:
    """True decomposition: call GoalTreePlanner to split into sub-goals, submit each."""
    try:
        from app.agent.goal_tree import GoalTreePlanner
        planner = GoalTreePlanner(provider=self._provider)
        sub_goals = await planner.decompose(goal, max_sub_goals=3)

        if not sub_goals:
            return None  # Fall back to prompt injection

        # Submit each sub-goal
        results = []
        for sub_goal_text in sub_goals:
            result = await goal_service.submit_goal(
                goal=sub_goal_text,
                tenant_ctx=tenant_ctx,
                execution_context={"parent_persistence_goal": goal, "is_decomposed_subtask": True},
            )
            results.append(result)

        # Wait for all sub-goals to complete (poll with timeout)
        sub_goal_ids = [r.get("goal_id") or r.get("id") for r in results]
        await self._emit_event({"type": "persistence_decomposed", "sub_goals": [
            {"id": r.get("goal_id"), "description": sg} for r, sg in zip(results, sub_goals)
        ]})

        # Poll until all complete (up to iterations_per_attempt * 30s timeout)
        timeout_s = self._config.iterations_per_attempt * 30
        start = time.monotonic()
        while time.monotonic() - start < timeout_s:
            statuses = []
            for sid in sub_goal_ids:
                g = await goal_service.get_goal(sid, tenant_ctx)
                statuses.append(g.get("status") if g else "unknown")
            if all(s in ("complete", "completed", "failed", "cancelled") for s in statuses):
                all_ok = all(s in ("complete", "completed") for s in statuses)
                return {"success": all_ok, "sub_goal_ids": sub_goal_ids, "statuses": statuses}
            await asyncio.sleep(5)
        return None  # Timed out
    except Exception as exc:
        self._logger.warning("decompose_strategy_failed", error=str(exc))
        return None  # Fall back to prompt injection
```

### 3.4 True HUMAN_GUIDANCE Strategy

**File to modify**: `agent-verse-backend/app/agent/persistence.py`

```python
async def _handle_human_guidance_strategy(self, goal: str, goal_id: str, tenant_ctx, hitl_gateway) -> str | None:
    """Create a HITL request, pause the loop, resume with human guidance injected."""
    if hitl_gateway is None:
        return None  # Fall back to prompt injection

    try:
        # Create HITL request with persistence context
        failure_summary = "\n".join(
            f"Attempt {r.attempt_number} ({r.strategy}): {r.failure_reason}"
            for r in self._attempt_records[-3:]  # last 3 failures
        )
        request_id = await hitl_gateway.create_request(
            goal_id=goal_id,
            action=f"Persistence guidance needed: {goal[:100]}",
            risk_level="medium",
            context={
                "type": "persistence_guidance",
                "failure_history": failure_summary,
                "strategies_tried": [r.strategy for r in self._attempt_records],
                "prompt": "Please provide guidance on how to achieve this goal, or reject to abort.",
            },
            tenant_ctx=tenant_ctx,
        )

        await self._emit_event({
            "type": "persistence_awaiting_human",
            "request_id": request_id,
            "failure_summary": failure_summary,
        })

        # Wait for approval (poll Redis for 10 minutes max)
        timeout_s = 600
        start = time.monotonic()
        while time.monotonic() - start < timeout_s:
            approval = hitl_gateway.get_request(request_id, tenant_ctx)
            if approval and approval.status == "approved":
                guidance = approval.note or ""
                if guidance:
                    return f"{goal}\n\nHuman guidance: {guidance}"
                return goal  # No note, just resume
            if approval and approval.status == "rejected":
                return None  # Human rejected — abort persistence
            await asyncio.sleep(5)
        return None  # Timed out waiting for human
    except Exception as exc:
        self._logger.warning("human_guidance_strategy_failed", error=str(exc))
        return None
```

### 3.5 Celery-Backed Persistence

**File to modify**: `agent-verse-backend/app/scaling/tasks.py`

Add a dedicated Celery task for persistence runs:

```python
@celery_app.task(name="app.scaling.tasks.run_persistent_goal", queue="goals.persistence", max_retries=0)
def run_persistent_goal(goal_id: str, tenant_id: str, goal_text: str, persistence_config: dict, execution_context: dict | None = None):
    """Run a goal with persistence engine. Wrapped in Celery for restart recovery."""
    import asyncio
    from app.agent.persistence import GoalPersistenceEngine, PersistenceConfig
    from app.tenancy.context import TenantContext, PlanTier

    async def _run():
        # Reconstruct minimal app context
        from app.db.session import get_session_factory
        from app.services.goal_service import GoalService
        # ... reconstruct services from settings
        # Build engine with DB
        config = PersistenceConfig(**persistence_config)
        engine = GoalPersistenceEngine(
            goal_id=goal_id,
            config=config,
            db=get_session_factory(),
        )
        # Store current attempt in Redis for recovery
        import redis as _redis
        r = _redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
        r.setex(f"persistence:{goal_id}:status", 86400, "running")
        r.setex(f"persistence:{goal_id}:config", 86400, json.dumps(persistence_config))
        try:
            await engine.run(goal=goal_text, tenant_ctx=tenant_ctx, goal_service=goal_svc)
        finally:
            r.delete(f"persistence:{goal_id}:status")

    asyncio.run(_run())
```

Also add `goals.persistence` queue to worker `-Q` flag in docker-compose.

### 3.6 Step Loop Persistence

**File to modify**: `agent-verse-backend/app/agent/graph.py`

In `_execute_step_with_loop()`, after each loop iteration:
```python
# Write loop iteration record
if self._db:
    try:
        async with self._db() as session:
            await session.execute(_t("""
                INSERT INTO goal_step_loops
                (id, goal_id, tenant_id, step_index, step_description, loop_condition, iteration_number, condition_result, output_snapshot, created_at)
                VALUES (:id, :goal, :tenant, :idx, :desc, :cond, :iter, :result, :output, NOW())
            """), {
                "id": str(uuid.uuid4()),
                "goal": self._goal_id,
                "tenant": self._tenant_ctx.tenant_id,
                "idx": step_index,
                "desc": step.description[:300],
                "cond": step.loop_until or "",
                "iter": loop_iter,
                "result": condition_met,
                "output": str(step_output)[:500],
            })
            await session.commit()
    except Exception:
        pass  # Non-critical, don't fail the loop
```

### 3.7 New API Endpoints

#### GET /goals/{id}/attempts
```
Response: {
  "goal_id": str,
  "persistence_active": bool,
  "total_attempts": int,
  "max_attempts": int,
  "current_strategy": str | null,
  "next_retry_at": ISO8601 | null,
  "total_cost_usd": float,
  "attempts": [
    {
      "id": str,
      "attempt_number": int,
      "strategy": str,
      "enriched_goal": str,
      "started_at": ISO8601,
      "ended_at": ISO8601 | null,
      "succeeded": bool | null,
      "failure_reason": str | null,
      "iterations_used": int | null,
      "cost_usd": float | null,
      "backoff_seconds": int
    }
  ]
}
Business logic: SELECT * FROM goal_attempts WHERE goal_id=? ORDER BY attempt_number
                JOIN with Redis key persistence:{goal_id}:status for live state
```

#### GET /goals/{id}/persistence-state
```
Response: {
  "active": bool,
  "current_attempt": int,
  "max_attempts": int,
  "current_strategy": str,
  "strategies_tried": [str],
  "next_retry_at": ISO8601 | null,
  "total_cost_usd": float,
  "redis_state": "running" | "completed" | null
}
Business logic: Redis GET persistence:{goal_id}:status + SELECT from goal_attempts
```

#### POST /goals/{id}/persistence/abort
```
Business logic: Redis SET persistence:{goal_id}:abort = "true" (engine checks this flag in run loop)
Response: {success: true}
```

#### POST /goals/{id}/persistence/skip-strategy
```
Business logic: Redis SET persistence:{goal_id}:skip_strategy = "true"
Response: {success: true, next_strategy: str}
```

#### POST /goals/{id}/persistence/inject-guidance
```
Request body: {"guidance": "Try using the GraphQL API instead of REST"}
Business logic: Redis SET persistence:{goal_id}:injected_guidance = guidance
Engine checks this key at start of each attempt and prepends to enriched_goal
Response: {success: true}
```

#### GET /goals/{id}/step-loops
```
Response: {
  "steps": [
    {
      "step_index": int,
      "step_description": str,
      "loop_condition": str,
      "iterations": [
        {"iteration_number": int, "condition_result": bool, "output_snapshot": str, "created_at": ISO8601}
      ]
    }
  ]
}
Business logic: SELECT from goal_step_loops WHERE goal_id=? GROUP BY step_index ORDER BY iteration_number
```

### 3.8 Enhanced SSE Events

Add these new event types to the goal SSE stream (`services/goal_service.py`):

```python
# In _emit_sse_event(), ensure these are included alongside existing persistence events:

{
  "type": "persistence_attempt_start",
  "attempt": 3,
  "max_attempts": 10,
  "strategy": "DIFFERENT_TOOLS",
  "enriched_goal_preview": "Try solving this using...",
  "backoff_was_seconds": 60
}

{
  "type": "persistence_backoff_waiting",
  "remaining_seconds": 45,
  "reason": "rate_limit_detected",
  "next_attempt": 4
}

{
  "type": "persistence_strategy_changed",
  "from_strategy": "SAME_APPROACH",
  "to_strategy": "DIFFERENT_TOOLS",
  "after_failures": 2
}

{
  "type": "persistence_decomposed",
  "sub_goals": [
    {"id": "goal-abc", "description": "Step 1: ..."},
    {"id": "goal-def", "description": "Step 2: ..."}
  ]
}

{
  "type": "persistence_awaiting_human",
  "request_id": "hitl-xyz",
  "failure_summary": "Failed 6 times with different strategies"
}

{
  "type": "persistence_goal_achieved",
  "total_attempts": 4,
  "total_cost_usd": 0.23,
  "winning_strategy": "DECOMPOSE",
  "total_duration_s": 180
}

{
  "type": "persistence_exhausted",
  "attempts_used": 10,
  "strategies_tried": ["SAME_APPROACH", "DIFFERENT_TOOLS", "SIMPLIFY"],
  "final_cost_usd": 1.45,
  "recommendation": "Consider breaking this goal into simpler sub-tasks manually"
}
```

---

## 4. Frontend Specification

### 4.1 GoalsListPage — Persistence Mode Toggle

**File to modify**: `agent-verse-frontend/src/features/goals/GoalsListPage.tsx`

Add "Advanced Options" expandable section below the submit button:

```typescript
const [showAdvanced, setShowAdvanced] = useState(false);
const [persistenceMode, setPersistenceMode] = useState(false);
const [persistencePreset, setPersistencePreset] = useState<"auto" | "aggressive" | "conservative" | "decompose">("auto");

const PERSISTENCE_PRESETS = {
  auto:        { max_attempts: 10, iterations_per_attempt: 15, strategy_switch_after: 2, base_backoff_seconds: 30 },
  aggressive:  { max_attempts: 20, iterations_per_attempt: 20, strategy_switch_after: 1, base_backoff_seconds: 10 },
  conservative:{ max_attempts: 5,  iterations_per_attempt: 10, strategy_switch_after: 3, base_backoff_seconds: 60 },
  decompose:   { max_attempts: 8,  iterations_per_attempt: 12, strategy_switch_after: 1, base_backoff_seconds: 20 },
};

// In JSX:
<button
  type="button"
  onClick={() => setShowAdvanced(v => !v)}
  className="w-full flex items-center justify-between text-xs text-muted-foreground hover:text-foreground p-2 rounded hover:bg-muted/30"
>
  Advanced options <ChevronDown className={`h-3.5 w-3.5 transition-transform ${showAdvanced ? "rotate-180" : ""}`} />
</button>

{showAdvanced && (
  <div className="border border-border rounded-lg p-3 space-y-3 bg-muted/20">
    <label className="flex items-center gap-2 cursor-pointer">
      <input type="checkbox" checked={persistenceMode} onChange={e => setPersistenceMode(e.target.checked)} className="accent-primary" />
      <span className="text-sm font-medium">Enable Persistence Mode</span>
      <span className="text-xs text-muted-foreground">— keep trying until goal is achieved</span>
    </label>

    {persistenceMode && (
      <div className="space-y-2">
        <div className="grid grid-cols-4 gap-2">
          {(["auto", "aggressive", "conservative", "decompose"] as const).map(preset => (
            <button
              key={preset}
              onClick={() => setPersistencePreset(preset)}
              className={`py-1.5 px-2 text-xs rounded-lg border capitalize transition-colors
                ${persistencePreset === preset ? "bg-primary text-primary-foreground border-primary" : "border-border hover:border-primary/30"}`}
            >
              {preset}
            </button>
          ))}
        </div>
        <p className="text-xs text-muted-foreground">
          {persistencePreset === "auto" && "Balanced: up to 10 attempts, switches strategy after 2 failures"}
          {persistencePreset === "aggressive" && "Maximum effort: 20 attempts, switches strategy immediately after each failure"}
          {persistencePreset === "conservative" && "Cautious: 5 attempts with long backoff between retries"}
          {persistencePreset === "decompose" && "Decomposition-first: breaks complex goals into sub-tasks early"}
        </p>
      </div>
    )}
  </div>
)}
```

### 4.2 GoalDetailPage — Loop Engineering Tab

**File to modify**: `agent-verse-frontend/src/features/goals/GoalDetailPage.tsx`

Add "Loop Control" tab (shown when `goal.persistence_mode === true` OR `goal.status === "failed"`):

#### PersistenceStatusPanel Component

**File to create**: `agent-verse-frontend/src/features/goals/components/PersistenceStatusPanel.tsx`

```typescript
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { goalsApi } from "@/lib/api/client";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { LiveCostTicker } from "@/components/live/LiveCostTicker";
import {
  RotateCcw, Wrench, Minimize2, GitBranch, UserCheck, AlertTriangle,
  CheckCircle2, XCircle, Clock,
} from "lucide-react";

const STRATEGY_ICONS: Record<string, React.ElementType> = {
  SAME_APPROACH: RotateCcw,
  DIFFERENT_TOOLS: Wrench,
  SIMPLIFY: Minimize2,
  DECOMPOSE: GitBranch,
  HUMAN_GUIDANCE: UserCheck,
  ESCALATE: AlertTriangle,
};

const STRATEGY_COLORS: Record<string, string> = {
  SAME_APPROACH:   "text-blue-600 dark:text-blue-400",
  DIFFERENT_TOOLS: "text-violet-600 dark:text-violet-400",
  SIMPLIFY:        "text-cyan-600 dark:text-cyan-400",
  DECOMPOSE:       "text-green-600 dark:text-green-400",
  HUMAN_GUIDANCE:  "text-amber-600 dark:text-amber-400",
  ESCALATE:        "text-red-600 dark:text-red-400",
};

interface Props { goalId: string; currentEvents: any[] }

export function PersistenceStatusPanel({ goalId, currentEvents }: Props) {
  const { data: persistenceData } = useQuery({
    queryKey: ["persistence-attempts", goalId],
    queryFn: () => fetch(`/goals/${goalId}/attempts`).then(r => r.json()),
    refetchInterval: 3000,
  });

  // Backoff countdown from SSE events
  const [countdown, setCountdown] = useState<number | null>(null);
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const lastBackoff = [...currentEvents].reverse()
      .find(e => e.type === "persistence_backoff_waiting");
    if (lastBackoff) {
      let remaining = lastBackoff.payload?.remaining_seconds ?? 0;
      setCountdown(remaining);
      if (countdownRef.current) clearInterval(countdownRef.current);
      countdownRef.current = setInterval(() => {
        remaining -= 1;
        if (remaining <= 0) {
          setCountdown(null);
          if (countdownRef.current) clearInterval(countdownRef.current);
        } else {
          setCountdown(remaining);
        }
      }, 1000);
    }
  }, [currentEvents]);

  const attempts = persistenceData?.attempts ?? [];
  const maxAttempts = persistenceData?.max_attempts ?? 10;
  const currentAttempt = persistenceData?.total_attempts ?? 0;
  const totalCost = persistenceData?.total_cost_usd ?? 0;
  const currentStrategy = persistenceData?.current_strategy ?? "";
  const strategiesTried = [...new Set(attempts.map((a: any) => a.strategy))];

  // Animated attempt ring
  const attemptPct = (currentAttempt / maxAttempts) * 100;
  const circumference = 2 * Math.PI * 36; // r=36
  const dashoffset = circumference - (attemptPct / 100) * circumference;

  return (
    <div className="space-y-5">
      {/* Attempt ring + key stats */}
      <div className="flex items-center gap-6">
        {/* SVG donut ring */}
        <div className="relative flex-shrink-0">
          <svg width="90" height="90" viewBox="0 0 90 90" aria-label={`${currentAttempt} of ${maxAttempts} attempts`}>
            <circle cx="45" cy="45" r="36" fill="none" stroke="hsl(var(--muted))" strokeWidth="8" />
            <circle
              cx="45" cy="45" r="36" fill="none"
              stroke={attemptPct > 80 ? "#ef4444" : attemptPct > 50 ? "#f59e0b" : "#3b82f6"}
              strokeWidth="8"
              strokeDasharray={circumference}
              strokeDashoffset={dashoffset}
              strokeLinecap="round"
              transform="rotate(-90 45 45)"
              style={{ transition: "stroke-dashoffset 600ms ease-out" }}
            />
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-xl font-bold tabular-nums">{currentAttempt}</span>
            <span className="text-[10px] text-muted-foreground">/ {maxAttempts}</span>
          </div>
        </div>

        {/* Current strategy + countdown */}
        <div className="space-y-2">
          {currentStrategy && (
            <div className="flex items-center gap-2">
              {(() => { const Icon = STRATEGY_ICONS[currentStrategy] ?? RotateCcw; return <Icon className={`h-4 w-4 ${STRATEGY_COLORS[currentStrategy] ?? "text-primary"}`} />; })()}
              <span className="text-sm font-medium capitalize">{currentStrategy.replace(/_/g, " ").toLowerCase()} strategy</span>
            </div>
          )}
          {countdown !== null && (
            <div className="flex items-center gap-2 text-amber-600 dark:text-amber-400">
              <Clock className="h-4 w-4" />
              <span className="text-sm font-mono">Next retry in {countdown}s</span>
            </div>
          )}
          <LiveCostTicker
            currentCost={totalCost}
            isRunning={persistenceData?.active}
            className="text-sm"
          />
        </div>
      </div>

      {/* Strategy timeline */}
      {strategiesTried.length > 0 && (
        <div>
          <p className="text-xs text-muted-foreground mb-2">Strategies tried</p>
          <div className="flex items-center gap-1 flex-wrap">
            {strategiesTried.map((strategy, i) => {
              const Icon = STRATEGY_ICONS[strategy as string] ?? RotateCcw;
              const isActive = strategy === currentStrategy;
              return (
                <span
                  key={`${strategy}-${i}`}
                  className={`flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium border
                    ${isActive
                      ? "bg-primary/10 border-primary text-primary"
                      : "bg-muted border-transparent text-muted-foreground"
                    }`}
                  style={{ animationDelay: `${i * 80}ms` }}
                >
                  <Icon className="h-3 w-3" />
                  {(strategy as string).replace(/_/g, " ")}
                </span>
              );
            })}
          </div>
        </div>
      )}

      {/* Per-attempt breakdown table */}
      {attempts.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs font-medium">Attempt history</p>
          <div className="border border-border rounded-lg overflow-hidden">
            {attempts.map((attempt: any) => (
              <div
                key={attempt.id}
                className="flex items-center gap-3 px-3 py-2 border-b border-border last:border-b-0 text-xs hover:bg-muted/30"
              >
                <span className="text-muted-foreground w-4 shrink-0">#{attempt.attempt_number}</span>
                <span className="capitalize flex-1 truncate text-foreground">
                  {attempt.strategy.replace(/_/g, " ").toLowerCase()}
                </span>
                {attempt.succeeded === true && <CheckCircle2 className="h-3.5 w-3.5 text-green-500 shrink-0" />}
                {attempt.succeeded === false && <XCircle className="h-3.5 w-3.5 text-red-500 shrink-0" />}
                {attempt.succeeded === null && <Clock className="h-3.5 w-3.5 text-muted-foreground shrink-0" />}
                {attempt.cost_usd !== null && (
                  <span className="text-muted-foreground shrink-0">${attempt.cost_usd?.toFixed(4)}</span>
                )}
                {attempt.failure_reason && (
                  <span className="text-red-500 truncate max-w-[120px]" title={attempt.failure_reason}>
                    {attempt.failure_reason.slice(0, 40)}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
```

#### PersistenceControlsPanel Component

**File to create**: `agent-verse-frontend/src/features/goals/components/PersistenceControlsPanel.tsx`

```typescript
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { SkipForward, MessageSquare, XCircle } from "lucide-react";
import { ConfirmModal } from "@/components/ui/ConfirmModal";
import { toast } from "@/stores/toast";

interface Props { goalId: string; isActive: boolean; }

export function PersistenceControlsPanel({ goalId, isActive }: Props) {
  const [showAbortConfirm, setShowAbortConfirm] = useState(false);
  const [guidanceText, setGuidanceText] = useState("");
  const [showGuidanceForm, setShowGuidanceForm] = useState(false);

  const skipStrategy = useMutation({
    mutationFn: () => fetch(`/goals/${goalId}/persistence/skip-strategy`, { method: "POST" }).then(r => r.json()),
    onSuccess: () => toast({ kind: "success", message: "Skipping to next strategy…" }),
    onError: () => toast({ kind: "error", message: "Failed to skip strategy" }),
  });

  const injectGuidance = useMutation({
    mutationFn: () => fetch(`/goals/${goalId}/persistence/inject-guidance`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ guidance: guidanceText }),
    }).then(r => r.json()),
    onSuccess: () => {
      toast({ kind: "success", message: "Guidance injected — will apply on next attempt" });
      setGuidanceText("");
      setShowGuidanceForm(false);
    },
  });

  const abortPersistence = useMutation({
    mutationFn: () => fetch(`/goals/${goalId}/persistence/abort`, { method: "POST" }).then(r => r.json()),
    onSuccess: () => toast({ kind: "warning", message: "Persistence loop aborted" }),
  });

  if (!isActive) return null;

  return (
    <div className="flex flex-wrap gap-2 pt-3 border-t border-border">
      <button
        onClick={() => skipStrategy.mutate()}
        disabled={skipStrategy.isPending}
        className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-border rounded-lg hover:bg-muted/50 transition-colors"
      >
        <SkipForward className="h-3.5 w-3.5" /> Skip Strategy
      </button>

      <button
        onClick={() => setShowGuidanceForm(v => !v)}
        className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-border rounded-lg hover:bg-muted/50 transition-colors"
      >
        <MessageSquare className="h-3.5 w-3.5" /> Provide Guidance
      </button>

      <button
        onClick={() => setShowAbortConfirm(true)}
        className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 rounded-lg hover:bg-red-50 dark:hover:bg-red-950/30 transition-colors"
      >
        <XCircle className="h-3.5 w-3.5" /> Abort Loop
      </button>

      {showGuidanceForm && (
        <div className="w-full mt-2 space-y-2">
          <textarea
            value={guidanceText}
            onChange={e => setGuidanceText(e.target.value)}
            placeholder="Provide specific guidance for the next attempt (e.g., 'Use the GraphQL API at /graphql endpoint instead of REST')"
            rows={3}
            className="w-full px-3 py-2 text-sm border border-input rounded-lg bg-background focus:outline-none focus:ring-2 focus:ring-primary resize-none"
          />
          <div className="flex gap-2">
            <button
              onClick={() => injectGuidance.mutate()}
              disabled={!guidanceText.trim() || injectGuidance.isPending}
              className="px-4 py-1.5 bg-primary text-primary-foreground text-xs rounded-lg hover:opacity-90 disabled:opacity-50"
            >
              {injectGuidance.isPending ? "Injecting…" : "Inject Guidance"}
            </button>
            <button onClick={() => setShowGuidanceForm(false)} className="px-3 py-1.5 text-xs border border-input rounded-lg hover:bg-muted/50">
              Cancel
            </button>
          </div>
        </div>
      )}

      <ConfirmModal
        open={showAbortConfirm}
        title="Abort persistence loop?"
        description="The goal will be marked as failed. This cannot be undone."
        confirmLabel="Abort"
        variant="danger"
        isLoading={abortPersistence.isPending}
        onConfirm={() => abortPersistence.mutate()}
        onCancel={() => setShowAbortConfirm(false)}
      />
    </div>
  );
}
```

#### StepLoopDebugger Component

**File to create**: `agent-verse-frontend/src/features/goals/components/StepLoopDebugger.tsx`

Shows step-level `loop_until` iterations in the pipeline tab when data exists:

```typescript
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, XCircle, ChevronDown } from "lucide-react";
import { useState } from "react";

interface Props { goalId: string; stepIndex: number; stepDescription: string; }

export function StepLoopDebugger({ goalId, stepIndex, stepDescription }: Props) {
  const [expanded, setExpanded] = useState(false);
  const { data } = useQuery({
    queryKey: ["step-loops", goalId, stepIndex],
    queryFn: () => fetch(`/goals/${goalId}/step-loops`).then(r => r.json()),
    enabled: expanded,
    staleTime: 10_000,
  });

  const stepData = data?.steps?.find((s: any) => s.step_index === stepIndex);
  if (!stepData?.iterations?.length) return null;

  return (
    <div className="ml-4 mt-1">
      <button
        onClick={() => setExpanded(v => !v)}
        className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        <ChevronDown className={`h-3 w-3 transition-transform ${expanded ? "rotate-180" : ""}`} />
        Loop: {stepData.iterations.length} iteration{stepData.iterations.length !== 1 ? "s" : ""}
        {" · condition: "}<code className="bg-muted px-1 rounded">{stepData.loop_condition}</code>
      </button>
      {expanded && (
        <div className="mt-1 space-y-0.5 ml-4">
          {stepData.iterations.map((iter: any) => (
            <div key={iter.iteration_number} className="flex items-center gap-2 text-xs">
              {iter.condition_result
                ? <CheckCircle2 className="h-3 w-3 text-green-500 shrink-0" />
                : <XCircle className="h-3 w-3 text-muted-foreground shrink-0" />}
              <span className="text-muted-foreground">Iter {iter.iteration_number}:</span>
              <span className="truncate text-foreground">{iter.output_snapshot.slice(0, 80)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

### 4.3 New: LoopEngineeringPlayground at /goals/loop-playground

**File to create**: `agent-verse-frontend/src/features/goals/LoopEngineeringPlayground.tsx`

Interactive tool for understanding and tuning persistence:

Layout: two-column
- Left: Goal textarea + PersistenceConfig sliders + "Strategy Preview" section + "Try It" button
- Right: Live attempt counter + strategy timeline (updates if a goal was submitted)

**PersistenceConfig sliders:**
```
Max Attempts: [slider 1-20, default 10]
Iterations/attempt: [slider 5-30, default 15]
Base backoff: [slider 10-300s, default 30]
Max backoff: [slider 60-3600s, default 600]
Strategy switch after: [slider 1-5, default 2]
Escalate after: [slider 3-10, default 6]
```

**Strategy Preview** (shows which strategy would be selected at each failure count):
```typescript
// Client-side prediction of strategy selection
function predictStrategies(config: PersistenceConfig): Array<{attempt: number; strategy: string}> {
  const results = [];
  let consecutive = 0;
  for (let i = 1; i <= config.max_attempts; i++) {
    const strategyIndex = Math.min(
      Math.floor(consecutive / config.strategy_switch_after),
      strategies.length - 1
    );
    results.push({ attempt: i, strategy: strategies[strategyIndex] });
    consecutive++;
  }
  return results;
}
```

Display as a mini timeline with icons for each strategy segment.

Add to App.tsx: `<Route path="goals/loop-playground" element={<LoopEngineeringPlayground />} />`
Add to Sidebar: `{ to: "/goals/loop-playground", icon: RefreshCw, label: "Loop Playground" }`

### 4.4 Animations

**1. Attempt ring fill animation** (`stroke-dashoffset` transition: `600ms ease-out`)

**2. Strategy node slide-in on timeline**:
```css
@keyframes strategyNodeIn {
  from { transform: translateX(30px); opacity: 0; }
  to   { transform: translateX(0); opacity: 1; }
}
.strategy-node-entering {
  animation: strategyNodeIn 300ms ease-out forwards;
}
```
New strategy nodes appear when persistence_strategy_changed SSE event arrives.

**3. Backoff countdown drain**:
```css
/* Progress bar that starts full and drains to 0 */
@keyframes countdownDrain {
  from { width: 100%; }
  to   { width: 0%; }
}
/* Applied with animation-duration = backoff_seconds */
```

**4. Cost accumulation across attempts**: Uses `LiveCostTicker` but never resets — shows total across all attempts.

**5. "Persistence achieved" win banner**:
```typescript
// Slides down from top when persistence_goal_achieved SSE received
// CSS: @keyframes slideDownIn { from { transform: translateY(-100%); } to { transform: translateY(0); } }
// Shows: "Goal achieved after N attempts using DECOMPOSE strategy · Saved $X vs single attempt"
// Green background, confetti particles, auto-dismiss after 8s
```

**6. Strategy switch flash**: When persistence_strategy_changed SSE arrives, briefly flash the current strategy badge (amber → primary bg, 400ms).

---

## 5. TypeScript Interfaces

```typescript
// Add to agent-verse-frontend/src/lib/api/client.ts:

export interface GoalAttempt {
  id: string;
  attempt_number: number;
  strategy: string;
  enriched_goal: string;
  started_at: string;
  ended_at: string | null;
  succeeded: boolean | null;
  failure_reason: string | null;
  iterations_used: number | null;
  cost_usd: number | null;
  backoff_seconds: number;
}

export interface PersistenceState {
  active: boolean;
  current_attempt: number;
  max_attempts: number;
  current_strategy: string;
  strategies_tried: string[];
  next_retry_at: string | null;
  total_cost_usd: number;
  attempts: GoalAttempt[];
}

export interface StepLoopData {
  steps: Array<{
    step_index: number;
    step_description: string;
    loop_condition: string;
    iterations: Array<{
      iteration_number: number;
      condition_result: boolean | null;
      output_snapshot: string;
      created_at: string;
    }>;
  }>;
}

// Add to goalsApi:
// getAttempts: (id: string) => request<PersistenceState>(`/goals/${id}/attempts`),
// getPersistenceState: (id: string) => request<PersistenceState>(`/goals/${id}/persistence-state`),
// abortPersistence: (id: string) => request<{success: boolean}>(`/goals/${id}/persistence/abort`, {method:"POST"}),
// skipStrategy: (id: string) => request<{success: boolean; next_strategy: string}>(`/goals/${id}/persistence/skip-strategy`, {method:"POST"}),
// injectGuidance: (id: string, guidance: string) => request<{success: boolean}>(`/goals/${id}/persistence/inject-guidance`, {method:"POST", body:JSON.stringify({guidance})}),
// getStepLoops: (id: string) => request<StepLoopData>(`/goals/${id}/step-loops`),
```

---

## 6. Testing Strategy

```python
# tests/agent/test_persistence_db.py
def test_attempt_written_to_db():
    engine = GoalPersistenceEngine(goal_id="g1", config=..., db=mock_db)
    attempt_id = asyncio.run(engine._write_attempt_start("g1", "t1", 1, "SAME_APPROACH", "goal text", 0))
    assert attempt_id is not None
    # Query DB, assert record exists

def test_attempt_updated_on_end():
    # ... write start, write end, assert ended_at and succeeded are set

def test_true_decompose_calls_goal_service():
    engine = GoalPersistenceEngine(...)
    mock_service = AsyncMock()
    mock_service.submit_goal.return_value = {"goal_id": "sub-1", "status": "planning"}
    result = asyncio.run(engine._handle_decompose_strategy("complex goal", tenant_ctx, mock_service))
    assert mock_service.submit_goal.call_count >= 1

def test_human_guidance_creates_hitl_request():
    # Mock HITLGateway, assert create_request called

def test_persistence_abort_via_redis():
    # Set abort key in Redis, run engine, assert loop terminates early
```

```typescript
// Frontend: PersistenceStatusPanel.test.tsx
test("attempt ring fills to correct percentage", () => { ... });
test("strategy timeline shows all strategies tried", () => { ... });
test("backoff countdown decrements from SSE event", async () => { ... });

// E2E: loop-engineering.spec.ts
test("submit persistence goal → attempt counter increments → abort works", async ({page}) => { ... });
```

---

## 8. Amendments — World-Class Completeness Fixes

### FIX: Celery task body with real reconstruction code

```python
@celery_app.task(name="app.scaling.tasks.run_persistent_goal", queue="goals.persistence", max_retries=0)
def run_persistent_goal(goal_id: str, tenant_id: str, goal_text: str, persistence_config: dict, execution_context: dict | None = None):
    """Run a goal with persistence engine in Celery worker context."""
    import asyncio, os, json
    from app.core.config import get_settings
    from app.tenancy.context import TenantContext, PlanTier
    from app.agent.persistence import GoalPersistenceEngine, PersistenceConfig

    settings = get_settings()

    async def _async_run():
        from app.db.session import get_session_factory
        from app.services.goal_service import GoalService
        from app.services.tenant_service import TenantService
        from app.providers.fake import FakeProvider

        db_factory = get_session_factory()

        # Reconstruct tenant context from DB
        tenant_svc = TenantService(db_session_factory=db_factory)
        tenant_record = await tenant_svc.get_tenant(tenant_id)
        plan_str = tenant_record.get("plan", "free") if tenant_record else "free"
        try:
            plan = PlanTier(plan_str)
        except ValueError:
            plan = PlanTier.FREE

        tenant_ctx = TenantContext(
            tenant_id=tenant_id, plan=plan, api_key_id="celery-persistence"
        )

        # Resolve LLM provider
        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        openai_key = os.getenv("OPENAI_API_KEY", "")
        provider = None
        if anthropic_key:
            try:
                from app.providers.anthropic_provider import AnthropicProvider
                provider = AnthropicProvider(api_key=anthropic_key)
            except Exception:
                pass
        if provider is None and openai_key:
            try:
                from app.providers.openai_compatible import OpenAICompatibleProvider
                provider = OpenAICompatibleProvider(api_key=openai_key)
            except Exception:
                pass
        if provider is None:
            provider = FakeProvider(responses=["Attempting goal again with different approach"])

        # Build GoalService
        goal_svc = GoalService(db_session_factory=db_factory, provider=provider)

        # Build PersistenceConfig from dict
        config = PersistenceConfig(**{k: v for k, v in persistence_config.items() if k in PersistenceConfig.__dataclass_fields__})

        # Store running state in Redis for UI polling
        import redis as _redis
        r = _redis.from_url(settings.redis_url)
        r.setex(f"persistence:{goal_id}:status", 86400, "running")
        r.setex(f"persistence:{goal_id}:config", 86400, json.dumps(persistence_config))

        try:
            engine = GoalPersistenceEngine(
                goal_id=goal_id,
                config=config,
                provider=provider,
                db=db_factory,
            )
            await engine.run(
                goal=goal_text,
                tenant_ctx=tenant_ctx,
                goal_service=goal_svc,
            )
        finally:
            r.delete(f"persistence:{goal_id}:status")
            r.delete(f"persistence:{goal_id}:config")

    asyncio.run(_async_run())
```

### FIX: Specify exact line to modify in goal_service.py

```
In app/services/goal_service.py, find the code:
    if persistence_mode:
        asyncio.create_task(self._run_agent_loop_persistent(...))

REPLACE with:
    if persistence_mode:
        try:
            from app.scaling.tasks import run_persistent_goal
            run_persistent_goal.delay(
                goal_id=goal_id,
                tenant_id=tenant_ctx.tenant_id,
                goal_text=goal,
                persistence_config=execution_context.get("persistence_config", {}),
                execution_context=execution_context,
            )
        except Exception:
            # Fall back to asyncio.create_task if Celery unavailable
            asyncio.create_task(self._run_agent_loop_persistent(
                goal_id=goal_id, goal=goal, tenant_ctx=tenant_ctx,
                execution_context=execution_context or {},
            ))
```

### FIX: Add goals.persistence queue to celery_app.py

```python
# In app/scaling/celery_app.py, add to celery_app.conf.update():
task_routes = {
    "app.scaling.tasks.run_goal": {"queue": PLAN_QUEUE_MAP.get("free", "goals.free")},
    "app.scaling.tasks.run_persistent_goal": {"queue": "goals.persistence"},
    "app.scaling.tasks.run_schedule": {"queue": "schedules"},
}
```

Also add `goals.persistence` to docker-compose worker `-Q` flags.

### FIX: Replace raw fetch() with goalsApi

In `PersistenceStatusPanel` and `PersistenceControlsPanel`, replace all raw `fetch()` calls:

```typescript
// Instead of: fetch(`/goals/${goalId}/attempts`)
// Use: request<PersistenceState>(`/goals/${goalId}/attempts`) via apiFetch from @/lib/api/client

// Add to goalsApi in client.ts:
// getPersistenceState: (id: string) => request<PersistenceState>(`/goals/${id}/attempts`),
// abortPersistence: (id: string) => request<{success:boolean}>(`/goals/${id}/persistence/abort`, {method:"POST"}),
// skipStrategy: (id: string) => request<{success:boolean;next_strategy:string}>(`/goals/${id}/persistence/skip-strategy`, {method:"POST"}),
// injectGuidance: (id: string, guidance: string) => request<{success:boolean}>(`/goals/${id}/persistence/inject-guidance`, {method:"POST",body:JSON.stringify({guidance})}),
```

### FIX: clearInterval cleanup in countdown useEffect

```typescript
// Replace the countdown useEffect with:
useEffect(() => {
  const lastBackoff = [...currentEvents].reverse()
    .find(e => e.type === "persistence_backoff_waiting");
  if (!lastBackoff) return;

  const startedAt = Date.now();
  const totalMs = (lastBackoff.payload?.remaining_seconds ?? 0) * 1000;

  // Use requestAnimationFrame-based countdown for accuracy (avoids setInterval drift)
  let rafId: number;
  function tick() {
    const elapsed = Date.now() - startedAt;
    const remaining = Math.max(0, Math.ceil((totalMs - elapsed) / 1000));
    setCountdown(remaining);
    if (remaining > 0) rafId = requestAnimationFrame(tick);
    else setCountdown(null);
  }
  rafId = requestAnimationFrame(tick);
  return () => cancelAnimationFrame(rafId);
}, [currentEvents]);
```

### FIX: predictStrategies function

```typescript
const STRATEGIES = ["SAME_APPROACH", "DIFFERENT_TOOLS", "SIMPLIFY", "DECOMPOSE", "HUMAN_GUIDANCE", "ESCALATE"];

function predictStrategies(config: typeof PERSISTENCE_PRESETS.auto): Array<{attempt: number; strategy: string}> {
  const results = [];
  let consecutiveWithSameStrategy = 0;
  let strategyIndex = 0;

  for (let attempt = 1; attempt <= config.max_attempts; attempt++) {
    if (attempt > config.escalate_after_failures && strategyIndex < STRATEGIES.length - 1) {
      strategyIndex = STRATEGIES.indexOf("ESCALATE");
    } else if (consecutiveWithSameStrategy >= config.strategy_switch_after && strategyIndex < STRATEGIES.length - 2) {
      strategyIndex++;
      consecutiveWithSameStrategy = 0;
    }
    results.push({ attempt, strategy: STRATEGIES[Math.min(strategyIndex, STRATEGIES.length - 1)] });
    consecutiveWithSameStrategy++;
  }
  return results;
}
```

### FIX: HITL gateway injection

```python
# In GoalPersistenceEngine.__init__, add:
def __init__(self, goal_id: str, config: PersistenceConfig, provider=None, db=None, hitl_gateway=None):
    # ... existing params ...
    self._hitl_gateway = hitl_gateway

# In GoalService._run_agent_loop_persistent(), pass hitl gateway:
engine = GoalPersistenceEngine(
    goal_id=goal_id,
    config=config,
    provider=provider,
    db=self._db,
    hitl_gateway=getattr(self._app_state, "hitl_gateway", None) if self._app_state else None,
)
```

### ADD: Mobile responsiveness, prefers-reduced-motion, empty states

```
Mobile:
- PersistenceStatusPanel: SVG ring collapses to 70px width on mobile; strategy timeline wraps
- LoopEngineeringPlayground: stacks to single column on mobile (flex-col)
- Attempt breakdown table: hide backoff_seconds column on mobile (hidden sm:table-cell)

prefers-reduced-motion:
All animation classes (.strategy-node-entering, countdown drain bar, attempt ring stroke transition)
must be wrapped:
  @media (prefers-reduced-motion: reduce) {
    .strategy-node-entering { animation: none !important; }
    .attempt-ring circle { transition: none !important; }
  }

Empty states:
- PersistenceStatusPanel with no attempts:
  <EmptyState title="No persistence attempts" description="Enable persistence mode when submitting a goal to see retry history here" />
- LoopEngineeringPlayground strategy preview with 0 predictions: "Configure a goal and persistence settings to see strategy predictions"
```
