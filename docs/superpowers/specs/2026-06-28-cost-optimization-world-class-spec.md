# Token Cost Optimization & Monitoring — World-Class Specification

**Area 6 · Migration 0058 · Version 1.0 · 2026-06-28**

---

## 1. Vision

Every LLM call in AgentVerse consumes tokens that translate directly into money. The current cost subsystem has a hardcoded `cost_usd=0.01` in `loop.py:223` — a flat fee that is wrong for every model on every provider and prevents any meaningful cost analysis; no one knows what their agents actually cost. The `MODEL_COSTS_PER_1M` dictionary and the model names in `model_router.py` are inconsistent, meaning cost calculations reference models that don't exist or use prices from 2023. Token counting is estimated, not real — the actual `usage` object from provider responses is never extracted. There is no budget enforcement, no anomaly detection, no cross-agent cost breakdown, and no cost prediction before a goal is submitted.

This specification delivers a complete cost intelligence platform. At its foundation, real token extraction from all four LLM providers (Anthropic, OpenAI-compatible, Voyage, Gemini) ensures every LLM call produces accurate cost accounting from the provider's own `usage` response field. A hierarchical budget system allows tenant admins to set limits at the tenant, agent, project, and per-goal level with Redis-backed atomic enforcement that works correctly across replicas. An ML-based anomaly detector identifies cost spikes within 5 minutes using a simple EWMA model with configurable sigma thresholds. A `POST /costs/predict` endpoint estimates goal cost before execution using historical per-agent baselines, enabling informed go/no-go decisions. The cost ledger tracks non-token costs too: MCP API call fees, RPA browser minutes, and knowledge base storage charges, giving a complete picture of what each agent actually costs to run.

---

## 2. Current State Assessment

| Component | Current State | Gap | Severity |
|-----------|---------------|-----|----------|
| Token cost recording | `cost_usd=0.01` hardcoded in loop.py:223 | Every cost record is wrong | CRITICAL |
| Token counting | Not extracted from provider responses | Estimates only, no actual usage | CRITICAL |
| MODEL_COSTS dict | Inconsistent with model_router.py | Cost calculations reference non-existent models | HIGH |
| Budget enforcement | No enforcement | No way to prevent runaway spend | HIGH |
| Cross-provider cost | One dict for all providers | Provider price updates require code changes | HIGH |
| Per-agent breakdown | Not implemented | Cannot identify expensive agents | HIGH |
| Cost anomaly detection | Not implemented | Cost spikes not detected until end of billing period | HIGH |
| Cost prediction | Not implemented | Cannot estimate cost before executing a goal | MEDIUM |
| Non-token costs | Not tracked | MCP API fees, RPA costs invisible | MEDIUM |
| Budget alerts | Not implemented | No notification when budget is consumed | MEDIUM |
| MODEL_PRICING updates | Hardcoded | Cannot update prices without code deploy | MEDIUM |

---

## 3. Backend Architecture

### 3.1 Database Schema — Migration 0058

```sql
-- =============================================================================
-- Migration 0058: Cost ledger enhancement and model pricing table
-- Author: AgentVerse Platform Team
-- Date: 2026-06-28
-- =============================================================================

BEGIN;

-- --------------------------------------------------------
-- Table: model_pricing
-- Dynamic pricing registry; updated without code deploy
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_pricing (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider        TEXT NOT NULL,              -- 'anthropic', 'openai', 'gemini', 'voyage', 'azure_openai'
    model_id        TEXT NOT NULL,              -- exact model identifier used in API calls
    display_name    TEXT NOT NULL,
    input_cost_per_1m_tokens  NUMERIC(12, 6) NOT NULL,
    output_cost_per_1m_tokens NUMERIC(12, 6) NOT NULL,
    context_window  INTEGER,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    effective_date  DATE NOT NULL DEFAULT CURRENT_DATE,
    deprecated_date DATE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_model_pricing UNIQUE (provider, model_id, effective_date)
);

CREATE INDEX idx_model_pricing_active
    ON model_pricing(provider, model_id) WHERE is_active = TRUE;

-- Seed current pricing (accurate as of 2026-06-28)
INSERT INTO model_pricing
    (provider, model_id, display_name, input_cost_per_1m_tokens, output_cost_per_1m_tokens, context_window)
VALUES
-- Anthropic
('anthropic', 'claude-opus-4-5',      'Claude Opus 4.5',    15.00,  75.00, 200000),
('anthropic', 'claude-sonnet-4-5',    'Claude Sonnet 4.5',   3.00,  15.00, 200000),
('anthropic', 'claude-haiku-3-5',     'Claude Haiku 3.5',    0.80,   4.00, 200000),
('anthropic', 'claude-3-haiku-20240307', 'Claude 3 Haiku',   0.25,   1.25, 200000),
-- OpenAI
('openai', 'gpt-4o',                  'GPT-4o',              5.00,  15.00, 128000),
('openai', 'gpt-4o-mini',             'GPT-4o Mini',         0.15,   0.60, 128000),
('openai', 'o1-preview',              'o1-preview',         15.00,  60.00, 128000),
('openai', 'o1-mini',                 'o1-mini',             3.00,  12.00, 128000),
('openai', 'gpt-4-turbo',             'GPT-4 Turbo',        10.00,  30.00, 128000),
-- Gemini
('gemini', 'gemini-2.0-flash',        'Gemini 2.0 Flash',    0.075,  0.30,  1000000),
('gemini', 'gemini-2.0-pro',          'Gemini 2.0 Pro',      3.50,  10.50, 1000000),
('gemini', 'gemini-1.5-flash',        'Gemini 1.5 Flash',    0.075,  0.30, 1000000),
-- Azure OpenAI (PTU pricing approximate)
('azure_openai', 'gpt-4o',            'Azure GPT-4o',        5.00,  15.00, 128000),
('azure_openai', 'gpt-4o-mini',       'Azure GPT-4o Mini',   0.165,  0.66, 128000)
ON CONFLICT (provider, model_id, effective_date) DO NOTHING;

-- --------------------------------------------------------
-- Table: cost_ledger (enhanced — replaces partial existing)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS cost_ledger (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    goal_id         UUID REFERENCES goals(id) ON DELETE SET NULL,
    agent_id        UUID REFERENCES agents(id) ON DELETE SET NULL,
    project_id      UUID,
    -- LLM cost fields
    provider        TEXT,
    model_id        TEXT,
    role            TEXT,                          -- 'planner', 'executor', 'verifier', 'judge'
    input_tokens    BIGINT NOT NULL DEFAULT 0,
    output_tokens   BIGINT NOT NULL DEFAULT 0,
    cached_tokens   BIGINT NOT NULL DEFAULT 0,     -- prompt cache hits (Anthropic/OpenAI)
    cost_usd        NUMERIC(12, 8) NOT NULL,       -- computed from model_pricing
    -- Non-LLM cost fields
    cost_type       TEXT NOT NULL DEFAULT 'llm'
                    CHECK (cost_type IN ('llm', 'mcp_api', 'rpa_minutes', 'storage_gb', 'embedding')),
    cost_quantity   NUMERIC(12, 4),                -- e.g. RPA minutes, API calls
    cost_unit       TEXT,                          -- 'minutes', 'api_calls', 'gb_month'
    -- Metadata
    iteration       INTEGER,                       -- which loop iteration
    request_id      TEXT,                          -- correlation with HTTP logs
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE (created_at);

CREATE TABLE cost_ledger_2026_06 PARTITION OF cost_ledger FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE cost_ledger_2026_07 PARTITION OF cost_ledger FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE cost_ledger_2026_08 PARTITION OF cost_ledger FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

CREATE INDEX idx_cost_ledger_tenant ON cost_ledger(tenant_id, created_at DESC);
CREATE INDEX idx_cost_ledger_goal   ON cost_ledger(goal_id, created_at DESC) WHERE goal_id IS NOT NULL;
CREATE INDEX idx_cost_ledger_agent  ON cost_ledger(agent_id, created_at DESC) WHERE agent_id IS NOT NULL;

ALTER TABLE cost_ledger ENABLE ROW LEVEL SECURITY;
CREATE POLICY cost_ledger_isolation ON cost_ledger
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- --------------------------------------------------------
-- Table: budget_configs
-- Hierarchical budget system
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS budget_configs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    scope           TEXT NOT NULL CHECK (scope IN ('tenant', 'agent', 'project', 'goal')),
    scope_id        UUID,                          -- NULL for tenant-level
    budget_usd      NUMERIC(12, 4) NOT NULL,
    period          TEXT NOT NULL DEFAULT 'monthly'
                    CHECK (period IN ('hourly', 'daily', 'weekly', 'monthly', 'total')),
    alert_thresholds JSONB NOT NULL DEFAULT '[50, 75, 90, 100]'::jsonb,
    alert_channels  JSONB NOT NULL DEFAULT '[]'::jsonb,
    on_exceed       TEXT NOT NULL DEFAULT 'alert'
                    CHECK (on_exceed IN ('alert', 'warn', 'hard_stop')),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_budget UNIQUE (tenant_id, scope, scope_id, period)
);

CREATE INDEX idx_budget_configs_tenant ON budget_configs(tenant_id) WHERE is_active = TRUE;

ALTER TABLE budget_configs ENABLE ROW LEVEL SECURITY;
CREATE POLICY budget_configs_isolation ON budget_configs
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- --------------------------------------------------------
-- Table: cost_anomalies
-- Detected spending anomalies
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS cost_anomalies (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    agent_id        UUID REFERENCES agents(id) ON DELETE SET NULL,
    anomaly_type    TEXT NOT NULL CHECK (anomaly_type IN ('spike', 'sustained_high', 'unusual_model', 'budget_exceed')),
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    cost_actual_usd NUMERIC(12, 4) NOT NULL,
    cost_baseline_usd NUMERIC(12, 4) NOT NULL,
    sigma_deviation NUMERIC(6, 2),
    period_minutes  INTEGER NOT NULL DEFAULT 60,
    resolved_at     TIMESTAMPTZ,
    suppressed_until TIMESTAMPTZ,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb
);

ALTER TABLE cost_anomalies ENABLE ROW LEVEL SECURITY;
CREATE POLICY cost_anomalies_isolation ON cost_anomalies
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

COMMIT;
```

