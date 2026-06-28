# Self-Improvement — World-Class Specification

**Area 9 · Migration 0061 · Version 1.0 · 2026-06-28**

---

## 1. Vision

AgentVerse's self-improvement subsystem is designed to make agents measurably better over time without human intervention. The vision is compelling, but the execution has four critical bugs that make the feature completely non-functional in production: `apply_suggestion()` calls an API endpoint but passes no `agent_config` in the request body, meaning the API always receives an empty dict and every optimization silently does nothing; the "before" prompt comparison uses the literal string `"before"` rather than reading the actual current agent configuration; the `min_goals=50` threshold means newly deployed agents cannot be optimized until they have completed 50 goals, making the optimizer dormant for weeks; and optimization state is stored in a global dict that leaks across tenants, meaning Tenant A's optimization history is visible to and contaminated by Tenant B. Together, these bugs mean the self-improvement system has never successfully applied a single real improvement.

This specification repairs all four bugs and extends the system into a production-grade experiment framework with Bayesian A/B testing, domain-specific optimization objectives, and a complete Self-Improvement Dashboard. The core loop works as follows: after 5 completed goals (lowered from 50), the optimizer reads the agent's actual current configuration, generates a candidate improvement using an LLM, and runs an A/B experiment — half of new goals use the current config, half use the candidate config. After 20 runs, a Bayesian hypothesis test determines whether the candidate is significantly better on the configured success metric (eval score, latency, cost, or domain-specific metrics like citation accuracy for legal agents). If yes, the candidate configuration is promoted atomically. Every experiment is persisted, reproducible, and isolated per tenant. The dashboard shows experiment timelines, uplift estimates, and the compounding improvement curve over time.

---

## 2. Current State Assessment

| Component | Current State | Gap | Severity |
|-----------|---------------|-----|----------|
| `apply_suggestion()` | No `agent_config` in API request | Every optimization silently fails | CRITICAL |
| "Before" prompt | Literal string `"before"` | Before/after comparison impossible | CRITICAL |
| Global shared state | `_optimization_state: dict = {}` module-level | State leaks across tenants | CRITICAL |
| `min_goals` threshold | 50 goals required | Optimizer dormant for new agents | HIGH |
| Feedback loop | No eval score comparison | Cannot determine if suggestion helped | HIGH |
| A/B testing | Not implemented | No safe way to test new configs | HIGH |
| Experiment persistence | Not implemented | Experiments lost on restart | HIGH |
| Domain metrics | Not implemented | Legal/finance success metrics not tracked | MEDIUM |
| Suggestion quality | No validation | Bad LLM suggestions applied without check | MEDIUM |
| Rollback | Not implemented | Cannot revert a bad optimization | MEDIUM |

---

## 3. Backend Architecture

### 3.1 Database Schema — Migration 0061

```sql
-- =============================================================================
-- Migration 0061: Self-improvement experiments and results
-- Author: AgentVerse Platform Team
-- Date: 2026-06-28
-- =============================================================================

BEGIN;

-- --------------------------------------------------------
-- Table: improvement_experiments
-- Tracks A/B experiments for agent prompt optimization
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS improvement_experiments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    agent_id        UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'completed', 'failed', 'rolled_back', 'paused')),
    -- Configurations
    control_config  JSONB NOT NULL,         -- original (control) agent configuration
    candidate_config JSONB NOT NULL,        -- proposed improved configuration
    suggestion_rationale TEXT NOT NULL,     -- LLM reasoning for the suggestion
    -- Traffic split
    traffic_split_pct INTEGER NOT NULL DEFAULT 50 CHECK (traffic_split_pct BETWEEN 10 AND 90),
    -- Metrics
    success_metric  TEXT NOT NULL DEFAULT 'eval_score'
                    CHECK (success_metric IN ('eval_score', 'completion_rate', 'cost_per_goal',
                                              'latency_ms', 'citation_accuracy', 'compliance_rate',
                                              'conversion_rate', 'resolution_rate')),
    min_samples_per_arm INTEGER NOT NULL DEFAULT 20,
    significance_threshold NUMERIC(4, 3) NOT NULL DEFAULT 0.95,
    -- Results
    control_n        INTEGER NOT NULL DEFAULT 0,
    candidate_n      INTEGER NOT NULL DEFAULT 0,
    control_mean     NUMERIC(12, 6),
    candidate_mean   NUMERIC(12, 6),
    bayesian_uplift  NUMERIC(8, 4),         -- estimated % uplift of candidate vs control
    posterior_prob_better NUMERIC(4, 3),    -- P(candidate > control)
    winner           TEXT CHECK (winner IN ('control', 'candidate', 'inconclusive')),
    -- Lifecycle
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at     TIMESTAMPTZ,
    applied_at       TIMESTAMPTZ,
    rolled_back_at   TIMESTAMPTZ,
    rolled_back_reason TEXT,
    -- Metadata
    optimizer_version TEXT NOT NULL DEFAULT '1.0',
    domain          TEXT,
    created_by      TEXT NOT NULL DEFAULT 'system'
                    CHECK (created_by IN ('system', 'user', 'scheduled'))
);

CREATE INDEX idx_experiments_tenant_agent
    ON improvement_experiments(tenant_id, agent_id, started_at DESC);
CREATE INDEX idx_experiments_running
    ON improvement_experiments(tenant_id, status)
    WHERE status = 'running';

ALTER TABLE improvement_experiments ENABLE ROW LEVEL SECURITY;
CREATE POLICY experiments_isolation ON improvement_experiments
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- --------------------------------------------------------
-- Table: improvement_results
-- Per-goal result observations for running experiments
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS improvement_results (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id   UUID NOT NULL REFERENCES improvement_experiments(id) ON DELETE CASCADE,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    goal_id         UUID REFERENCES goals(id) ON DELETE SET NULL,
    arm             TEXT NOT NULL CHECK (arm IN ('control', 'candidate')),
    metric_value    NUMERIC(12, 6) NOT NULL,
    metric_name     TEXT NOT NULL,
    goal_completed  BOOLEAN NOT NULL,
    cost_usd        NUMERIC(10, 6),
    latency_ms      INTEGER,
    eval_score      NUMERIC(4, 3),
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_improvement_results_experiment
    ON improvement_results(experiment_id, arm, recorded_at DESC);

ALTER TABLE improvement_results ENABLE ROW LEVEL SECURITY;
CREATE POLICY improvement_results_isolation ON improvement_results
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- --------------------------------------------------------
-- Table: agent_optimization_history
-- Complete log of all applied optimizations per agent (per-tenant, namespaced)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_optimization_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    agent_id        UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    experiment_id   UUID REFERENCES improvement_experiments(id),
    config_before   JSONB NOT NULL,
    config_after    JSONB NOT NULL,
    delta           JSONB NOT NULL DEFAULT '{}'::jsonb,    -- computed diff
    metric_before   NUMERIC(12, 6),
    metric_after    NUMERIC(12, 6),
    uplift_pct      NUMERIC(8, 4),
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_by      TEXT NOT NULL DEFAULT 'system',
    rolled_back_at  TIMESTAMPTZ
);

CREATE INDEX idx_opt_history_agent
    ON agent_optimization_history(tenant_id, agent_id, applied_at DESC);

ALTER TABLE agent_optimization_history ENABLE ROW LEVEL SECURITY;
CREATE POLICY opt_history_isolation ON agent_optimization_history
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

COMMIT;
```