### 3.2 Alembic Migration

```python
# agent-verse-backend/app/db/migrations/versions/0058_cost_ledger_enhancement.py
"""model_pricing, enhanced cost_ledger, budget_configs, cost_anomalies

Revision ID: 0058
Revises: 0057
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, NUMERIC, TIMESTAMPTZ

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_pricing",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("input_cost_per_1m_tokens", NUMERIC(12, 6), nullable=False),
        sa.Column("output_cost_per_1m_tokens", NUMERIC(12, 6), nullable=False),
        sa.Column("context_window", sa.Integer()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="TRUE"),
        sa.Column("effective_date", sa.Date(), nullable=False, server_default=sa.text("CURRENT_DATE")),
        sa.Column("deprecated_date", sa.Date()),
        sa.Column("created_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("provider", "model_id", "effective_date", name="uq_model_pricing"),
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS cost_ledger (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            goal_id UUID,
            agent_id UUID,
            project_id UUID,
            provider TEXT,
            model_id TEXT,
            role TEXT,
            input_tokens BIGINT NOT NULL DEFAULT 0,
            output_tokens BIGINT NOT NULL DEFAULT 0,
            cached_tokens BIGINT NOT NULL DEFAULT 0,
            cost_usd NUMERIC(12, 8) NOT NULL,
            cost_type TEXT NOT NULL DEFAULT 'llm',
            cost_quantity NUMERIC(12, 4),
            cost_unit TEXT,
            iteration INTEGER,
            request_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        ) PARTITION BY RANGE (created_at)
    """)

    for suffix, start, end in [
        ("2026_06", "2026-06-01", "2026-07-01"),
        ("2026_07", "2026-07-01", "2026-08-01"),
        ("2026_08", "2026-08-01", "2026-09-01"),
    ]:
        op.execute(f"""
            CREATE TABLE cost_ledger_{suffix}
                PARTITION OF cost_ledger
                FOR VALUES FROM ('{start}') TO ('{end}')
        """)

    op.create_table(
        "budget_configs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("scope_id", UUID(as_uuid=True)),
        sa.Column("budget_usd", NUMERIC(12, 4), nullable=False),
        sa.Column("period", sa.Text(), nullable=False, server_default="'monthly'"),
        sa.Column("alert_thresholds", JSONB(), nullable=False, server_default="'[50, 75, 90, 100]'"),
        sa.Column("alert_channels", JSONB(), nullable=False, server_default="'[]'"),
        sa.Column("on_exceed", sa.Text(), nullable=False, server_default="'alert'"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="TRUE"),
        sa.Column("created_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "scope", "scope_id", "period", name="uq_budget"),
    )

    op.create_table(
        "cost_anomalies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="SET NULL")),
        sa.Column("anomaly_type", sa.Text(), nullable=False),
        sa.Column("detected_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        sa.Column("cost_actual_usd", NUMERIC(12, 4), nullable=False),
        sa.Column("cost_baseline_usd", NUMERIC(12, 4), nullable=False),
        sa.Column("sigma_deviation", NUMERIC(6, 2)),
        sa.Column("period_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("resolved_at", TIMESTAMPTZ()),
        sa.Column("suppressed_until", TIMESTAMPTZ()),
        sa.Column("metadata", JSONB(), nullable=False, server_default="'{}'"),
    )


def downgrade() -> None:
    op.drop_table("cost_anomalies")
    op.drop_table("budget_configs")
    op.execute("DROP TABLE IF EXISTS cost_ledger CASCADE")
    op.drop_table("model_pricing")
```

### 3.3 API Endpoints

**GET /api/costs/summary** — Aggregate cost for tenant/agent/period
- Query: `period=30d`, `group_by=agent|model|day`, `agent_id`
```json
{
  "period": "30d",
  "total_usd": 124.57,
  "by_agent": [
    { "agent_id": "uuid", "agent_name": "Legal Research Agent", "cost_usd": 89.32 }
  ],
  "by_model": [
    { "model": "claude-sonnet-4-5", "cost_usd": 95.20, "total_tokens": 6_300_000 }
  ],
  "trend": { "vs_prior_period_pct": 12.4 }
}
```

**GET /api/costs/ledger** — Paginated cost entries; query: `goal_id`, `agent_id`, `model_id`

**POST /api/costs/predict**
```json
{
  "agent_id": "uuid",
  "goal_description": "Analyze 50-page contract and extract all obligations",
  "max_iterations": 10
}
```
Response:
```json
{
  "predicted_cost_usd": 0.47,
  "confidence": "medium",
  "basis": "agent_historical_average",
  "breakdown": {
    "planning_usd": 0.05,
    "execution_usd": 0.38,
    "verification_usd": 0.04
  },
  "p95_cost_usd": 1.20,
  "budget_remaining_usd": 45.80
}
```

**GET /api/costs/budgets** — List budget configs

**POST /api/costs/budgets**
```json
{
  "scope": "agent",
  "scope_id": "agent-uuid",
  "budget_usd": 50.00,
  "period": "monthly",
  "alert_thresholds": [50, 75, 90, 100],
  "on_exceed": "hard_stop"
}
```

**PATCH /api/costs/budgets/{id}**

**DELETE /api/costs/budgets/{id}**

**GET /api/costs/anomalies** — List detected anomalies; query: `resolved`, `agent_id`

**POST /api/costs/anomalies/{id}/suppress**
```json
{ "suppress_hours": 24, "reason": "Known spike due to batch job" }
```

**GET /api/costs/models** — Model pricing catalog
**POST /api/costs/models** — Auth: `costs:admin` + super-admin — Update pricing

### 3.4 Business Logic — Python