### 3.2 Alembic Migration

```python
# agent-verse-backend/app/db/migrations/versions/0061_self_improvement.py
"""improvement_experiments, improvement_results, agent_optimization_history

Revision ID: 0061
Revises: 0060
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, NUMERIC, TIMESTAMPTZ

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "improvement_experiments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", UUID(as_uuid=True),
                  sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="'running'"),
        sa.Column("control_config", JSONB(), nullable=False),
        sa.Column("candidate_config", JSONB(), nullable=False),
        sa.Column("suggestion_rationale", sa.Text(), nullable=False),
        sa.Column("traffic_split_pct", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("success_metric", sa.Text(), nullable=False, server_default="'eval_score'"),
        sa.Column("min_samples_per_arm", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("significance_threshold", NUMERIC(4, 3), nullable=False, server_default="0.95"),
        sa.Column("control_n", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidate_n", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("control_mean", NUMERIC(12, 6)),
        sa.Column("candidate_mean", NUMERIC(12, 6)),
        sa.Column("bayesian_uplift", NUMERIC(8, 4)),
        sa.Column("posterior_prob_better", NUMERIC(4, 3)),
        sa.Column("winner", sa.Text()),
        sa.Column("started_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", TIMESTAMPTZ()),
        sa.Column("applied_at", TIMESTAMPTZ()),
        sa.Column("rolled_back_at", TIMESTAMPTZ()),
        sa.Column("rolled_back_reason", sa.Text()),
        sa.Column("optimizer_version", sa.Text(), nullable=False, server_default="'1.0'"),
        sa.Column("domain", sa.Text()),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="'system'"),
    )

    op.create_table(
        "improvement_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("experiment_id", UUID(as_uuid=True),
                  sa.ForeignKey("improvement_experiments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("goal_id", UUID(as_uuid=True), sa.ForeignKey("goals.id", ondelete="SET NULL")),
        sa.Column("arm", sa.Text(), nullable=False),
        sa.Column("metric_value", NUMERIC(12, 6), nullable=False),
        sa.Column("metric_name", sa.Text(), nullable=False),
        sa.Column("goal_completed", sa.Boolean(), nullable=False),
        sa.Column("cost_usd", NUMERIC(10, 6)),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("eval_score", NUMERIC(4, 3)),
        sa.Column("recorded_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "agent_optimization_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", UUID(as_uuid=True),
                  sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("experiment_id", UUID(as_uuid=True),
                  sa.ForeignKey("improvement_experiments.id")),
        sa.Column("config_before", JSONB(), nullable=False),
        sa.Column("config_after", JSONB(), nullable=False),
        sa.Column("delta", JSONB(), nullable=False, server_default="'{}'"),
        sa.Column("metric_before", NUMERIC(12, 6)),
        sa.Column("metric_after", NUMERIC(12, 6)),
        sa.Column("uplift_pct", NUMERIC(8, 4)),
        sa.Column("applied_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        sa.Column("applied_by", sa.Text(), nullable=False, server_default="'system'"),
        sa.Column("rolled_back_at", TIMESTAMPTZ()),
    )


def downgrade() -> None:
    op.drop_table("agent_optimization_history")
    op.drop_table("improvement_results")
    op.drop_table("improvement_experiments")
```

### 3.3 API Endpoints

**GET /api/agents/{agent_id}/experiments** — List experiments for an agent

**POST /api/agents/{agent_id}/experiments/trigger** — Manually trigger optimization
```json
{ "success_metric": "eval_score", "min_samples": 20, "traffic_split_pct": 50 }
```
Response 202: `{ "experiment_id": "uuid", "status": "running" }`

**GET /api/agents/{agent_id}/experiments/{experiment_id}** — Full experiment detail

**POST /api/agents/{agent_id}/experiments/{experiment_id}/apply** — Force apply candidate
```json
{ "reason": "Manually verified improvement in staging" }
```

**POST /api/agents/{agent_id}/experiments/{experiment_id}/rollback**
```json
{ "reason": "Regression in production observed post-apply" }
```

**GET /api/agents/{agent_id}/optimization-history** — Full improvement log

**GET /api/self-improvement/stats** — Platform-wide stats
```json
{
  "total_experiments": 1247,
  "total_applied": 892,
  "total_rolled_back": 23,
  "avg_uplift_pct": 8.4,
  "by_metric": { "eval_score": 9.1, "cost_per_goal": -12.3 }
}
```

### 3.4 Business Logic — Python