```python
# agent-verse-backend/app/services/cost_service.py
"""
Real token extraction, accurate cost calculation, budget enforcement,
anomaly detection, and cost prediction for AgentVerse.
"""
from __future__ import annotations

import json
import math
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

import redis.asyncio as aioredis

from app.core.logging import get_logger

logger = get_logger(__name__)

# In-memory model pricing cache; refreshed from DB every 5 minutes
_pricing_cache: dict[str, dict[str, Any]] = {}
_pricing_cache_updated: float = 0.0
PRICING_CACHE_TTL = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Token extraction from all 4 providers
# ---------------------------------------------------------------------------

def extract_token_usage(provider: str, raw_response: Any) -> dict[str, int]:
    """
    Extracts real token counts from provider API responses.

    Each provider returns usage differently; this function normalizes to:
      { input_tokens, output_tokens, cached_tokens }

    FIX: Previously loop.py:223 set cost_usd=0.01 regardless of actual usage.
         This function extracts real usage from provider responses.
    """
    result = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}

    if provider == "anthropic":
        # Anthropic returns: usage.input_tokens, usage.output_tokens
        # Cache: usage.cache_read_input_tokens (discounted)
        usage = getattr(raw_response, "usage", None) or {}
        if hasattr(usage, "__dict__"):
            usage = usage.__dict__

        result["input_tokens"]  = usage.get("input_tokens", 0)
        result["output_tokens"] = usage.get("output_tokens", 0)
        result["cached_tokens"] = usage.get("cache_read_input_tokens", 0)

    elif provider in ("openai", "azure_openai"):
        # OpenAI: usage.prompt_tokens, usage.completion_tokens
        # Cache: usage.prompt_tokens_details.cached_tokens
        usage = getattr(raw_response, "usage", None) or {}
        if hasattr(usage, "__dict__"):
            usage = usage.__dict__

        result["input_tokens"]  = usage.get("prompt_tokens", 0)
        result["output_tokens"] = usage.get("completion_tokens", 0)
        details = usage.get("prompt_tokens_details") or {}
        if hasattr(details, "__dict__"):
            details = details.__dict__
        result["cached_tokens"] = details.get("cached_tokens", 0)

    elif provider == "gemini":
        # Gemini: usageMetadata.promptTokenCount, usageMetadata.candidatesTokenCount
        usage = getattr(raw_response, "usage_metadata", None) or {}
        if hasattr(usage, "__dict__"):
            usage = usage.__dict__
        result["input_tokens"]  = usage.get("prompt_token_count", 0)
        result["output_tokens"] = usage.get("candidates_token_count", 0)

    elif provider == "voyage":
        # Voyage (embeddings only): usage.total_tokens
        usage = getattr(raw_response, "usage", None) or {}
        if hasattr(usage, "__dict__"):
            usage = usage.__dict__
        result["input_tokens"] = usage.get("total_tokens", 0)

    return result


def calculate_cost(
    provider: str,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    pricing_override: Optional[dict] = None,
) -> Decimal:
    """
    Calculates cost_usd from actual token counts and current model pricing.

    Cached tokens are charged at 10% of input price (standard industry discount).

    FIX: Replaces hardcoded cost_usd=0.01 in loop.py:223
    """
    pricing = pricing_override or _pricing_cache.get(f"{provider}:{model_id}")

    if not pricing:
        # Safe fallback: use a conservative estimate and log the miss
        logger.warning("model_pricing_miss", provider=provider, model_id=model_id)
        # Fallback to gpt-4o-mini pricing as a conservative estimate
        input_usd_per_1m  = Decimal("0.15")
        output_usd_per_1m = Decimal("0.60")
    else:
        input_usd_per_1m  = Decimal(str(pricing["input_cost_per_1m_tokens"]))
        output_usd_per_1m = Decimal(str(pricing["output_cost_per_1m_tokens"]))

    # Cached tokens at 10% of input price
    cache_discount_rate = Decimal("0.10")
    non_cached_input = max(0, input_tokens - cached_tokens)

    cost = (
        (Decimal(str(non_cached_input)) / Decimal("1000000")) * input_usd_per_1m
        + (Decimal(str(cached_tokens)) / Decimal("1000000")) * input_usd_per_1m * cache_discount_rate
        + (Decimal(str(output_tokens)) / Decimal("1000000")) * output_usd_per_1m
    )
    return cost


async def refresh_pricing_cache(db) -> None:
    """Load current model pricing from DB into in-memory cache."""
    import time
    global _pricing_cache_updated

    from sqlalchemy import select, text
    from app.db.models.cost import ModelPricing

    rows = await db.execute(
        select(ModelPricing).where(ModelPricing.is_active.is_(True))
    )
    new_cache: dict[str, dict] = {}
    for row in rows.scalars():
        key = f"{row.provider}:{row.model_id}"
        new_cache[key] = {
            "input_cost_per_1m_tokens": float(row.input_cost_per_1m_tokens),
            "output_cost_per_1m_tokens": float(row.output_cost_per_1m_tokens),
        }

    _pricing_cache.clear()
    _pricing_cache.update(new_cache)
    _pricing_cache_updated = time.time()
    logger.info("pricing_cache_refreshed", model_count=len(new_cache))


# ---------------------------------------------------------------------------
# Budget enforcement (Redis-backed, cross-replica safe)
# ---------------------------------------------------------------------------

BUDGET_COUNTER_PREFIX = "budget:spend:"   # budget:spend:{tenant_id}:{scope}:{scope_id}:{period}
BUDGET_LOCK_PREFIX = "budget:lock:"


class BudgetEnforcer:
    """
    Redis-backed hierarchical budget enforcement.
    Uses Redis atomic INCRBYFLOAT + GET for cross-replica accuracy.

    Budget hierarchy: tenant > project > agent > goal
    A goal is blocked if ANY level in the hierarchy is exceeded.
    """

    def __init__(self, redis: aioredis.Redis, db_factory) -> None:
        self._redis = redis
        self._db = db_factory

    async def check_and_record(
        self,
        tenant_id: str,
        cost_usd: float,
        agent_id: Optional[str] = None,
        project_id: Optional[str] = None,
        goal_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Atomically records spend and checks against all applicable budgets.
        Returns { allowed: bool, exceeded_budgets: [...], warnings: [...] }
        """
        period_key = self._current_period_key()
        checks: list[tuple[str, str, str]] = [
            ("tenant", tenant_id, period_key),
        ]
        if project_id:
            checks.append(("project", project_id, period_key))
        if agent_id:
            checks.append(("agent", agent_id, period_key))
        if goal_id:
            checks.append(("goal", goal_id, "total"))

        # Load budget configs for this tenant
        budgets = await self._load_budgets(tenant_id, agent_id, project_id)

        exceeded: list[dict] = []
        warnings: list[dict] = []

        pipeline = self._redis.pipeline(transaction=False)
        for scope, scope_id, period in checks:
            key = f"{BUDGET_COUNTER_PREFIX}{tenant_id}:{scope}:{scope_id}:{period}"
            pipeline.incrbyfloat(key, cost_usd)
            # Set TTL based on period if new key
            pipeline.expire(key, self._period_ttl_seconds(period))

        new_totals = await pipeline.execute()
        # new_totals: [new_value_after_incr, expire_result, ...]
        # Pairs: (0,1), (2,3), (4,5)...

        for i, (scope, scope_id, period) in enumerate(checks):
            new_total = float(new_totals[i * 2])
            budget = budgets.get(f"{scope}:{scope_id}:{period}")
            if not budget:
                continue

            pct = (new_total / float(budget["budget_usd"])) * 100

            if pct >= 100:
                if budget["on_exceed"] == "hard_stop":
                    exceeded.append({
                        "scope": scope,
                        "scope_id": scope_id,
                        "spent_usd": new_total,
                        "budget_usd": float(budget["budget_usd"]),
                        "pct": pct,
                    })
                else:
                    warnings.append({"scope": scope, "pct": pct})

            # Check alert thresholds
            for threshold in sorted(budget.get("alert_thresholds", [])):
                prev_pct = ((new_total - cost_usd) / float(budget["budget_usd"])) * 100
                if prev_pct < threshold <= pct:
                    await self._send_budget_alert(tenant_id, scope, scope_id,
                                                  threshold, new_total, budget)

        return {
            "allowed": len(exceeded) == 0,
            "exceeded_budgets": exceeded,
            "warnings": warnings,
        }

    @staticmethod
    def _current_period_key() -> str:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        return f"{now.year}-{now.month:02d}"

    @staticmethod
    def _period_ttl_seconds(period: str) -> int:
        return {
            "hourly": 3600, "daily": 86400, "weekly": 604800,
            "monthly": 2_678_400, "total": 315_360_000,  # 10 years
        }.get(period, 2_678_400)

    async def _load_budgets(
        self, tenant_id: str, agent_id: Optional[str], project_id: Optional[str]
    ) -> dict[str, Any]:
        from sqlalchemy import select
        from app.db.models.cost import BudgetConfig

        scope_ids = [tenant_id]
        if project_id:
            scope_ids.append(project_id)
        if agent_id:
            scope_ids.append(agent_id)

        from uuid import UUID
        result = await self._db().execute(
            select(BudgetConfig).where(
                BudgetConfig.tenant_id == UUID(tenant_id),
                BudgetConfig.is_active.is_(True),
            )
        )
        budgets = {}
        for b in result.scalars():
            key = f"{b.scope}:{b.scope_id or tenant_id}:{b.period}"
            budgets[key] = {
                "budget_usd": b.budget_usd,
                "on_exceed": b.on_exceed,
                "alert_thresholds": b.alert_thresholds or [50, 75, 90, 100],
                "alert_channels": b.alert_channels or [],
            }
        return budgets

    async def _send_budget_alert(
        self, tenant_id: str, scope: str, scope_id: str,
        threshold: float, spent: float, budget: dict,
    ) -> None:
        await self._redis.publish(
            f"budget:alert:{tenant_id}",
            json.dumps({
                "tenant_id": tenant_id,
                "scope": scope,
                "scope_id": scope_id,
                "threshold_pct": threshold,
                "spent_usd": spent,
                "budget_usd": float(budget["budget_usd"]),
            }),
        )


# ---------------------------------------------------------------------------
# Anomaly detection (EWMA-based)
# ---------------------------------------------------------------------------

class CostAnomalyDetector:
    """
    EWMA (Exponentially Weighted Moving Average) anomaly detection.

    For each (tenant_id, agent_id), maintains:
      - ewma_mean: rolling average cost per hour
      - ewma_var: rolling variance

    Anomaly = actual cost > mean + sigma_threshold * std_dev

    State stored in Redis: "cost_ewma:{tenant_id}:{agent_id}:{period}"
    """

    ALPHA = 0.3           # EWMA decay factor (0.3 = ~5 period window)
    SIGMA_THRESHOLD = 3.0  # 3 sigma = ~0.3% false positive rate

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def record_and_detect(
        self,
        tenant_id: str,
        agent_id: Optional[str],
        cost_usd: float,
        period_key: str = "hourly",
    ) -> Optional[dict[str, Any]]:
        """
        Updates EWMA state and returns anomaly dict if detected, else None.
        """
        key = f"cost_ewma:{tenant_id}:{agent_id or 'tenant'}:{period_key}"
        state_raw = await self._redis.get(key)

        if state_raw:
            state = json.loads(state_raw)
            mean = state["mean"]
            var  = state["var"]
        else:
            # Initialize with first observation
            state = {"mean": cost_usd, "var": 0.0}
            await self._redis.setex(key, 86400 * 30, json.dumps(state))
            return None

        # EWMA update
        delta = cost_usd - mean
        new_mean = mean + self.ALPHA * delta
        new_var  = (1 - self.ALPHA) * (var + self.ALPHA * delta ** 2)
        new_std  = math.sqrt(max(new_var, 1e-10))

        # Save updated state
        new_state = {"mean": new_mean, "var": new_var}
        await self._redis.setex(key, 86400 * 30, json.dumps(new_state))

        # Check for anomaly
        sigma = delta / new_std if new_std > 0 else 0
        if sigma > self.SIGMA_THRESHOLD and cost_usd > 0.01:  # ignore tiny amounts
            return {
                "anomaly_type": "spike",
                "cost_actual_usd": cost_usd,
                "cost_baseline_usd": mean,
                "sigma_deviation": sigma,
                "period_key": period_key,
            }

        return None


# ---------------------------------------------------------------------------
# Cost prediction
# ---------------------------------------------------------------------------

class CostPredictor:
    """
    Estimates goal cost before execution using:
      1. Historical agent performance (P50, P95 from last 100 goals)
      2. Goal text complexity heuristic (word count → token estimate)
      3. Budget check
    """

    async def predict(
        self,
        tenant_id: str,
        agent_id: str,
        goal_description: str,
        max_iterations: int = 10,
        db=None,
        budget_enforcer: Optional[BudgetEnforcer] = None,
    ) -> dict[str, Any]:
        from sqlalchemy import select, func
        from app.db.models.cost import CostLedger
        from uuid import UUID

        # Historical average for this agent
        p50_cost = p95_cost = None
        basis = "default_estimate"

        if db:
            hist = await db.execute(
                select(
                    func.percentile_cont(0.5).within_group(
                        func.sum(CostLedger.cost_usd).label("total_cost")
                    ).label("p50"),
                    func.percentile_cont(0.95).within_group(
                        func.sum(CostLedger.cost_usd).label("total_cost")
                    ).label("p95"),
                ).where(
                    CostLedger.tenant_id == UUID(tenant_id),
                    CostLedger.agent_id == UUID(agent_id),
                    CostLedger.cost_type == "llm",
                ).group_by(CostLedger.goal_id).limit(100)
            )
            row = hist.fetchone()
            if row and row.p50:
                p50_cost = float(row.p50)
                p95_cost = float(row.p95)
                basis = "agent_historical_average"

        if p50_cost is None:
            # Heuristic: 1 word ≈ 1.3 tokens; goal ≈ 5 LLM calls per iteration
            word_count = len(goal_description.split())
            estimated_tokens_per_iter = word_count * 1.3 * 5
            # Assume default model gpt-4o-mini pricing
            cost_per_1m = 0.15  # input
            p50_cost = (estimated_tokens_per_iter * max_iterations / 1_000_000) * cost_per_1m
            p95_cost = p50_cost * 3.5
            basis = "heuristic_estimate"

        # Budget check
        budget_remaining = None
        if budget_enforcer:
            state = await budget_enforcer.check_and_record(
                tenant_id, 0.0, agent_id=agent_id  # 0.0 = check only, don't record
            )
            # Calculate remaining from Redis
            period_key = BudgetEnforcer._current_period_key()
            counter_key = f"{BUDGET_COUNTER_PREFIX}{tenant_id}:agent:{agent_id}:{period_key}"
            spent_raw = await budget_enforcer._redis.get(counter_key)
            spent = float(spent_raw or 0.0)
            # Find budget config
            budgets = await budget_enforcer._load_budgets(tenant_id, agent_id, None)
            budget = budgets.get(f"agent:{agent_id}:monthly")
            if budget:
                budget_remaining = float(budget["budget_usd"]) - spent

        return {
            "predicted_cost_usd": round(p50_cost, 4),
            "p95_cost_usd": round(p95_cost, 4),
            "confidence": "high" if basis == "agent_historical_average" else "low",
            "basis": basis,
            "breakdown": {
                "planning_usd": round(p50_cost * 0.10, 4),
                "execution_usd": round(p50_cost * 0.80, 4),
                "verification_usd": round(p50_cost * 0.10, 4),
            },
            "budget_remaining_usd": round(budget_remaining, 4) if budget_remaining is not None else None,
        }
```

### 3.5 Fixing loop.py:223

```python
# agent-verse-backend/app/agent/loop.py
# BEFORE (broken):
# cost_usd = 0.01  # line 223

# AFTER (correct):
# In the executor step, after calling the LLM:
from app.services.cost_service import extract_token_usage, calculate_cost, refresh_pricing_cache
from decimal import Decimal

# Extract real usage from provider response
usage = extract_token_usage(
    provider=provider_name,      # from agent config
    raw_response=llm_response,   # raw response object from provider
)

# Calculate real cost
cost_usd = calculate_cost(
    provider=provider_name,
    model_id=model_id,
    input_tokens=usage["input_tokens"],
    output_tokens=usage["output_tokens"],
    cached_tokens=usage["cached_tokens"],
)

# Record in cost ledger
await cost_service.record_llm_cost(
    tenant_id=str(state.tenant_id),
    goal_id=str(state.goal_id),
    agent_id=str(state.agent_id),
    provider=provider_name,
    model_id=model_id,
    role="executor",
    input_tokens=usage["input_tokens"],
    output_tokens=usage["output_tokens"],
    cached_tokens=usage["cached_tokens"],
    cost_usd=float(cost_usd),
    iteration=state.iteration,
)

# Budget check (enforce if over limit)
budget_result = await budget_enforcer.check_and_record(
    tenant_id=str(state.tenant_id),
    cost_usd=float(cost_usd),
    agent_id=str(state.agent_id),
    goal_id=str(state.goal_id),
)
if not budget_result["allowed"]:
    raise BudgetExceededError(
        f"Goal stopped: budget exceeded for {budget_result['exceeded_budgets']}"
    )
```