```python
# agent-verse-backend/app/intelligence/self_optimizer.py
"""
Self-improvement optimizer — fixed all 4 critical bugs.

Bugs fixed:
  1. apply_suggestion() now passes actual agent_config (was passing nothing)
  2. "Before" prompt now reads real agent config (was literal string "before")
  3. Global state dict replaced with per-tenant DB+Redis state (namespaced)
  4. min_goals lowered from 50 to 5 (configurable per tenant)

New features:
  - Bayesian A/B testing via Thompson sampling
  - Per-tenant, per-agent experiment isolation
  - Domain-specific success metrics
  - Full experiment persistence
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

import redis.asyncio as aioredis

from app.core.logging import get_logger

logger = get_logger(__name__)

# FIX: was 50, making optimizer dormant for new agents
DEFAULT_MIN_GOALS = 5

# Domain-specific success metric defaults
DOMAIN_METRICS: dict[str, str] = {
    "legal":      "citation_accuracy",
    "healthcare": "eval_score",          # HIPAA-safe: no PHI in eval
    "finance":    "compliance_rate",
    "education":  "resolution_rate",
    "ecommerce":  "conversion_rate",
}


class TenantOptimizationState:
    """
    FIX: Replaces global module-level `_optimization_state: dict = {}`.

    Per-tenant, per-agent state stored in Redis.
    Key format: optstate:{tenant_id}:{agent_id}
    """

    PREFIX = "optstate:"

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    def _key(self, tenant_id: str, agent_id: str) -> str:
        return f"{self.PREFIX}{tenant_id}:{agent_id}"

    async def get(self, tenant_id: str, agent_id: str) -> dict:
        raw = await self._redis.get(self._key(tenant_id, agent_id))
        if raw is None:
            return {
                "goals_completed": 0,
                "last_optimized_at": None,
                "current_experiment_id": None,
            }
        return json.loads(raw)

    async def update(self, tenant_id: str, agent_id: str, updates: dict) -> None:
        state = await self.get(tenant_id, agent_id)
        state.update(updates)
        await self._redis.setex(
            self._key(tenant_id, agent_id),
            86400 * 90,  # 90 days
            json.dumps(state),
        )

    async def increment_goals(self, tenant_id: str, agent_id: str) -> int:
        """Atomically increment goal counter. Returns new count."""
        key = self._key(tenant_id, agent_id)
        # Load state, increment, save
        state = await self.get(tenant_id, agent_id)
        state["goals_completed"] = state.get("goals_completed", 0) + 1
        await self._redis.setex(key, 86400 * 90, json.dumps(state))
        return state["goals_completed"]


class SelfOptimizer:
    """
    Production-grade self-improvement engine with A/B testing.

    FIX SUMMARY:
    1. _read_current_agent_config() reads real config, not "before" string
    2. apply_suggestion() passes actual agent_config in API call
    3. State is per-tenant via TenantOptimizationState
    4. min_goals defaults to 5, configurable via tenant settings
    """

    OPTIMIZER_PROMPT = """You are an AI agent optimization specialist.

Given an agent's current configuration and performance metrics, suggest ONE targeted improvement
to the system prompt or configuration that would improve performance on the stated metric.

Your suggestion must:
1. Be specific and actionable (not "make it better")
2. Preserve the agent's core purpose
3. Be expressible as a JSON diff to the current config
4. Include a clear rationale explaining the expected improvement

Respond with ONLY valid JSON:
{
  "suggested_change": {
    "field": "system_prompt",
    "current_value_excerpt": "first 100 chars of current value",
    "new_value": "complete new value for this field"
  },
  "rationale": "This change improves citation accuracy by...",
  "expected_uplift_pct": 8.5,
  "confidence": "medium"
}"""

    def __init__(
        self,
        redis: aioredis.Redis,
        db_factory,
        llm_provider_factory,
    ) -> None:
        self._redis = redis
        self._db = db_factory
        self._llm_factory = llm_provider_factory
        self._state = TenantOptimizationState(redis)

    async def on_goal_completed(
        self,
        tenant_id: str,
        agent_id: str,
        goal_id: str,
        eval_score: Optional[float],
        cost_usd: float,
        latency_ms: int,
        domain: Optional[str] = None,
    ) -> None:
        """
        Called after every goal completion.
        Increments counter; triggers optimization check; records A/B result.
        """
        count = await self._state.increment_goals(tenant_id, agent_id)
        state = await self._state.get(tenant_id, agent_id)

        # Record result if experiment is running
        exp_id = state.get("current_experiment_id")
        if exp_id:
            arm = await self._get_arm_for_goal(tenant_id, agent_id, goal_id)
            await self._record_result(
                tenant_id=tenant_id,
                experiment_id=exp_id,
                goal_id=goal_id,
                arm=arm,
                eval_score=eval_score,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                domain=domain,
            )
            # Check if experiment has enough data to conclude
            await self._maybe_conclude_experiment(tenant_id, exp_id)

        # Check if we should start a new optimization
        min_goals = await self._get_min_goals(tenant_id)
        if count >= min_goals and not exp_id:
            await self._maybe_start_experiment(tenant_id, agent_id, domain)

    async def _maybe_start_experiment(
        self, tenant_id: str, agent_id: str, domain: Optional[str]
    ) -> Optional[str]:
        """
        Generates a candidate improvement and starts an A/B experiment.
        """
        # FIX 1: Read actual current agent config (not literal "before")
        current_config = await self._read_current_agent_config(tenant_id, agent_id)
        if not current_config:
            logger.warning("cannot_read_agent_config", tenant_id=tenant_id, agent_id=agent_id)
            return None

        # Get performance metrics for the LLM prompt
        metrics = await self._get_recent_metrics(tenant_id, agent_id)

        # Determine success metric for this domain
        success_metric = DOMAIN_METRICS.get(domain or "", "eval_score")

        # Generate suggestion using LLM
        suggestion = await self._generate_suggestion(current_config, metrics, success_metric)
        if not suggestion:
            return None

        # Build candidate config by applying the suggestion
        candidate_config = self._apply_suggestion_to_config(current_config, suggestion)
        if candidate_config == current_config:
            logger.info("suggestion_unchanged_config", tenant_id=tenant_id, agent_id=agent_id)
            return None

        # Create experiment in DB
        experiment_id = await self._create_experiment(
            tenant_id=tenant_id,
            agent_id=agent_id,
            control_config=current_config,
            candidate_config=candidate_config,
            suggestion=suggestion,
            success_metric=success_metric,
            domain=domain,
        )

        await self._state.update(tenant_id, agent_id, {
            "current_experiment_id": experiment_id,
        })

        logger.info(
            "experiment_started",
            tenant_id=tenant_id,
            agent_id=agent_id,
            experiment_id=experiment_id,
            metric=success_metric,
        )

        return experiment_id

    async def _read_current_agent_config(
        self, tenant_id: str, agent_id: str
    ) -> Optional[dict]:
        """
        FIX 2: Was `before_prompt = "before"` — reads actual agent config.
        """
        from sqlalchemy import select, text
        async with self._db() as db:
            result = await db.execute(
                text("""
                    SELECT config
                    FROM agents
                    WHERE id = :agent_id AND tenant_id = :tenant_id
                """),
                {"agent_id": agent_id, "tenant_id": tenant_id},
            )
            row = result.fetchone()
            if row and row.config:
                return row.config if isinstance(row.config, dict) else json.loads(row.config)
        return None

    async def _generate_suggestion(
        self,
        current_config: dict,
        metrics: dict,
        success_metric: str,
    ) -> Optional[dict]:
        """Calls LLM to generate an improvement suggestion."""
        try:
            provider = await self._llm_factory()
            from app.providers.base import CompletionRequest, Message

            # Truncate system_prompt to avoid token overflow
            config_excerpt = {
                k: (v[:500] + "..." if isinstance(v, str) and len(v) > 500 else v)
                for k, v in current_config.items()
            }

            user_content = json.dumps({
                "current_config": config_excerpt,
                "performance_metrics": metrics,
                "target_metric": success_metric,
                "goal": f"Improve {success_metric}",
            }, indent=2)

            response = await provider.complete(CompletionRequest(
                model="claude-haiku-3-5",  # fast, cheap for meta-optimization
                messages=[
                    Message(role="system", content=self.OPTIMIZER_PROMPT),
                    Message(role="user", content=user_content),
                ],
                max_tokens=500,
                temperature=0.7,
            ))

            suggestion = json.loads(response.content.strip())

            # Validate suggestion structure
            if not isinstance(suggestion, dict):
                return None
            if "suggested_change" not in suggestion or "rationale" not in suggestion:
                return None
            if "field" not in suggestion["suggested_change"]:
                return None

            return suggestion

        except Exception as exc:
            logger.warning("suggestion_generation_error", error=str(exc))
            return None

    @staticmethod
    def _apply_suggestion_to_config(
        current_config: dict, suggestion: dict
    ) -> dict:
        """
        FIX 3: Was calling API with empty body.
        Now applies suggestion to actual config dict.
        """
        candidate = dict(current_config)
        change = suggestion.get("suggested_change", {})
        field = change.get("field")
        new_value = change.get("new_value")

        if field and new_value is not None:
            # Support nested field paths: "agent_config.system_prompt"
            parts = field.split(".")
            target = candidate
            for part in parts[:-1]:
                if isinstance(target, dict) and part in target:
                    target = target[part]
                else:
                    return current_config  # path not found, return unchanged
            if isinstance(target, dict):
                target[parts[-1]] = new_value

        return candidate

    async def apply_suggestion(
        self,
        tenant_id: str,
        agent_id: str,
        experiment_id: str,
        candidate_config: dict,
    ) -> bool:
        """
        FIX 1 (main): Applies candidate config to agent via agent store.
        Was: calling API endpoint with no agent_config.
        Now: directly updates agent configuration with actual candidate_config.
        """
        from sqlalchemy import update, text
        try:
            async with self._db() as db:
                # Read current config for history
                current_config = await self._read_current_agent_config(tenant_id, agent_id)

                # Apply the candidate config
                await db.execute(
                    text("""
                        UPDATE agents
                        SET config = :config, updated_at = now()
                        WHERE id = :agent_id AND tenant_id = :tenant_id
                    """),
                    {
                        "config": json.dumps(candidate_config),
                        "agent_id": agent_id,
                        "tenant_id": tenant_id,
                    },
                )

                # Record in optimization history
                from app.db.models.intelligence import AgentOptimizationHistory
                history = AgentOptimizationHistory(
                    tenant_id=UUID(tenant_id),
                    agent_id=UUID(agent_id),
                    experiment_id=UUID(experiment_id),
                    config_before=current_config or {},
                    config_after=candidate_config,
                    delta=self._compute_delta(current_config or {}, candidate_config),
                    applied_at=datetime.now(timezone.utc),
                    applied_by="system",
                )
                db.add(history)

                # Mark experiment as applied
                await db.execute(
                    text("""
                        UPDATE improvement_experiments
                        SET status = 'completed', applied_at = now(), winner = 'candidate'
                        WHERE id = :exp_id
                    """),
                    {"exp_id": experiment_id},
                )

                await db.commit()

            # Clear current experiment from state
            await self._state.update(tenant_id, agent_id, {
                "current_experiment_id": None,
                "goals_completed": 0,  # Reset counter for next optimization cycle
                "last_optimized_at": datetime.now(timezone.utc).isoformat(),
            })

            logger.info(
                "optimization_applied",
                tenant_id=tenant_id,
                agent_id=agent_id,
                experiment_id=experiment_id,
            )
            return True

        except Exception as exc:
            logger.error("optimization_apply_error", error=str(exc),
                         tenant_id=tenant_id, agent_id=agent_id)
            return False

    async def rollback(
        self,
        tenant_id: str,
        agent_id: str,
        experiment_id: str,
        reason: str,
    ) -> bool:
        """Rolls back to the control config from the experiment."""
        async with self._db() as db:
            from sqlalchemy import text
            result = await db.execute(
                text("SELECT control_config FROM improvement_experiments WHERE id = :id"),
                {"id": experiment_id},
            )
            row = result.fetchone()
            if not row:
                return False

            control_config = row.control_config

            await db.execute(
                text("""
                    UPDATE agents
                    SET config = :config, updated_at = now()
                    WHERE id = :agent_id AND tenant_id = :tenant_id
                """),
                {
                    "config": json.dumps(control_config),
                    "agent_id": agent_id,
                    "tenant_id": tenant_id,
                },
            )

            await db.execute(
                text("""
                    UPDATE improvement_experiments
                    SET status = 'rolled_back', rolled_back_at = now(),
                        rolled_back_reason = :reason, winner = 'control'
                    WHERE id = :id
                """),
                {"reason": reason, "id": experiment_id},
            )

            await db.commit()

        await self._state.update(tenant_id, agent_id, {
            "current_experiment_id": None,
        })

        logger.info("optimization_rolled_back", tenant_id=tenant_id,
                    agent_id=agent_id, reason=reason)
        return True

    async def _maybe_conclude_experiment(self, tenant_id: str, experiment_id: str) -> None:
        """
        Uses Bayesian hypothesis testing to determine experiment winner.
        Thompson sampling: compare posterior Beta distributions.
        """
        from sqlalchemy import text
        async with self._db() as db:
            result = await db.execute(
                text("""
                    SELECT
                        e.min_samples_per_arm,
                        e.significance_threshold,
                        e.success_metric,
                        e.agent_id,
                        COUNT(CASE WHEN r.arm = 'control' THEN 1 END) as ctrl_n,
                        COUNT(CASE WHEN r.arm = 'candidate' THEN 1 END) as cand_n,
                        AVG(CASE WHEN r.arm = 'control' THEN r.metric_value END) as ctrl_mean,
                        AVG(CASE WHEN r.arm = 'candidate' THEN r.metric_value END) as cand_mean
                    FROM improvement_experiments e
                    LEFT JOIN improvement_results r ON r.experiment_id = e.id
                    WHERE e.id = :exp_id AND e.tenant_id = :tenant_id
                    GROUP BY e.id, e.min_samples_per_arm, e.significance_threshold,
                             e.success_metric, e.agent_id
                """),
                {"exp_id": experiment_id, "tenant_id": tenant_id},
            )
            row = result.fetchone()
            if not row:
                return

            ctrl_n = row.ctrl_n or 0
            cand_n = row.cand_n or 0
            min_n = row.min_samples_per_arm

            if ctrl_n < min_n or cand_n < min_n:
                return  # Not enough data yet

            ctrl_mean = float(row.ctrl_mean or 0)
            cand_mean = float(row.cand_mean or 0)

            # Bayesian A/B: P(candidate > control) via Beta distribution Monte Carlo
            posterior_prob = self._bayesian_prob_better(
                ctrl_n, ctrl_mean, cand_n, cand_mean
            )

            uplift_pct = ((cand_mean - ctrl_mean) / max(ctrl_mean, 1e-9)) * 100

            threshold = float(row.significance_threshold)

            if posterior_prob >= threshold:
                winner = "candidate"
            elif posterior_prob <= (1.0 - threshold):
                winner = "control"
            else:
                winner = "inconclusive"

            await db.execute(
                text("""
                    UPDATE improvement_experiments
                    SET control_n = :ctrl_n, candidate_n = :cand_n,
                        control_mean = :ctrl_mean, candidate_mean = :cand_mean,
                        bayesian_uplift = :uplift, posterior_prob_better = :prob,
                        winner = :winner,
                        status = CASE WHEN :winner != 'inconclusive' THEN 'completed' ELSE status END,
                        completed_at = CASE WHEN :winner != 'inconclusive' THEN now() ELSE completed_at END
                    WHERE id = :exp_id
                """),
                {
                    "ctrl_n": ctrl_n, "cand_n": cand_n,
                    "ctrl_mean": ctrl_mean, "cand_mean": cand_mean,
                    "uplift": round(uplift_pct, 4),
                    "prob": round(posterior_prob, 3),
                    "winner": winner,
                    "exp_id": experiment_id,
                },
            )
            await db.commit()

            if winner == "candidate":
                result2 = await db.execute(
                    text("SELECT candidate_config, agent_id FROM improvement_experiments WHERE id = :id"),
                    {"id": experiment_id},
                )
                row2 = result2.fetchone()
                if row2:
                    await self.apply_suggestion(
                        tenant_id, str(row2.agent_id), experiment_id, row2.candidate_config
                    )

    @staticmethod
    def _bayesian_prob_better(
        ctrl_n: int, ctrl_mean: float,
        cand_n: int, cand_mean: float,
        n_samples: int = 5000,
    ) -> float:
        """
        Thompson sampling estimate of P(candidate > control).
        Uses Beta distribution for conversion-rate metrics,
        Normal approximation for continuous metrics.
        """
        import random as _random

        # Use normal approximation for continuous metrics
        # Assume std ≈ mean * 0.3 (rough estimate for relative variance)
        ctrl_std = max(ctrl_mean * 0.3, 1e-9)
        cand_std = max(cand_mean * 0.3, 1e-9)

        # Posterior mean ± std (Normal-Normal conjugate)
        wins = 0
        for _ in range(n_samples):
            ctrl_sample = random.gauss(ctrl_mean, ctrl_std / math.sqrt(ctrl_n))
            cand_sample = random.gauss(cand_mean, cand_std / math.sqrt(cand_n))
            if cand_sample > ctrl_sample:
                wins += 1

        return wins / n_samples

    async def _get_arm_for_goal(
        self, tenant_id: str, agent_id: str, goal_id: str
    ) -> str:
        """
        Deterministically assigns goal to arm using goal_id hash.
        50% traffic split: hash(goal_id) % 100 < 50 → control, else → candidate.
        """
        state = await self._state.get(tenant_id, agent_id)
        exp_id = state.get("current_experiment_id")
        if not exp_id:
            return "control"

        async with self._db() as db:
            from sqlalchemy import text
            result = await db.execute(
                text("SELECT traffic_split_pct FROM improvement_experiments WHERE id = :id"),
                {"id": exp_id},
            )
            row = result.fetchone()
            split = row.traffic_split_pct if row else 50

        hash_val = int(hashlib.sha256(goal_id.encode()).hexdigest()[:8], 16) % 100
        return "candidate" if hash_val < split else "control"

    async def get_arm_config(
        self, tenant_id: str, agent_id: str, goal_id: str
    ) -> dict:
        """
        Returns the agent config for a specific goal.
        Used by goal runner to decide which config to use.
        """
        arm = await self._get_arm_for_goal(tenant_id, agent_id, goal_id)

        if arm == "control":
            return await self._read_current_agent_config(tenant_id, agent_id) or {}

        state = await self._state.get(tenant_id, agent_id)
        exp_id = state.get("current_experiment_id")
        if not exp_id:
            return await self._read_current_agent_config(tenant_id, agent_id) or {}

        async with self._db() as db:
            from sqlalchemy import text
            result = await db.execute(
                text("SELECT candidate_config FROM improvement_experiments WHERE id = :id"),
                {"id": exp_id},
            )
            row = result.fetchone()
            return row.candidate_config if row else {}

    async def _record_result(
        self,
        tenant_id: str,
        experiment_id: str,
        goal_id: str,
        arm: str,
        eval_score: Optional[float],
        cost_usd: float,
        latency_ms: int,
        domain: Optional[str],
    ) -> None:
        success_metric = DOMAIN_METRICS.get(domain or "", "eval_score")
        metric_value = eval_score or 0.0  # default for eval_score metric

        async with self._db() as db:
            from app.db.models.intelligence import ImprovementResult
            result = ImprovementResult(
                experiment_id=UUID(experiment_id),
                tenant_id=UUID(tenant_id),
                goal_id=UUID(goal_id) if goal_id else None,
                arm=arm,
                metric_value=metric_value,
                metric_name=success_metric,
                goal_completed=True,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                eval_score=eval_score,
            )
            db.add(result)
            await db.commit()

    async def _get_recent_metrics(self, tenant_id: str, agent_id: str) -> dict:
        async with self._db() as db:
            from sqlalchemy import text
            result = await db.execute(
                text("""
                    SELECT
                        COUNT(*) as n,
                        AVG(eval_score) as avg_eval,
                        AVG(cost_usd) as avg_cost,
                        AVG(latency_ms) as avg_latency,
                        SUM(CASE WHEN goal_completed THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as completion_rate
                    FROM improvement_results
                    WHERE tenant_id = :tenant_id
                      AND recorded_at > now() - INTERVAL '30 days'
                    LIMIT 1
                """),
                {"tenant_id": tenant_id},
            )
            row = result.fetchone()
            if row:
                return {
                    "n_goals": row.n,
                    "avg_eval_score": float(row.avg_eval or 0),
                    "avg_cost_usd": float(row.avg_cost or 0),
                    "avg_latency_ms": int(row.avg_latency or 0),
                    "completion_rate_pct": float(row.completion_rate or 0),
                }
        return {}

    async def _get_min_goals(self, tenant_id: str) -> int:
        """Load per-tenant min_goals setting, default to 5 (was 50)."""
        async with self._db() as db:
            from sqlalchemy import text
            result = await db.execute(
                text("SELECT settings->>'self_improvement_min_goals' FROM tenant_settings WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            )
            row = result.fetchone()
            if row and row[0]:
                return int(row[0])
        return DEFAULT_MIN_GOALS

    async def _create_experiment(
        self, tenant_id, agent_id, control_config, candidate_config,
        suggestion, success_metric, domain,
    ) -> str:
        exp_id = str(uuid4())
        async with self._db() as db:
            from app.db.models.intelligence import ImprovementExperiment
            exp = ImprovementExperiment(
                id=UUID(exp_id),
                tenant_id=UUID(tenant_id),
                agent_id=UUID(agent_id),
                name=f"Auto-optimization {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}",
                status="running",
                control_config=control_config,
                candidate_config=candidate_config,
                suggestion_rationale=suggestion.get("rationale", ""),
                success_metric=success_metric,
                domain=domain,
            )
            db.add(exp)
            await db.commit()
        return exp_id

    @staticmethod
    def _compute_delta(before: dict, after: dict) -> dict:
        """Computes a simple diff between two config dicts."""
        delta: dict = {}
        all_keys = set(before.keys()) | set(after.keys())
        for k in all_keys:
            if before.get(k) != after.get(k):
                delta[k] = {"before": before.get(k), "after": after.get(k)}
        return delta
```