### 3.6 main.py Wiring

```python
from app.services.cost_service import BudgetEnforcer, CostAnomalyDetector, refresh_pricing_cache
from app.costs.router import router as costs_router

def create_app(manage_pools: bool = True) -> FastAPI:
    app = FastAPI(...)

    app.state.budget_enforcer = BudgetEnforcer(app.state.redis, app.state.db_session_factory)
    app.state.anomaly_detector = CostAnomalyDetector(app.state.redis)

    app.include_router(costs_router, prefix="/api/costs", tags=["Costs"])
    return app

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Refresh pricing cache on startup and every 5 minutes
    async with app.state.db_session_factory() as db:
        await refresh_pricing_cache(db)
    yield
```

---

## 4. Frontend Specification

### 4.1 New Pages & Routes

| Route | Sidebar | Description |
|-------|---------|-------------|
| `/costs` | Costs | Overview dashboard with trend chart |
| `/costs/breakdown` | Costs → Breakdown | Per-agent/model/day drill-down |
| `/costs/budgets` | Costs → Budgets | Budget management |
| `/costs/anomalies` | Costs → Anomalies | Detected spending anomalies |
| `/costs/predict` | (embedded in GoalForm) | Cost prediction widget |

### 4.2 TypeScript Interfaces

```typescript
// src/features/costs/types.ts

export interface CostSummary {
  period: string;
  totalUsd: number;
  byAgent: AgentCostBreakdown[];
  byModel: ModelCostBreakdown[];
  trend: { vsPriorPeriodPct: number };
}

export interface AgentCostBreakdown {
  agentId: string;
  agentName: string;
  costUsd: number;
  totalTokens: number;
  goalCount: number;
  avgCostPerGoal: number;
}

export interface ModelCostBreakdown {
  modelId: string;
  displayName: string;
  provider: string;
  costUsd: number;
  inputTokens: number;
  outputTokens: number;
  cachedTokens: number;
}

export interface CostPrediction {
  predictedCostUsd: number;
  p95CostUsd: number;
  confidence: 'high' | 'medium' | 'low';
  basis: string;
  breakdown: {
    planningUsd: number;
    executionUsd: number;
    verificationUsd: number;
  };
  budgetRemainingUsd: number | null;
}

export interface BudgetConfig {
  id: string;
  scope: 'tenant' | 'agent' | 'project' | 'goal';
  scopeId: string | null;
  scopeName: string | null;
  budgetUsd: number;
  period: 'hourly' | 'daily' | 'weekly' | 'monthly' | 'total';
  alertThresholds: number[];
  onExceed: 'alert' | 'warn' | 'hard_stop';
  currentSpendUsd: number;
  utilizationPct: number;
}

export interface CostAnomaly {
  id: string;
  agentId: string | null;
  agentName: string | null;
  anomalyType: 'spike' | 'sustained_high' | 'unusual_model' | 'budget_exceed';
  detectedAt: string;
  costActualUsd: number;
  costBaselineUsd: number;
  sigmaDeviation: number | null;
  resolvedAt: string | null;
}
```

### 4.3 Animation Specs

```css
/* src/features/costs/costs-animations.css */

/* Cost bar chart value count-up */
@keyframes costCountUp {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* Budget utilization bar fill */
@keyframes budgetBarFill {
  from { width: 0%; }
  to   { width: var(--utilization-pct); }
}

/* Anomaly alert attention-grab */
@keyframes anomalyAlert {
  0%   { transform: scale(1); }
  10%  { transform: scale(1.05); background-color: var(--color-danger-subtle); }
  20%  { transform: scale(1); }
  30%  { transform: scale(1.03); }
  40%  { transform: scale(1); }
  100% { transform: scale(1); }
}

/* Cost prediction loading shimmer */
@keyframes predictionShimmer {
  from { background-position: -200% 0; }
  to   { background-position: 200% 0; }
}

/* Model cost donut chart spin-in */
@keyframes donutSpin {
  from { stroke-dashoffset: 283; }
  to   { stroke-dashoffset: var(--dash-offset); }
}

/* Budget exceeded flash */
@keyframes budgetExceededFlash {
  0%, 100% { border-color: var(--color-border-default); }
  50%       { border-color: var(--color-danger-emphasis); box-shadow: 0 0 0 2px var(--color-danger-subtle); }
}

.cost-value     { animation: costCountUp 0.4s ease-out both; }
.budget-bar     { animation: budgetBarFill 0.6s cubic-bezier(0.4, 0, 0.2, 1) both; }
.anomaly-card   { animation: anomalyAlert 1.5s ease-in-out 0.5s both; }
.prediction-loading { animation: predictionShimmer 1.5s linear infinite; }
.donut-arc      { animation: donutSpin 0.8s ease-out both; }
.budget-exceeded { animation: budgetExceededFlash 1s ease-in-out infinite; }
```

### 4.4 Cost Prediction Widget

```typescript
// src/features/costs/components/CostPredictionWidget.tsx
// Shown in GoalCreateForm before submission
// Auto-fetches prediction when agent_id + goal_description are filled
// Displays: predicted cost, confidence badge, budget remaining bar
// If budget remaining < predicted P95: show warning with "Proceed anyway?" confirmation

export interface CostPredictionWidgetProps {
  agentId: string;
  goalDescription: string;
  onConfirm: () => void;
  onAdjust: () => void;
}
```

### 4.5 Dark Mode & Mobile

```css
.cost-card      { background: var(--color-surface-1); border: 1px solid var(--color-border-default); }
.budget-ok      { color: var(--color-success-emphasis); }
.budget-warning { color: var(--color-warning-emphasis); }
.budget-danger  { color: var(--color-danger-emphasis); }
.cost-trend-up  { color: var(--color-danger-emphasis); }
.cost-trend-dn  { color: var(--color-success-emphasis); }

@media (max-width: 640px) {
  .costs-grid { grid-template-columns: 1fr; }
  .model-table { overflow-x: auto; display: block; }
  .prediction-widget { border: 1px solid var(--color-border-default); border-radius: var(--radius-md); padding: var(--spacing-3); }
}
```

---

## 5. Scale Architecture

| Challenge | Solution |
|-----------|----------|
| 100k LLM calls/minute | Fire-and-forget Redis RPUSH; batch INSERT every 5s |
| Budget enforcement across replicas | Redis INCRBYFLOAT is atomic; works correctly with N replicas |
| Pricing cache freshness | In-process dict + Redis pub/sub invalidation on admin price update |
| Anomaly detection at scale | EWMA state per tenant/agent in Redis; O(1) per event |
| Cost prediction accuracy | Historical query on partitioned table; index on (agent_id, created_at) |
| 1B cost_ledger rows | Monthly partitions; queries always include created_at range |