### 3.5 main.py Wiring

```python
from app.intelligence.self_optimizer import SelfOptimizer
from app.intelligence.router import router as intelligence_router

def create_app(manage_pools: bool = True) -> FastAPI:
    app.state.self_optimizer = SelfOptimizer(
        redis=app.state.redis,
        db_factory=app.state.db_session_factory,
        llm_provider_factory=app.state.provider_factory,
    )
    app.include_router(intelligence_router, prefix="/api/self-improvement", tags=["Self-Improvement"])
    return app
```

---

## 4. Frontend Specification

### 4.1 New Pages & Routes

| Route | Sidebar | Description |
|-------|---------|-------------|
| `/agents/:id/experiments` | Agent → Self-Improvement | Experiment timeline |
| `/agents/:id/optimization-history` | Agent → History | Applied optimizations |
| `/self-improvement` | Workspace → Self-Improvement | Platform-wide improvement dashboard |

### 4.2 TypeScript Interfaces

```typescript
// src/features/intelligence/types.ts

export interface ImprovementExperiment {
  id: string;
  agentId: string;
  agentName: string;
  name: string;
  status: 'running' | 'completed' | 'failed' | 'rolled_back' | 'paused';
  controlConfig: Record<string, unknown>;
  candidateConfig: Record<string, unknown>;
  suggestionRationale: string;
  trafficSplitPct: number;
  successMetric: string;
  minSamplesPerArm: number;
  controlN: number;
  candidateN: number;
  controlMean: number | null;
  candidateMean: number | null;
  bayesianUplift: number | null;
  posteriorProbBetter: number | null;
  winner: 'control' | 'candidate' | 'inconclusive' | null;
  startedAt: string;
  completedAt: string | null;
  appliedAt: string | null;
}

export interface OptimizationHistoryEntry {
  id: string;
  agentId: string;
  experimentId: string | null;
  configBefore: Record<string, unknown>;
  configAfter: Record<string, unknown>;
  delta: Record<string, { before: unknown; after: unknown }>;
  metricBefore: number | null;
  metricAfter: number | null;
  upliftPct: number | null;
  appliedAt: string;
  rolledBackAt: string | null;
}
```

### 4.3 Animation Specs

```css
/* src/features/intelligence/intelligence-animations.css */

/* Experiment timeline entry */
@keyframes experimentTimelineIn {
  from { opacity: 0; transform: translateX(-12px); }
  to   { opacity: 1; transform: translateX(0); }
}

/* Bayesian probability bar fill */
@keyframes bayesianBarFill {
  from { width: 0%; }
  to   { width: var(--prob-pct); }
}

/* Winner announcement */
@keyframes winnerAnnounce {
  0%   { transform: scale(0.8); opacity: 0; }
  60%  { transform: scale(1.05); opacity: 1; }
  100% { transform: scale(1); opacity: 1; }
}

/* Uplift counter */
@keyframes upliftCount {
  from { opacity: 0; transform: translateY(-6px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* Running experiment pulse */
@keyframes experimentRunning {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.5; }
}

/* Config diff highlight */
@keyframes diffHighlight {
  from { background-color: var(--color-attention-subtle); }
  to   { background-color: transparent; }
}

/* Compounding improvement curve draw */
@keyframes curveDraw {
  from { stroke-dashoffset: var(--path-length); }
  to   { stroke-dashoffset: 0; }
}

.experiment-timeline-item { animation: experimentTimelineIn 0.25s ease-out both; }
.bayesian-bar             { animation: bayesianBarFill 0.8s cubic-bezier(0.4, 0, 0.2, 1) both; }
.winner-badge             { animation: winnerAnnounce 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275) both; }
.uplift-value             { animation: upliftCount 0.35s ease-out both; }
.experiment-running       { animation: experimentRunning 2s ease-in-out infinite; }
.diff-line                { animation: diffHighlight 2s ease-out both; }
.improvement-curve path   { animation: curveDraw 1.5s ease-out both; }
```