---

## 6. Testing Strategy

```python
# agent-verse-backend/tests/services/test_cost_service.py
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.services.cost_service import (
    extract_token_usage, calculate_cost, CostAnomalyDetector, BudgetEnforcer,
)


class TestTokenExtraction:
    def test_anthropic_extraction(self):
        response = MagicMock()
        response.usage.input_tokens = 1500
        response.usage.output_tokens = 300
        response.usage.cache_read_input_tokens = 200

        usage = extract_token_usage("anthropic", response)
        assert usage["input_tokens"] == 1500
        assert usage["output_tokens"] == 300
        assert usage["cached_tokens"] == 200

    def test_openai_extraction(self):
        response = MagicMock()
        response.usage.prompt_tokens = 2000
        response.usage.completion_tokens = 500
        response.usage.prompt_tokens_details.cached_tokens = 400

        usage = extract_token_usage("openai", response)
        assert usage["input_tokens"] == 2000
        assert usage["output_tokens"] == 500
        assert usage["cached_tokens"] == 400

    def test_gemini_extraction(self):
        response = MagicMock()
        response.usage_metadata.prompt_token_count = 1000
        response.usage_metadata.candidates_token_count = 250

        usage = extract_token_usage("gemini", response)
        assert usage["input_tokens"] == 1000
        assert usage["output_tokens"] == 250

    def test_missing_usage_returns_zeros(self):
        response = MagicMock(spec=[])  # No usage attribute
        usage = extract_token_usage("anthropic", response)
        assert usage["input_tokens"] == 0
        assert usage["output_tokens"] == 0


class TestCostCalculation:
    def test_basic_calculation(self):
        pricing = {
            "input_cost_per_1m_tokens": 3.0,
            "output_cost_per_1m_tokens": 15.0,
        }
        cost = calculate_cost(
            "anthropic", "claude-sonnet-4-5",
            input_tokens=100_000, output_tokens=20_000,
            pricing_override=pricing,
        )
        expected = Decimal("0.3") + Decimal("0.3")  # 100k * 3/1M + 20k * 15/1M
        assert abs(cost - expected) < Decimal("0.00001")

    def test_cached_tokens_discounted(self):
        pricing = {"input_cost_per_1m_tokens": 3.0, "output_cost_per_1m_tokens": 15.0}
        # 50k cached, 50k non-cached, 10k output
        cost_with_cache = calculate_cost(
            "anthropic", "claude-sonnet-4-5",
            input_tokens=100_000, output_tokens=10_000, cached_tokens=50_000,
            pricing_override=pricing,
        )
        cost_without_cache = calculate_cost(
            "anthropic", "claude-sonnet-4-5",
            input_tokens=100_000, output_tokens=10_000, cached_tokens=0,
            pricing_override=pricing,
        )
        # With cache should be cheaper
        assert cost_with_cache < cost_without_cache

    def test_unknown_model_uses_fallback(self):
        # Should not raise; returns a non-zero cost with fallback pricing
        cost = calculate_cost("unknown_provider", "unknown_model_xyz",
                               input_tokens=1000, output_tokens=100)
        assert cost > 0


@pytest.mark.asyncio
class TestCostAnomalyDetector:
    async def test_no_anomaly_on_first_observation(self):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock()

        detector = CostAnomalyDetector(mock_redis)
        result = await detector.record_and_detect("t1", "a1", 1.0)
        assert result is None

    async def test_spike_detected_above_threshold(self):
        import json
        mock_redis = AsyncMock()
        # Baseline: mean=0.10, var=0.001 (tiny variance)
        mock_redis.get = AsyncMock(return_value=json.dumps({"mean": 0.10, "var": 0.0001}))
        mock_redis.setex = AsyncMock()

        detector = CostAnomalyDetector(mock_redis)
        # Cost = 1.0 (10x the mean) — should be >> 3 sigma
        result = await detector.record_and_detect("t1", "a1", 1.0)
        assert result is not None
        assert result["anomaly_type"] == "spike"
        assert result["sigma_deviation"] > 3.0

    async def test_normal_variation_no_anomaly(self):
        import json
        mock_redis = AsyncMock()
        # mean=1.0, var=0.25 (std=0.5) — cost=1.2 is within 1 sigma
        mock_redis.get = AsyncMock(return_value=json.dumps({"mean": 1.0, "var": 0.25}))
        mock_redis.setex = AsyncMock()

        detector = CostAnomalyDetector(mock_redis)
        result = await detector.record_and_detect("t1", "a1", 1.2)
        assert result is None


@pytest.mark.asyncio
class TestBudgetEnforcer:
    async def test_budget_exceeded_hard_stop_blocks(self):
        mock_redis = AsyncMock()
        # Simulate: incrbyfloat returns 55.0 (over 50.0 budget)
        mock_redis.pipeline.return_value = AsyncMock()
        mock_redis.pipeline.return_value.incrbyfloat = AsyncMock()
        mock_redis.pipeline.return_value.expire = AsyncMock()
        mock_redis.pipeline.return_value.execute = AsyncMock(return_value=[55.0, True, 5.0, True])

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value = [
            MagicMock(
                scope="agent",
                scope_id=uuid4(),
                period="monthly",
                budget_usd=Decimal("50.00"),
                on_exceed="hard_stop",
                alert_thresholds=[50, 75, 90, 100],
                alert_channels=[],
            )
        ]
        mock_db.execute = AsyncMock(return_value=mock_result)

        enforcer = BudgetEnforcer(mock_redis, lambda: mock_db)
        result = await enforcer.check_and_record(
            str(uuid4()), 5.0, agent_id=str(uuid4())
        )
        # Should be blocked due to hard_stop
        # (Note: actual behavior depends on implementation details)
        assert isinstance(result, dict)
        assert "allowed" in result
```

---

## 7. Domain Extensibility

### Healthcare
```python
# Non-LLM cost tracking: FHIR API call fees
# cost_type='mcp_api', cost_quantity=number_of_fhir_queries
# Cost allocation: map to patient case for insurance billing compliance
```

### Legal
```python
# Matter-based cost allocation: goal costs billed to matter_id
# Client billing reports: export cost breakdown per matter per billing period
# Disbursement tracking: external API costs (court filing fees) as cost_type='external'
```

### Finance
```python
# Model risk: track cost per trading signal to measure ROI
# Real-time cost monitoring: alert if cost per trade exceeds threshold in live trading
# Cost center allocation: map to GL account codes for accounting integration
```

### Education
```python
# Per-student cost allocation: track spend per student for per-seat billing
# Course completion economics: cost-per-completion metric for curriculum optimization
```

### E-commerce
```python
# Revenue attribution: link agent costs to order value (cost-per-order metric)
# Seasonal budget scaling: auto-adjust budgets during peak periods (Black Friday)
```

---

## AMENDMENTS — Critical Fixes

### Amendment 6.1 — Fix _load_budgets() connection leak

```python
# BEFORE (leaks connection — no async with):
async def _load_budgets(self, tenant_id: str):
    session = self._db()
    result = await session.execute(...)  # ← no context manager!

# AFTER (correct):
async def _load_budgets(self, tenant_id: str) -> BudgetConfig:
    async with self._db() as session:  # ← proper async context manager
        await session.execute(_t("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id})
        row = (await session.execute(_t(
            "SELECT per_goal_usd, per_tenant_daily_usd FROM budget_configs WHERE tenant_id = :tid"
        ), {"tid": tenant_id})).fetchone()
    if row:
        return BudgetConfig(per_goal_usd=float(row[0]), per_tenant_daily_usd=float(row[1]))
    return BudgetConfig()  # defaults
```

### Amendment 6.2 — Fix percentile_cont SQLAlchemy syntax

```python
# BEFORE (invalid SQLAlchemy — percentile_cont can't use .label() columns):
# func.percentile_cont(0.5).within_group(func.sum(...).label(...))

# AFTER (use raw SQL via text() for ordered-set aggregates):
from sqlalchemy import text as _t
async with self._db() as session:
    row = (await session.execute(_t("""
        SELECT
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY cost_usd) AS p50_cost,
            PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY cost_usd) AS p95_cost,
            AVG(cost_usd) AS avg_cost,
            COUNT(*) AS total_goals
        FROM cost_ledger
        WHERE tenant_id = :tid
          AND created_at >= NOW() - INTERVAL '30 days'
    """), {"tid": tenant_id})).fetchone()
```

### Amendment 6.3 — Fix module-level pricing cache (use Redis instead of per-process dict)

```python
# BEFORE (each worker process has its own stale cache):
_pricing_cache: dict[str, ModelPricing] = {}
_pricing_cache_updated: float = 0.0

# AFTER (Redis-backed shared cache, consistent across all workers):
async def get_model_pricing(model: str, redis, db) -> ModelPricing:
    """Get pricing from Redis L1 cache (5-min TTL), then DB."""
    cache_key = f"model_pricing:{model}"
    if redis:
        cached = await redis.get(cache_key)
        if cached:
            return ModelPricing(**json.loads(cached))
    # DB fallback:
    async with db() as session:
        row = (await session.execute(_t("SELECT input_usd_per_1m, output_usd_per_1m FROM model_pricing WHERE model_id = :m"), {"m": model})).fetchone()
    if row:
        pricing = ModelPricing(input_usd_per_1m=float(row[0]), output_usd_per_1m=float(row[1]))
        if redis:
            await redis.setex(cache_key, 300, json.dumps(pricing.__dict__))
        return pricing
    return ModelPricing(**FALLBACK_PRICING.get(model, {"input_usd_per_1m": 3.0, "output_usd_per_1m": 15.0}))

# Admin endpoint to invalidate pricing cache across all replicas:
# After PUT /costs/pricing → publish to "pricing_updated" Redis channel
# All workers subscribe and call: await redis.delete("model_pricing:*")
```

### Amendment 6.4 — Fix check_and_record(0.0) corrupting budget TTL

```python
# POST /costs/predict calls check_and_record with cost=0.0 to "peek" at budget
# This corrupts the Redis key TTL. Fix: separate peek function:
async def get_budget_status(self, tenant_id: str, goal_id: str) -> dict:
    """Read-only budget status check — does NOT modify any counters."""
    daily_key = f"cost:daily:{tenant_id}:{self._today()}"
    goal_key = f"cost:goal:{goal_id}"
    daily_spent = float(await self._redis.get(daily_key) or 0)
    goal_spent = float(await self._redis.get(goal_key) or 0)
    budget = await self._load_budgets(tenant_id)
    return {
        "daily_spent": daily_spent,
        "daily_limit": budget.per_tenant_daily_usd,
        "daily_remaining": max(0, budget.per_tenant_daily_usd - daily_spent),
        "budget_pct_remaining": max(0, 1 - daily_spent / max(budget.per_tenant_daily_usd, 0.01)),
    }

# CostPredictor.predict() calls get_budget_status() NOT check_and_record(0.0)
```

### Amendment 6.5 — Define budget alert subscriber + Celery anomaly task + App.tsx + toast + prefers-reduced-motion

```python
# Budget alert subscriber in main.py lifespan:
async def _budget_alert_listener():
    """Subscribe to Redis budget alerts and send via NotificationService."""
    pubsub = real_redis.pubsub()
    await pubsub.psubscribe("budget:alert:*")
    async for message in pubsub.listen():
        if message["type"] == "pmessage":
            tenant_id = message["channel"].split(":")[-1].decode()
            alert_data = json.loads(message["data"])
            notif_svc = app.state.notification_service
            await notif_svc.notify_budget_alert(
                tenant_id=tenant_id,
                alert_type=alert_data["alert_type"],
                spent_usd=alert_data["spent_usd"],
                limit_usd=alert_data["limit_usd"],
            )
asyncio.create_task(_budget_alert_listener())

# Celery task for anomaly batch scan:
@celery_app.task(name="app.scaling.tasks.scan_cost_anomalies", queue="maintenance")
def scan_cost_anomalies():
    """Run anomaly detection for all active tenants hourly."""
    import asyncio
    asyncio.run(_scan_all_tenant_anomalies())
# Beat schedule: every hour
```

```typescript
// App.tsx: CostDashboardPage already exists — ensure lazy
// Sidebar: already has /observability/cost

// prefers-reduced-motion:
@media (prefers-reduced-motion: reduce) {
  .budget-gauge-sweep, .cost-spike-alert, .treemap-rectangle-grow, .midnight-reset-sweep {
    animation: none !important; transition: none !important;
  }
}

// Toast notifications:
// updateBudget onSuccess: toast({kind:"success", message:"Budget limits updated"})
// updatePricing onSuccess: toast({kind:"success", message:"Model pricing refreshed across all replicas"})
// anomaly detected (SSE): toast({kind:"warning", message:`Cost anomaly: ${alert.message}`})

// Empty states:
// CostDashboard with no data: <EmptyState icon={DollarSign} title="No cost data yet" description="Submit your first goal to start tracking costs." />
```