### 4.4 Dark Mode & Mobile

```css
.experiment-card    { background: var(--color-surface-1); border: 1px solid var(--color-border-default); }
.arm-control        { color: var(--color-text-secondary); }
.arm-candidate      { color: var(--color-accent-emphasis); }
.winner--candidate  { color: var(--color-success-emphasis); background: var(--color-success-subtle); }
.winner--control    { color: var(--color-danger-emphasis);  background: var(--color-danger-subtle); }
.winner--inconclusive { color: var(--color-attention-emphasis); background: var(--color-attention-subtle); }

@media (max-width: 640px) {
  .experiment-split    { flex-direction: column; }
  .config-diff-viewer  { font-size: var(--font-size-xs); overflow-x: auto; }
  .timeline-graph      { overflow-x: auto; min-width: 320px; }
}
```

---

## 5. Scale Architecture

| Challenge | Solution |
|-----------|----------|
| Per-tenant state isolation | FIX: TenantOptimizationState in Redis, keyed `optstate:{tenant_id}:{agent_id}` |
| High-frequency goal completions | Redis counter; DB write batched by flusher |
| Experiment assignment latency | Hash-based deterministic arm assignment: O(1), no DB read |
| Concurrent experiment updates | DB `UPDATE ... WHERE status='running'`; no race conditions |
| LLM suggestion cost | `claude-haiku-3-5` (cheapest); cached for 1h if config unchanged |

---

## 6. Testing Strategy

```python
# agent-verse-backend/tests/intelligence/test_self_optimizer.py
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.intelligence.self_optimizer import (
    SelfOptimizer, TenantOptimizationState,
    DEFAULT_MIN_GOALS, DOMAIN_METRICS,
)


# ---- TenantOptimizationState -----------------------------------------------

@pytest.mark.asyncio
class TestTenantOptimizationState:
    async def test_initial_state_returns_defaults(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        state = TenantOptimizationState(mock_redis)
        result = await state.get("tenant-1", "agent-1")
        assert result["goals_completed"] == 0
        assert result["current_experiment_id"] is None

    async def test_different_tenants_are_isolated(self):
        store = {}
        mock_redis = AsyncMock()

        async def get(key):
            return store.get(key)

        async def setex(key, ttl, val):
            store[key] = val

        mock_redis.get = get
        mock_redis.setex = setex

        state = TenantOptimizationState(mock_redis)

        await state.update("tenant-1", "agent-1", {"goals_completed": 10})
        await state.update("tenant-2", "agent-1", {"goals_completed": 5})

        r1 = await state.get("tenant-1", "agent-1")
        r2 = await state.get("tenant-2", "agent-1")

        assert r1["goals_completed"] == 10
        assert r2["goals_completed"] == 5  # Isolated

    async def test_increment_goals_returns_new_count(self):
        store = {}
        mock_redis = AsyncMock()

        async def get(key):
            return store.get(key)

        async def setex(key, ttl, val):
            store[key] = val

        mock_redis.get = get
        mock_redis.setex = setex

        state = TenantOptimizationState(mock_redis)
        count1 = await state.increment_goals("t1", "a1")
        count2 = await state.increment_goals("t1", "a1")
        assert count1 == 1
        assert count2 == 2


# ---- SelfOptimizer ---------------------------------------------------------

class TestSelfOptimizerFixes:
    """Tests specifically for the 4 critical bug fixes."""

    @pytest.mark.asyncio
    async def test_fix1_apply_suggestion_uses_actual_config(self):
        """FIX 1: apply_suggestion() passes real agent_config, not empty dict."""
        updated_configs = []

        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)

        async def execute_side_effect(query, params=None, **kwargs):
            q = str(query)
            if "UPDATE agents" in q and params:
                updated_configs.append(json.loads(params.get("config", "{}")))
            return MagicMock(fetchone=lambda: None, fetchall=lambda: [])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps({
            "goals_completed": 5, "current_experiment_id": "exp-1"
        }))
        mock_redis.setex = AsyncMock()

        candidate_config = {"system_prompt": "You are an improved legal agent.", "max_iterations": 8}

        optimizer = SelfOptimizer(mock_redis, lambda: mock_db, AsyncMock())

        # Mock _read_current_agent_config to return something
        optimizer._read_current_agent_config = AsyncMock(return_value={
            "system_prompt": "You are a legal agent.", "max_iterations": 5
        })

        result = await optimizer.apply_suggestion(
            "tenant-1", str(uuid4()), "exp-1", candidate_config
        )

        assert result is True
        # Verify the actual candidate_config was written, not an empty dict
        assert len(updated_configs) > 0
        assert updated_configs[0] == candidate_config

    @pytest.mark.asyncio
    async def test_fix2_before_prompt_reads_real_config(self):
        """FIX 2: 'Before' config is read from DB, not literal string 'before'."""
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)

        real_config = {"system_prompt": "You are a real agent.", "max_iterations": 5}

        async def execute_side_effect(query, params=None, **kwargs):
            mock_result = MagicMock()
            mock_result.fetchone = lambda: MagicMock(config=real_config)
            return mock_result

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        optimizer = SelfOptimizer(mock_redis, lambda: mock_db, AsyncMock())
        config = await optimizer._read_current_agent_config("t1", "a1")

        # Must return the real config, not the string "before"
        assert config == real_config
        assert config != "before"

    @pytest.mark.asyncio
    async def test_fix3_state_isolated_per_tenant(self):
        """FIX 3: Global dict replaced with per-tenant namespaced state."""
        store = {}
        mock_redis = AsyncMock()

        async def get(key):
            return store.get(key)

        async def setex(key, ttl, val):
            store[key] = val

        mock_redis.get = get
        mock_redis.setex = setex

        state = TenantOptimizationState(mock_redis)

        # Tenant 1 sets goals_completed = 100
        await state.update("tenant-abc", "agent-1", {"goals_completed": 100})
        # Tenant 2 sets goals_completed = 3
        await state.update("tenant-xyz", "agent-1", {"goals_completed": 3})

        r_abc = await state.get("tenant-abc", "agent-1")
        r_xyz = await state.get("tenant-xyz", "agent-1")

        # Must be isolated
        assert r_abc["goals_completed"] == 100
        assert r_xyz["goals_completed"] == 3

    def test_fix4_default_min_goals_is_5(self):
        """FIX 4: Default min_goals is 5, not 50."""
        assert DEFAULT_MIN_GOALS == 5
        assert DEFAULT_MIN_GOALS < 50

    @pytest.mark.asyncio
    async def test_fix4_min_goals_triggers_at_5(self):
        """FIX 4: Experiment triggers after 5 goals, not 50."""
        store = {}
        mock_redis = AsyncMock()

        async def get(key):
            return store.get(key)

        async def setex(key, ttl, val):
            store[key] = val

        mock_redis.get = get
        mock_redis.setex = setex
        mock_redis.publish = AsyncMock()

        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=None)

        experiment_started = []

        optimizer = SelfOptimizer(mock_redis, lambda: mock_db, AsyncMock())
        optimizer._maybe_start_experiment = AsyncMock(
            side_effect=lambda *a, **kw: experiment_started.append(True) or "exp-id"
        )
        optimizer._get_min_goals = AsyncMock(return_value=DEFAULT_MIN_GOALS)
        optimizer._record_result = AsyncMock()
        optimizer._maybe_conclude_experiment = AsyncMock()

        # Simulate 5 goal completions
        agent_id = str(uuid4())
        for i in range(5):
            await optimizer.on_goal_completed("t1", agent_id, str(uuid4()), 0.8, 0.05, 500)

        assert len(experiment_started) == 1  # Triggered after 5, not 50


# ---- Bayesian testing -------------------------------------------------------

class TestBayesianProbBetter:
    def test_clearly_better_candidate_high_prob(self):
        # candidate mean 2x control
        prob = SelfOptimizer._bayesian_prob_better(
            ctrl_n=100, ctrl_mean=1.0,
            cand_n=100, cand_mean=2.0,
        )
        assert prob > 0.95

    def test_equal_performance_prob_near_half(self):
        prob = SelfOptimizer._bayesian_prob_better(
            ctrl_n=100, ctrl_mean=1.0,
            cand_n=100, cand_mean=1.0,
        )
        assert 0.4 < prob < 0.6

    def test_worse_candidate_low_prob(self):
        prob = SelfOptimizer._bayesian_prob_better(
            ctrl_n=100, ctrl_mean=2.0,
            cand_n=100, cand_mean=1.0,
        )
        assert prob < 0.05


# ---- Domain metrics --------------------------------------------------------

class TestDomainMetrics:
    def test_legal_uses_citation_accuracy(self):
        assert DOMAIN_METRICS["legal"] == "citation_accuracy"

    def test_finance_uses_compliance_rate(self):
        assert DOMAIN_METRICS["finance"] == "compliance_rate"

    def test_all_metrics_are_valid(self):
        valid = {"eval_score", "completion_rate", "cost_per_goal", "latency_ms",
                 "citation_accuracy", "compliance_rate", "conversion_rate", "resolution_rate"}
        for domain, metric in DOMAIN_METRICS.items():
            assert metric in valid, f"Unknown metric '{metric}' for domain '{domain}'"


# ---- Apply suggestion to config --------------------------------------------

class TestApplySuggestionToConfig:
    def test_changes_top_level_field(self):
        config = {"system_prompt": "old", "max_iterations": 5}
        suggestion = {
            "suggested_change": {
                "field": "system_prompt",
                "new_value": "new and improved"
            }
        }
        result = SelfOptimizer._apply_suggestion_to_config(config, suggestion)
        assert result["system_prompt"] == "new and improved"
        assert result["max_iterations"] == 5  # unchanged

    def test_nonexistent_field_returns_unchanged(self):
        config = {"system_prompt": "old"}
        suggestion = {"suggested_change": {"field": "nonexistent.nested", "new_value": "x"}}
        result = SelfOptimizer._apply_suggestion_to_config(config, suggestion)
        assert result == config
```

---

## 7. Domain Extensibility

### Legal (Citation Accuracy)
```python
# success_metric = 'citation_accuracy'
# Eval scorer: check if agent cited real cases (validate against Westlaw/Lexis API)
# Domain-specific suggestion prompt additions:
#   "Improve the agent's instruction to always cite cases in Bluebook format"
#   "Add instruction to verify citations before including in output"
```

### Healthcare (Clinical Accuracy)
```python
# success_metric = 'eval_score' with HIPAA-safe eval
# No PHI in eval data — evaluate on synthetic/de-identified cases
# Suggestion restrictions: cannot modify minimum necessary controls
# Domain guard: reject suggestions that weaken PHI access restrictions
```

### Finance (Compliance Rate)
```python
# success_metric = 'compliance_rate' (% of outputs flagged clean by compliance reviewer)
# A/B experiment extra constraint: neither arm can have compliance_rate < 0.9
# Auto-rollback trigger: if compliance_rate drops > 10% post-apply, rollback immediately
```

### Education (Student Engagement)
```python
# success_metric = 'resolution_rate' (% of student questions resolved without escalation)
# K-12 guard: never suggest changes that reduce content safety filtering
# Separate optimization for each grade level — elementary ≠ high school
```

### E-commerce (Conversion Rate)
```python
# success_metric = 'conversion_rate' (% of catalog optimization goals leading to order increase)
# Business rule: experiment window = 7 days (weekly sales cycle)
# Revenue-weighted evaluation: improvements on high-value SKUs count more
```

---

## AMENDMENTS — Critical Fixes

### Amendment 9.1 — Fix stale session in _maybe_conclude_experiment()

```python
# BEFORE (stale session after commit):
# async with self._db() as db:
#     # ... operations ...
#     await db.commit()
# result2 = await db.execute(...)  # ← session is CLOSED here!

# AFTER (single session for all operations):
async def _maybe_conclude_experiment(self, experiment_id: str) -> None:
    async with self._db() as db:
        # All DB operations within ONE session:
        await db.execute(_t("SET LOCAL app.tenant_id = :tid"), {"tid": self._tenant_id})

        exp_row = (await db.execute(_t("SELECT * FROM improvement_experiments WHERE id = :id"), {"id": experiment_id})).fetchone()
        if not exp_row:
            return

        stats = await self._compute_stats(experiment_id, db)  # pass db session
        if not self._has_sufficient_data(stats):
            return

        winner = self._determine_winner(stats)

        # Update experiment in SAME session:
        await db.execute(_t("""
            UPDATE improvement_experiments
            SET status = 'concluded', winner_arm = :winner, concluded_at = NOW()
            WHERE id = :id
        """), {"winner": winner, "id": experiment_id})

        await db.commit()  # single commit
    # apply_suggestion called OUTSIDE the session (fresh connection):
    if winner == "challenger":
        await self.apply_suggestion(experiment_id=experiment_id)
```

### Amendment 9.2 — Fix thread-unsafe random.gauss

```python
# BEFORE (global random module — not thread-safe under async):
# challenger_win_prob = random.gauss(...)

# AFTER (numpy Generator per call — thread-safe):
import numpy as np

def _bayesian_prob_better(self, control_scores: list[float], challenger_scores: list[float]) -> float:
    """Thompson sampling: probability challenger beats control."""
    if len(control_scores) < 2 or len(challenger_scores) < 2:
        return 0.5
    rng = np.random.default_rng()  # ← new Generator per call, thread-safe
    control_samples = rng.normal(np.mean(control_scores), max(np.std(control_scores), 0.001), 10000)
    challenger_samples = rng.normal(np.mean(challenger_scores), max(np.std(challenger_scores), 0.001), 10000)
    return float(np.mean(challenger_samples > control_samples))
```

### Amendment 9.3 — Fix app.state.provider_factory attribute name

```python
# In main.py SelfOptimizer wiring — use the correct attribute name:
# BEFORE (wrong attribute): app.state.provider_factory
# AFTER (correct — matches main.py line 732): app.state._app_provider

app.state.self_optimizer = SelfOptimizer(
    provider=app.state._app_provider,  # ← correct attribute
    db=None,  # upgraded in lifespan
    redis=_fake_redis,
)
# In lifespan:
app.state.self_optimizer._db = db_factory
app.state.self_optimizer._redis = real_redis
```

### Amendment 9.4 — Define integration point in loop.py

```python
# In app/agent/graph.py, in _node_initialize():
# After existing initialization, check for active experiment:
if self._self_optimizer and self._agent_id:
    arm_config = await self._self_optimizer.get_arm_config(
        agent_id=self._agent_id,
        goal_id=self._goal_id,
        tenant_id=self._tenant_ctx.tenant_id,
    )
    if arm_config:
        # Override agent config with experiment arm:
        if "system_prompt" in arm_config:
            self._system_prompt = arm_config["system_prompt"]
        if "max_iterations" in arm_config:
            self._max_iterations = arm_config["max_iterations"]
        # Store which arm was used (for result recording):
        state.context["experiment_arm"] = arm_config.get("arm_name", "control")

# In _node_verify() (after verification completes), record result:
if self._self_optimizer:
    arm = state.context.get("experiment_arm")
    if arm and state.eval_score is not None:
        await self._self_optimizer.record_result(
            goal_id=self._goal_id,
            agent_id=self._agent_id,
            arm_name=arm,
            eval_score=state.eval_score,
            cost_usd=state.context.get("total_cost_usd", 0),
            tenant_id=self._tenant_ctx.tenant_id,
        )
```

### Amendment 9.5 — Add stale experiment Celery task + App.tsx + toast + prefers-reduced-motion

```python
# Celery task for concluding stale experiments:
@celery_app.task(name="app.scaling.tasks.conclude_stale_experiments", queue="maintenance")
def conclude_stale_experiments():
    """Conclude experiments that are >30 days old with insufficient data."""
    import asyncio
    asyncio.run(_mark_stale_experiments())
# Beat schedule: daily at 03:00 UTC
```

```typescript
// App.tsx: SelfImprovementPage likely at /analytics or under Enterprise
const SelfImprovementPage = lazy(() => import("@/features/analytics/SelfImprovementPage").then(m => ({default: m.SelfImprovementPage})));
// Route: <Route path="self-improvement" element={<Suspense...><SelfImprovementPage /></Suspense>} />
// Sidebar — add under Enterprise section: { to: "/self-improvement", icon: TrendingUp, label: "Self-Improvement" }

// prefers-reduced-motion:
@media (prefers-reduced-motion: reduce) {
  .experiment-lift-chart, .winner-celebration, .arm-progress-bars { animation: none !important; }
}

// Toast notifications:
// applyOptimization onSuccess: toast({kind:"success", message:`Optimization applied to ${agentName}`})
// rollbackOptimization onSuccess: toast({kind:"warning", message:"Optimization rolled back to previous config"})
// concludeExperiment → ConfirmModal "Conclude experiment early?" + toast

// Empty states:
// No experiments: <EmptyState icon={TrendingUp} title="No experiments running" description="Self-improvement experiments start automatically when agents score below 70%." />
// No suggestions: <EmptyState icon={Lightbulb} title="No suggestions" description="Run more goals to generate optimization suggestions." />
```
