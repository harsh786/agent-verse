# Governance — World-Class Specification

**Area 4 · Migration 0056 · Version 1.0 · 2026-06-28**

---

## 1. Vision

Governance in AgentVerse is the system of policies, approvals, and controls that ensures autonomous agents operate within boundaries set by human operators. The current implementation has three fundamental flaws that make it unfit for production use in regulated environments: the Human-in-the-Loop (HITL) approval mechanism uses `asyncio.Event`, which is an in-process signal that dies when the server restarts and fails completely in multi-replica deployments because the event lives in one process's memory while the approval HTTP request lands on a different replica; the PolicyEngine supports only glob-pattern matching (`fnmatch`), which cannot handle semantically equivalent phrasings of a rule (a policy that says "no financial transactions over $10,000" will not match "transfer funds exceeding ten thousand dollars"); and there is no version history for policies, making it impossible to audit when a rule was changed, by whom, and what the previous state was — a blocker for SOX, GDPR, and HIPAA compliance.

This specification delivers a production-grade governance system built on four pillars. First, Redis Pub/Sub replaces `asyncio.Event` for HITL — an approval published to a Redis channel is consumed by whichever replica is holding the approval waiter, eliminating single-process dependency entirely. Second, the PolicyEngine gains a semantic evaluation path that uses an LLM judge to evaluate tool calls against policies expressed in natural language, with a structured confidence score and an audit trail of the reasoning. Third, every policy mutation creates a versioned snapshot in `policy_versions`, enabling full rollback and compliance-grade change history. Fourth, batch approval, SLA escalation with configurable escalation chains, and timezone-aware time windows make the governance system actionable in global organizations operating across multiple regulatory regimes simultaneously.

---

## 2. Current State Assessment

| Component | Current State | Gap | Severity |
|-----------|---------------|-----|----------|
| HITL approval mechanism | `asyncio.Event` in-process | Fails on restart; fails across replicas | CRITICAL |
| PolicyEngine matching | `fnmatch` glob-only | Cannot match semantically equivalent phrasings | HIGH |
| Policy version history | Overwrite in place | No audit trail for policy changes | HIGH |
| Policy inheritance | Not supported | Sub-agent policies cannot inherit from parent | HIGH |
| Timezone-aware windows | UTC-only | Cannot express "business hours New York" | HIGH |
| Batch approval | Not supported | Approvers must process one-by-one | MEDIUM |
| SLA on approvals | None | Approvals block indefinitely | MEDIUM |
| Escalation chain | Not implemented | No automatic escalation when approver is unavailable | MEDIUM |
| Policy change audit | Not implemented | SOX/HIPAA compliance blocker | HIGH |
| Domain templates | None | Healthcare/legal/finance start from zero | MEDIUM |

---

## 3. Backend Architecture

### 3.1 Database Schema — Migration 0056

```sql
-- =============================================================================
-- Migration 0056: Policy versions, SLA configs, approval queue persistence
-- Author: AgentVerse Platform Team
-- Date: 2026-06-28
-- =============================================================================

BEGIN;

-- --------------------------------------------------------
-- Table: policy_versions
-- Immutable snapshot of every policy state change
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS policy_versions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    policy_id       UUID NOT NULL,                          -- logical policy ID (stable across versions)
    version_number  INTEGER NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT,
    rules           JSONB NOT NULL,                         -- serialized policy rule set
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,     -- tags, domain, author notes
    is_active       BOOLEAN NOT NULL DEFAULT FALSE,
    parent_policy_id UUID,                                  -- for sub-agent inheritance
    change_summary  TEXT,
    changed_by      UUID REFERENCES users(id),
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ,                            -- soft delete for legal holds
    CONSTRAINT uq_policy_version UNIQUE (policy_id, version_number)
);

CREATE INDEX idx_policy_versions_tenant
    ON policy_versions(tenant_id, policy_id, version_number DESC);
CREATE INDEX idx_policy_versions_active
    ON policy_versions(policy_id) WHERE is_active = TRUE;
CREATE INDEX idx_policy_versions_parent
    ON policy_versions(parent_policy_id) WHERE parent_policy_id IS NOT NULL;

ALTER TABLE policy_versions ENABLE ROW LEVEL SECURITY;
CREATE POLICY policy_versions_isolation ON policy_versions
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- --------------------------------------------------------
-- Table: approval_sla_configs
-- Per-tenant SLA definitions for HITL approvals
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS approval_sla_configs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    description     TEXT,
    risk_level      TEXT NOT NULL
                    CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    response_sla_minutes    INTEGER NOT NULL DEFAULT 60,
    escalation_sla_minutes  INTEGER NOT NULL DEFAULT 120,
    escalation_roles        JSONB NOT NULL DEFAULT '[]'::jsonb,  -- role names to escalate to
    escalation_channels     JSONB NOT NULL DEFAULT '[]'::jsonb,  -- slack/email/pagerduty
    auto_approve_on_timeout BOOLEAN NOT NULL DEFAULT FALSE,      -- dangerous — audit always
    auto_deny_on_timeout    BOOLEAN NOT NULL DEFAULT FALSE,
    timeout_minutes         INTEGER NOT NULL DEFAULT 480,        -- 8 hours default
    business_hours_only     BOOLEAN NOT NULL DEFAULT FALSE,
    timezone                TEXT NOT NULL DEFAULT 'UTC',
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_sla_tenant_name UNIQUE (tenant_id, name),
    CONSTRAINT chk_sla_timeout_auto CHECK (
        NOT (auto_approve_on_timeout = TRUE AND auto_deny_on_timeout = TRUE)
    )
);

CREATE INDEX idx_sla_configs_tenant ON approval_sla_configs(tenant_id) WHERE is_active = TRUE;

ALTER TABLE approval_sla_configs ENABLE ROW LEVEL SECURITY;
CREATE POLICY sla_configs_isolation ON approval_sla_configs
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- --------------------------------------------------------
-- Table: hitl_approval_requests (durable, replaces asyncio.Event)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS hitl_approval_requests (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    goal_id         UUID REFERENCES goals(id) ON DELETE SET NULL,
    agent_id        UUID REFERENCES agents(id) ON DELETE SET NULL,
    step_id         TEXT NOT NULL,
    request_payload JSONB NOT NULL,                         -- full step context for approver
    risk_level      TEXT NOT NULL DEFAULT 'high',
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'denied', 'escalated',
                                      'timed_out', 'auto_approved', 'auto_denied')),
    sla_config_id   UUID REFERENCES approval_sla_configs(id),
    sla_deadline    TIMESTAMPTZ,
    escalated_at    TIMESTAMPTZ,
    resolved_by     UUID REFERENCES users(id),
    resolved_at     TIMESTAMPTZ,
    resolution_note TEXT,
    resolution_data JSONB,                                  -- approver-supplied context
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_hitl_requests_tenant_status
    ON hitl_approval_requests(tenant_id, status, created_at DESC)
    WHERE status = 'pending';
CREATE INDEX idx_hitl_requests_sla
    ON hitl_approval_requests(sla_deadline)
    WHERE status = 'pending';

ALTER TABLE hitl_approval_requests ENABLE ROW LEVEL SECURITY;
CREATE POLICY hitl_requests_isolation ON hitl_approval_requests
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- --------------------------------------------------------
-- Table: policy_evaluations
-- Audit trail for every policy evaluation (both glob and LLM-judge)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS policy_evaluations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    policy_id       UUID,
    policy_version  INTEGER,
    goal_id         UUID REFERENCES goals(id) ON DELETE SET NULL,
    tool_name       TEXT,
    tool_args_hash  TEXT,
    matched         BOOLEAN NOT NULL,
    match_method    TEXT NOT NULL CHECK (match_method IN ('glob', 'regex', 'llm_judge', 'exact')),
    llm_judge_score NUMERIC(4,3),
    llm_judge_reason TEXT,
    action_taken    TEXT NOT NULL,
    evaluated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
) PARTITION BY RANGE (evaluated_at);

CREATE TABLE policy_evaluations_2026_06
    PARTITION OF policy_evaluations FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE policy_evaluations_2026_07
    PARTITION OF policy_evaluations FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');

CREATE INDEX idx_policy_evals_tenant
    ON policy_evaluations(tenant_id, evaluated_at DESC);

ALTER TABLE policy_evaluations ENABLE ROW LEVEL SECURITY;
CREATE POLICY policy_evals_isolation ON policy_evaluations
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

COMMIT;
```

### 3.2 Alembic Migration File

```python
# agent-verse-backend/app/db/migrations/versions/0056_governance_versioning_sla.py
"""policy_versions, approval_sla_configs, hitl_approval_requests, policy_evaluations

Revision ID: 0056
Revises: 0055
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, NUMERIC, TIMESTAMPTZ

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "policy_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_id", UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("rules", JSONB(), nullable=False),
        sa.Column("metadata", JSONB(), nullable=False, server_default="'{}'"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="FALSE"),
        sa.Column("parent_policy_id", UUID(as_uuid=True)),
        sa.Column("change_summary", sa.Text()),
        sa.Column("changed_by", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("changed_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", TIMESTAMPTZ()),
        sa.UniqueConstraint("policy_id", "version_number", name="uq_policy_version"),
    )

    op.create_table(
        "approval_sla_configs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("risk_level", sa.Text(), nullable=False),
        sa.Column("response_sla_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("escalation_sla_minutes", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("escalation_roles", JSONB(), nullable=False, server_default="'[]'"),
        sa.Column("escalation_channels", JSONB(), nullable=False, server_default="'[]'"),
        sa.Column("auto_approve_on_timeout", sa.Boolean(), nullable=False, server_default="FALSE"),
        sa.Column("auto_deny_on_timeout", sa.Boolean(), nullable=False, server_default="FALSE"),
        sa.Column("timeout_minutes", sa.Integer(), nullable=False, server_default="480"),
        sa.Column("business_hours_only", sa.Boolean(), nullable=False, server_default="FALSE"),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="'UTC'"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="TRUE"),
        sa.Column("created_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_sla_tenant_name"),
    )

    op.create_table(
        "hitl_approval_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("goal_id", UUID(as_uuid=True), sa.ForeignKey("goals.id", ondelete="SET NULL")),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="SET NULL")),
        sa.Column("step_id", sa.Text(), nullable=False),
        sa.Column("request_payload", JSONB(), nullable=False),
        sa.Column("risk_level", sa.Text(), nullable=False, server_default="'high'"),
        sa.Column("status", sa.Text(), nullable=False, server_default="'pending'"),
        sa.Column("sla_config_id", UUID(as_uuid=True),
                  sa.ForeignKey("approval_sla_configs.id")),
        sa.Column("sla_deadline", TIMESTAMPTZ()),
        sa.Column("escalated_at", TIMESTAMPTZ()),
        sa.Column("resolved_by", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("resolved_at", TIMESTAMPTZ()),
        sa.Column("resolution_note", sa.Text()),
        sa.Column("resolution_data", JSONB()),
        sa.Column("created_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS policy_evaluations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            policy_id UUID,
            policy_version INTEGER,
            goal_id UUID REFERENCES goals(id) ON DELETE SET NULL,
            tool_name TEXT,
            tool_args_hash TEXT,
            matched BOOLEAN NOT NULL,
            match_method TEXT NOT NULL,
            llm_judge_score NUMERIC(4,3),
            llm_judge_reason TEXT,
            action_taken TEXT NOT NULL,
            evaluated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        ) PARTITION BY RANGE (evaluated_at)
    """)
    op.execute("""
        CREATE TABLE policy_evaluations_2026_06
            PARTITION OF policy_evaluations FOR VALUES FROM ('2026-06-01') TO ('2026-07-01')
    """)


def downgrade() -> None:
    op.drop_table("policy_evaluations")
    op.drop_table("hitl_approval_requests")
    op.drop_table("approval_sla_configs")
    op.drop_table("policy_versions")
```

### 3.3 API Endpoints

**GET /api/governance/policies** — List active policies; supports `?include_history=true`

**POST /api/governance/policies**
```json
{
  "name": "no_large_transactions",
  "description": "Block any financial transaction over $10,000 without HITL approval",
  "rules": [
    {
      "type": "tool_call",
      "tool_name_pattern": "*transfer*",
      "condition": {
        "field": "args.amount",
        "operator": "gt",
        "value": 10000
      },
      "action": "hitl",
      "semantic_description": "Financial transfer of more than ten thousand dollars"
    }
  ],
  "parent_policy_id": null,
  "change_summary": "Initial policy for SOX compliance"
}
```
Response 201: policy object with `version_number: 1`

**GET /api/governance/policies/{policy_id}** — Returns active version

**GET /api/governance/policies/{policy_id}/versions** — Full version history

**GET /api/governance/policies/{policy_id}/versions/{version}** — Specific version snapshot

**PATCH /api/governance/policies/{policy_id}**
- Creates a new version (increments `version_number`)
- Requires `change_summary`
- Atomically deactivates old, activates new

**DELETE /api/governance/policies/{policy_id}**
- Soft-delete (sets `deleted_at` on active version)
- Error: `409 POLICY_HAS_ACTIVE_HITL` if pending approvals exist

**POST /api/governance/policies/{policy_id}/rollback**
```json
{ "target_version": 3, "reason": "Rolled back due to false positive rate" }
```

#### HITL Approvals

**GET /api/governance/approvals** — List pending/resolved requests
- Query: `status`, `risk_level`, `goal_id`, `agent_id`, `from_date`, `assigned_to_me`

**GET /api/governance/approvals/{request_id}** — Full request with payload

**POST /api/governance/approvals/{request_id}/approve**
```json
{
  "note": "Reviewed; amount within policy exception for this client",
  "conditions": { "notify_risk_team": true }
}
```
Response 200: `{ "resolved": true, "goal_resumed": true }`

**POST /api/governance/approvals/{request_id}/deny**
```json
{ "note": "Amount exceeds client's risk profile", "notify_goal_owner": true }
```

**POST /api/governance/approvals/batch**
```json
{
  "action": "approve",
  "request_ids": ["uuid1", "uuid2", "uuid3"],
  "note": "Batch approval after risk review meeting"
}
```
Response 200: `{ "processed": 3, "failed": [], "results": [...] }`

**GET /api/governance/approvals/stats** — SLA compliance rate, average resolution time

**POST /api/governance/approvals/{request_id}/escalate** — Manual escalation

#### SLA Configuration

**GET /api/governance/sla-configs**

**POST /api/governance/sla-configs**
```json
{
  "name": "critical-financial",
  "risk_level": "critical",
  "response_sla_minutes": 30,
  "escalation_sla_minutes": 60,
  "escalation_roles": ["risk_officer", "cto"],
  "escalation_channels": [
    { "type": "pagerduty", "service_key": "..." },
    { "type": "slack", "channel": "#risk-alerts" }
  ],
  "auto_deny_on_timeout": true,
  "timeout_minutes": 240,
  "business_hours_only": true,
  "timezone": "America/New_York"
}
```

**PATCH /api/governance/sla-configs/{id}**

**DELETE /api/governance/sla-configs/{id}** — Error if active pending requests reference this config

### 3.4 Business Logic — Python

```python
# agent-verse-backend/app/governance/hitl_redis.py
"""
Redis Pub/Sub-based HITL approval gateway.

Replaces asyncio.Event (which dies on restart + fails across replicas) with
a durable Redis channel approach:

  1. When HITL is triggered, insert hitl_approval_requests row (durable)
  2. Publish "hitl:pending:{request_id}" to Redis channel
  3. Agent loop subscribes to "hitl:result:{request_id}" and waits with timeout
  4. Approver HTTP call inserts result to DB, then publishes to result channel
  5. Waiting agent loop reads result and continues or aborts

This pattern works correctly across any number of replicas.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Channel naming convention
PENDING_CHANNEL  = "hitl:pending:{tenant_id}"
RESULT_CHANNEL   = "hitl:result:{request_id}"
ESCALATION_CHANNEL = "hitl:escalate:{tenant_id}"


class HITLGateway:
    """
    Cross-replica HITL approval gateway backed by Redis Pub/Sub + durable DB storage.
    """

    def __init__(self, redis: aioredis.Redis, db_factory) -> None:
        self._redis = redis
        self._db_factory = db_factory

    async def request_approval(
        self,
        tenant_id: str,
        goal_id: str,
        agent_id: str,
        step_id: str,
        payload: dict[str, Any],
        risk_level: str = "high",
        timeout_seconds: int = 3600,
    ) -> dict[str, Any]:
        """
        Create a durable approval request, wait for resolution via Redis Pub/Sub.
        Returns {"approved": True/False, "note": "...", "resolved_by": "..."}.

        If the process restarts during the wait, the agent can recover by:
          1. Checking hitl_approval_requests for the step_id
          2. If status == 'approved'/'denied': use that result
          3. If status == 'pending': re-subscribe to result channel
        """
        request_id = str(uuid.uuid4())

        # 1. Persist request durably
        async with self._db_factory() as db:
            sla_config = await self._load_sla_config(db, tenant_id, risk_level)
            sla_deadline = None
            if sla_config:
                sla_deadline = datetime.now(timezone.utc) + timedelta(
                    minutes=sla_config.timeout_minutes
                )
            await self._insert_request(
                db, request_id, tenant_id, goal_id, agent_id,
                step_id, payload, risk_level, sla_config, sla_deadline,
            )

        # 2. Announce to all connected approver clients
        await self._redis.publish(
            PENDING_CHANNEL.format(tenant_id=tenant_id),
            json.dumps({
                "request_id": request_id,
                "goal_id": goal_id,
                "step_id": step_id,
                "risk_level": risk_level,
                "payload_summary": self._summarize_payload(payload),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }),
        )

        # 3. Wait for result via pub/sub
        result = await self._wait_for_result(request_id, timeout_seconds)

        if result is None:
            # Timeout: apply SLA action
            async with self._db_factory() as db:
                result = await self._handle_timeout(db, request_id, tenant_id, sla_config)

        return result

    async def _wait_for_result(
        self, request_id: str, timeout_seconds: int
    ) -> Optional[dict[str, Any]]:
        """
        Subscribe to the result channel, wait up to timeout_seconds.
        Uses a separate Redis connection for pub/sub (required by redis-py).
        """
        pubsub = self._redis.pubsub()
        channel = RESULT_CHANNEL.format(request_id=request_id)

        try:
            await pubsub.subscribe(channel)
            deadline = asyncio.get_event_loop().time() + timeout_seconds

            while asyncio.get_event_loop().time() < deadline:
                remaining = deadline - asyncio.get_event_loop().time()
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                        timeout=min(remaining, 5.0),
                    )
                except asyncio.TimeoutError:
                    continue

                if message and message["type"] == "message":
                    return json.loads(message["data"])

        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

        return None

    async def publish_resolution(
        self,
        request_id: str,
        tenant_id: str,
        approved: bool,
        resolved_by: str,
        note: str = "",
        data: Optional[dict] = None,
    ) -> None:
        """
        Called by the approver's HTTP handler.
        Persists result to DB, then publishes to unblock waiting agents.
        """
        status = "approved" if approved else "denied"

        async with self._db_factory() as db:
            await db.execute(
                """
                UPDATE hitl_approval_requests
                SET status = :status,
                    resolved_by = :resolved_by,
                    resolved_at = now(),
                    resolution_note = :note,
                    resolution_data = :data
                WHERE id = :request_id AND tenant_id = :tenant_id
                """,
                {
                    "status": status,
                    "resolved_by": resolved_by,
                    "note": note,
                    "data": json.dumps(data or {}),
                    "request_id": request_id,
                    "tenant_id": tenant_id,
                },
            )
            await db.commit()

        # Publish result to the waiting agent (which may be on any replica)
        await self._redis.publish(
            RESULT_CHANNEL.format(request_id=request_id),
            json.dumps({
                "request_id": request_id,
                "approved": approved,
                "resolved_by": resolved_by,
                "note": note,
                "resolved_at": datetime.now(timezone.utc).isoformat(),
            }),
        )

        logger.info(
            "hitl_resolved",
            request_id=request_id,
            approved=approved,
            resolved_by=resolved_by,
        )

    async def recover_pending(
        self, tenant_id: str, step_id: str
    ) -> Optional[dict[str, Any]]:
        """
        On agent restart: check if a previous approval request for this step
        was already resolved. Returns result dict or None if still pending.
        """
        async with self._db_factory() as db:
            row = await db.execute(
                """
                SELECT id, status, resolution_note, resolved_by, resolved_at
                FROM hitl_approval_requests
                WHERE tenant_id = :tenant_id
                  AND step_id = :step_id
                  AND status IN ('approved', 'denied', 'auto_approved', 'auto_denied')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                {"tenant_id": tenant_id, "step_id": step_id},
            )
            result = row.fetchone()
            if result:
                return {
                    "approved": result.status in ("approved", "auto_approved"),
                    "note": result.resolution_note,
                    "resolved_by": str(result.resolved_by) if result.resolved_by else "system",
                    "recovered": True,
                }
        return None

    async def _handle_timeout(
        self,
        db: AsyncSession,
        request_id: str,
        tenant_id: str,
        sla_config: Optional[Any],
    ) -> dict[str, Any]:
        if sla_config and sla_config.auto_approve_on_timeout:
            await db.execute(
                "UPDATE hitl_approval_requests SET status = 'auto_approved', "
                "resolved_at = now() WHERE id = :id",
                {"id": request_id},
            )
            await db.commit()
            logger.warning("hitl_auto_approved_on_timeout", request_id=request_id)
            return {"approved": True, "note": "Auto-approved: SLA timeout reached", "resolved_by": "system"}

        if sla_config and sla_config.auto_deny_on_timeout:
            await db.execute(
                "UPDATE hitl_approval_requests SET status = 'auto_denied', "
                "resolved_at = now() WHERE id = :id",
                {"id": request_id},
            )
            await db.commit()
            return {"approved": False, "note": "Auto-denied: SLA timeout reached", "resolved_by": "system"}

        # Default: deny on timeout for safety
        await db.execute(
            "UPDATE hitl_approval_requests SET status = 'timed_out', "
            "resolved_at = now() WHERE id = :id",
            {"id": request_id},
        )
        await db.commit()
        return {"approved": False, "note": "Timed out awaiting human approval", "resolved_by": "system"}

    @staticmethod
    def _summarize_payload(payload: dict[str, Any]) -> str:
        """Safe 200-char summary for notification channels."""
        summary = json.dumps(payload)
        return summary[:200] + "..." if len(summary) > 200 else summary

    async def _load_sla_config(self, db: AsyncSession, tenant_id: str, risk_level: str):
        from sqlalchemy import select
        from app.db.models.governance import ApprovalSLAConfig
        result = await db.execute(
            select(ApprovalSLAConfig).where(
                ApprovalSLAConfig.tenant_id == UUID(tenant_id),
                ApprovalSLAConfig.risk_level == risk_level,
                ApprovalSLAConfig.is_active.is_(True),
            ).order_by(ApprovalSLAConfig.created_at.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def _insert_request(
        self, db: AsyncSession, request_id: str, tenant_id: str,
        goal_id: str, agent_id: str, step_id: str, payload: dict,
        risk_level: str, sla_config, sla_deadline,
    ) -> None:
        from app.db.models.governance import HITLApprovalRequest
        req = HITLApprovalRequest(
            id=UUID(request_id),
            tenant_id=UUID(tenant_id),
            goal_id=UUID(goal_id) if goal_id else None,
            agent_id=UUID(agent_id) if agent_id else None,
            step_id=step_id,
            request_payload=payload,
            risk_level=risk_level,
            status="pending",
            sla_config_id=sla_config.id if sla_config else None,
            sla_deadline=sla_deadline,
        )
        db.add(req)
        await db.commit()


# ---------------------------------------------------------------------------
# Semantic PolicyEngine
# ---------------------------------------------------------------------------

class SemanticPolicyEngine:
    """
    Evaluates tool calls against policies using:
      1. Structural (glob/regex/condition) matching — fast, cheap
      2. LLM-as-judge semantic matching — only when structural is inconclusive
         or when policy rule has semantic_description set

    Each evaluation produces an audit entry in policy_evaluations.
    """

    def __init__(
        self,
        tenant_id: str,
        policies: list[dict],
        llm_provider_factory=None,
        judge_threshold: float = 0.75,
    ) -> None:
        self._tenant_id = tenant_id
        self._policies = policies
        self._llm_factory = llm_provider_factory
        self._threshold = judge_threshold

    async def evaluate(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        goal_context: Optional[str] = None,
    ) -> tuple[bool, Optional[str], Optional[str]]:
        """
        Returns (should_allow, matched_policy_id, action).
        action is one of: 'allow', 'deny', 'hitl', 'warn'
        """
        tool_call_text = f"Tool: {tool_name}\nArgs: {json.dumps(tool_args)}"

        for policy in self._policies:
            for rule in policy.get("rules", []):
                # --- Structural match ---
                structural_match = self._structural_match(rule, tool_name, tool_args)

                # --- Semantic match (if structural inconclusive and semantic_description given) ---
                if not structural_match and rule.get("semantic_description") and self._llm_factory:
                    semantic_score = await self._semantic_match(
                        rule["semantic_description"], tool_call_text
                    )
                    if semantic_score >= self._threshold:
                        action = rule.get("action", "deny")
                        logger.info(
                            "policy_semantic_match",
                            policy_id=str(policy.get("id")),
                            rule=rule.get("id"),
                            score=semantic_score,
                            tool=tool_name,
                        )
                        return (action == "allow"), str(policy.get("id")), action

                elif structural_match:
                    action = rule.get("action", "deny")
                    return (action == "allow"), str(policy.get("id")), action

        return True, None, "allow"  # No policy matched = allow

    def _structural_match(
        self, rule: dict[str, Any], tool_name: str, tool_args: dict[str, Any]
    ) -> bool:
        import fnmatch

        # Tool name match
        tool_pattern = rule.get("tool_name_pattern")
        if tool_pattern and not fnmatch.fnmatch(tool_name, tool_pattern):
            return False

        # Condition match
        condition = rule.get("condition")
        if not condition:
            return True  # No condition = match on tool name alone

        field_path = condition.get("field", "")
        operator = condition.get("operator", "eq")
        target = condition.get("value")

        actual = self._get_nested(tool_args, field_path)
        if actual is None:
            return False

        return self._evaluate_condition(actual, operator, target)

    @staticmethod
    def _get_nested(obj: Any, path: str) -> Any:
        """Navigate dot-separated path: 'args.amount' → obj['args']['amount']"""
        if not path:
            return obj
        parts = path.replace("args.", "").split(".")
        current = obj
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current

    @staticmethod
    def _evaluate_condition(actual: Any, operator: str, target: Any) -> bool:
        ops = {
            "eq":  lambda a, b: a == b,
            "neq": lambda a, b: a != b,
            "gt":  lambda a, b: float(a) > float(b),
            "gte": lambda a, b: float(a) >= float(b),
            "lt":  lambda a, b: float(a) < float(b),
            "lte": lambda a, b: float(a) <= float(b),
            "contains": lambda a, b: str(b).lower() in str(a).lower(),
            "regex": lambda a, b: bool(__import__("re").search(b, str(a))),
            "in":  lambda a, b: a in b,
        }
        fn = ops.get(operator)
        if fn is None:
            return False
        try:
            return fn(actual, target)
        except (TypeError, ValueError):
            return False

    async def _semantic_match(self, semantic_description: str, tool_call_text: str) -> float:
        """Returns 0.0-1.0 semantic similarity score via LLM."""
        system = """You are a policy compliance evaluator for an AI agent platform.

Given a policy rule description and a tool call, determine whether the tool call
violates or matches the described policy.

Respond with ONLY valid JSON:
{
  "matches": true,
  "confidence": 0.92,
  "reasoning": "The tool transfers $15,000 which exceeds the $10,000 threshold"
}

confidence must be 0.0 (definitely does not match) to 1.0 (definitely matches)."""

        user = f"""Policy rule: {semantic_description}

Tool call:
{tool_call_text}

Does this tool call match (violate) the policy rule?"""

        try:
            provider = await self._llm_factory()
            from app.providers.base import CompletionRequest, Message
            response = await provider.complete(CompletionRequest(
                model="gpt-4o-mini",
                messages=[
                    Message(role="system", content=system),
                    Message(role="user", content=user),
                ],
                max_tokens=150,
                temperature=0.0,
            ))
            result = json.loads(response.content.strip())
            return float(result.get("confidence", 0.0)) if result.get("matches") else 0.0
        except Exception as exc:
            logger.warning("semantic_policy_eval_error", error=str(exc))
            return 0.0


# ---------------------------------------------------------------------------
# SLA enforcement background task
# ---------------------------------------------------------------------------

async def enforce_sla_deadlines(redis: aioredis.Redis, db_factory) -> None:
    """
    Runs every minute via Celery beat.
    Escalates requests past response_sla_minutes.
    Auto-resolves requests past timeout_minutes.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    async with db_factory() as db:
        # Find pending requests past SLA deadline
        from sqlalchemy import select, text
        from app.db.models.governance import HITLApprovalRequest, ApprovalSLAConfig

        overdue = await db.execute(
            select(HITLApprovalRequest, ApprovalSLAConfig)
            .join(ApprovalSLAConfig,
                  HITLApprovalRequest.sla_config_id == ApprovalSLAConfig.id,
                  isouter=True)
            .where(
                HITLApprovalRequest.status == "pending",
                HITLApprovalRequest.sla_deadline <= now,
            )
        )

        for request, sla in overdue.fetchall():
            tenant_id = str(request.tenant_id)
            request_id = str(request.id)

            if sla and sla.auto_deny_on_timeout:
                await _auto_resolve(db, redis, request_id, tenant_id,
                                    approved=False, reason="SLA timeout: auto-denied")
            elif sla and sla.auto_approve_on_timeout:
                await _auto_resolve(db, redis, request_id, tenant_id,
                                    approved=True, reason="SLA timeout: auto-approved")
            else:
                # Escalate if not already escalated
                if not request.escalated_at:
                    await _escalate(db, redis, request, sla)

        await db.commit()


async def _auto_resolve(db, redis, request_id: str, tenant_id: str, approved: bool, reason: str):
    from sqlalchemy import update
    from app.db.models.governance import HITLApprovalRequest
    status = "auto_approved" if approved else "auto_denied"
    await db.execute(
        update(HITLApprovalRequest)
        .where(HITLApprovalRequest.id == UUID(request_id))
        .values(status=status, resolved_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc))
    )
    await redis.publish(
        RESULT_CHANNEL.format(request_id=request_id),
        json.dumps({"approved": approved, "note": reason, "resolved_by": "system"}),
    )


async def _escalate(db, redis, request, sla):
    from sqlalchemy import update
    from app.db.models.governance import HITLApprovalRequest
    from datetime import datetime, timezone
    await db.execute(
        update(HITLApprovalRequest)
        .where(HITLApprovalRequest.id == request.id)
        .values(
            status="escalated",
            escalated_at=datetime.now(timezone.utc),
        )
    )
    escalation_roles = sla.escalation_roles if sla else []
    await redis.publish(
        ESCALATION_CHANNEL.format(tenant_id=str(request.tenant_id)),
        json.dumps({
            "request_id": str(request.id),
            "escalation_roles": escalation_roles,
            "reason": "SLA response time exceeded",
        }),
    )


# ---------------------------------------------------------------------------
# Policy version management
# ---------------------------------------------------------------------------

class PolicyVersionManager:
    """
    Manages the version lifecycle for policies.
    Every mutation creates a new immutable version snapshot.
    """

    async def create_policy(
        self,
        db: AsyncSession,
        tenant_id: str,
        name: str,
        rules: list[dict],
        description: str = "",
        change_summary: str = "Initial version",
        changed_by: Optional[str] = None,
        parent_policy_id: Optional[str] = None,
    ) -> dict[str, Any]:
        policy_id = uuid.uuid4()
        version = await self._insert_version(
            db, tenant_id=tenant_id, policy_id=str(policy_id),
            version_number=1, name=name, rules=rules,
            description=description, is_active=True,
            change_summary=change_summary,
            changed_by=changed_by,
            parent_policy_id=parent_policy_id,
        )
        await db.commit()
        return version

    async def update_policy(
        self,
        db: AsyncSession,
        tenant_id: str,
        policy_id: str,
        updates: dict[str, Any],
        change_summary: str,
        changed_by: Optional[str] = None,
    ) -> dict[str, Any]:
        from sqlalchemy import select, update
        from app.db.models.governance import PolicyVersion

        # Get current active version
        result = await db.execute(
            select(PolicyVersion).where(
                PolicyVersion.policy_id == UUID(policy_id),
                PolicyVersion.is_active.is_(True),
            )
        )
        current = result.scalar_one_or_none()
        if not current:
            raise ValueError(f"Policy {policy_id} not found")

        # Deactivate current
        await db.execute(
            update(PolicyVersion)
            .where(PolicyVersion.id == current.id)
            .values(is_active=False)
        )

        # Create new version
        new_rules = updates.get("rules", current.rules)
        new_name  = updates.get("name", current.name)
        new_desc  = updates.get("description", current.description)

        new_version = await self._insert_version(
            db, tenant_id=tenant_id, policy_id=policy_id,
            version_number=current.version_number + 1,
            name=new_name, rules=new_rules, description=new_desc,
            is_active=True, change_summary=change_summary,
            changed_by=changed_by,
            parent_policy_id=str(current.parent_policy_id) if current.parent_policy_id else None,
        )
        await db.commit()
        return new_version

    async def rollback(
        self,
        db: AsyncSession,
        tenant_id: str,
        policy_id: str,
        target_version: int,
        reason: str,
        rolled_back_by: Optional[str] = None,
    ) -> dict[str, Any]:
        from sqlalchemy import select, update
        from app.db.models.governance import PolicyVersion

        # Load target version snapshot
        result = await db.execute(
            select(PolicyVersion).where(
                PolicyVersion.policy_id == UUID(policy_id),
                PolicyVersion.version_number == target_version,
            )
        )
        target = result.scalar_one_or_none()
        if not target:
            raise ValueError(f"Version {target_version} not found for policy {policy_id}")

        # Deactivate current active
        await db.execute(
            update(PolicyVersion)
            .where(
                PolicyVersion.policy_id == UUID(policy_id),
                PolicyVersion.is_active.is_(True),
            )
            .values(is_active=False)
        )

        # Get current max version number
        max_ver_result = await db.execute(
            select(__import__("sqlalchemy").func.max(PolicyVersion.version_number))
            .where(PolicyVersion.policy_id == UUID(policy_id))
        )
        max_version = max_ver_result.scalar() or 0

        # Create new version from snapshot (not reactivate old)
        return await self._insert_version(
            db, tenant_id=tenant_id, policy_id=policy_id,
            version_number=max_version + 1,
            name=target.name, rules=target.rules,
            description=target.description,
            is_active=True,
            change_summary=f"Rollback to v{target_version}: {reason}",
            changed_by=rolled_back_by,
        )

    async def _insert_version(self, db, *, tenant_id, policy_id, version_number,
                               name, rules, description, is_active, change_summary,
                               changed_by=None, parent_policy_id=None) -> dict:
        from app.db.models.governance import PolicyVersion
        pv = PolicyVersion(
            tenant_id=UUID(tenant_id),
            policy_id=UUID(policy_id),
            version_number=version_number,
            name=name,
            description=description,
            rules=rules,
            is_active=is_active,
            parent_policy_id=UUID(parent_policy_id) if parent_policy_id else None,
            change_summary=change_summary,
            changed_by=UUID(changed_by) if changed_by else None,
        )
        db.add(pv)
        await db.flush()
        return {"id": str(pv.id), "policy_id": policy_id, "version_number": version_number}


# ---------------------------------------------------------------------------
# Domain governance templates
# ---------------------------------------------------------------------------

DOMAIN_GOVERNANCE_TEMPLATES: dict[str, list[dict]] = {
    "hipaa": [
        {
            "name": "phi_access_control",
            "rules": [
                {
                    "type": "tool_call",
                    "tool_name_pattern": "*patient*",
                    "condition": {"field": "args.bulk", "operator": "eq", "value": True},
                    "action": "hitl",
                    "semantic_description": "Bulk access to patient health information",
                },
                {
                    "type": "tool_call",
                    "tool_name_pattern": "*export*",
                    "action": "hitl",
                    "semantic_description": "Export of medical records or patient data",
                },
            ],
        },
        {
            "name": "phi_minimum_necessary",
            "rules": [
                {
                    "type": "tool_call",
                    "tool_name_pattern": "*",
                    "condition": {"field": "args.phi_count", "operator": "gt", "value": 100},
                    "action": "deny",
                    "semantic_description": "Accessing more than 100 patient records in one operation",
                }
            ],
        },
    ],
    "sox": [
        {
            "name": "financial_transaction_controls",
            "rules": [
                {
                    "type": "tool_call",
                    "tool_name_pattern": "*transfer*",
                    "condition": {"field": "args.amount", "operator": "gt", "value": 10000},
                    "action": "hitl",
                    "semantic_description": "Financial transfer exceeding ten thousand dollars",
                },
                {
                    "type": "tool_call",
                    "tool_name_pattern": "*journal_entry*",
                    "action": "hitl",
                    "semantic_description": "Creating or modifying accounting journal entries",
                },
                {
                    "type": "tool_call",
                    "tool_name_pattern": "*override*",
                    "action": "deny",
                    "semantic_description": "Overriding financial controls or approvals",
                },
            ],
        },
    ],
    "gdpr": [
        {
            "name": "personal_data_processing",
            "rules": [
                {
                    "type": "tool_call",
                    "tool_name_pattern": "*personal_data*",
                    "action": "hitl",
                    "semantic_description": "Processing of personal data of EU data subjects",
                },
                {
                    "type": "tool_call",
                    "tool_name_pattern": "*delete_user*",
                    "action": "hitl",
                    "semantic_description": "Deletion of personal data (right to erasure)",
                },
                {
                    "type": "tool_call",
                    "tool_name_pattern": "*export_user*",
                    "action": "hitl",
                    "semantic_description": "Export of personal data (right to portability)",
                },
            ],
        },
    ],
    "legal": [
        {
            "name": "privileged_communication",
            "rules": [
                {
                    "type": "tool_call",
                    "tool_name_pattern": "*send*",
                    "action": "hitl",
                    "semantic_description": "Sending privileged attorney-client communications",
                },
                {
                    "type": "tool_call",
                    "tool_name_pattern": "*file*",
                    "condition": {"field": "args.court", "operator": "eq", "value": True},
                    "action": "hitl",
                    "semantic_description": "Filing a court document on behalf of a client",
                },
            ],
        },
    ],
}
```

### 3.5 main.py Wiring Changes

```python
# Additions to agent-verse-backend/app/main.py

from app.governance.hitl_redis import HITLGateway
from app.governance.router import router as governance_router

def create_app(manage_pools: bool = True) -> FastAPI:
    app = FastAPI(...)

    # ... existing ...

    # HITL gateway (replaces asyncio.Event-based hitl.py)
    app.state.hitl_gateway = HITLGateway(
        redis=app.state.redis,
        db_factory=app.state.db_session_factory,
    )

    app.include_router(governance_router, prefix="/api/governance", tags=["Governance"])

    return app
```

---

## 4. Frontend Specification

### 4.1 New Pages & Routes

| Route | Sidebar Entry | Description |
|-------|---------------|-------------|
| `/governance` | Governance | Policy list + approval queue |
| `/governance/policies` | Governance → Policies | Policy management |
| `/governance/policies/:id/versions` | (nested) | Version history diff viewer |
| `/governance/approvals` | Governance → Approvals | HITL approval queue with SLA timers |
| `/governance/approvals/batch` | (action) | Batch review interface |
| `/governance/sla` | Governance → SLA Config | SLA configuration |
| `/governance/templates` | Governance → Templates | Domain template library |

### 4.2 TypeScript Interfaces

```typescript
// src/features/governance/types.ts

export interface PolicyVersion {
  id: string;
  policyId: string;
  versionNumber: number;
  name: string;
  description: string | null;
  rules: PolicyRule[];
  isActive: boolean;
  parentPolicyId: string | null;
  changeSummary: string | null;
  changedBy: string | null;
  changedAt: string;
}

export interface PolicyRule {
  id?: string;
  type: 'tool_call' | 'goal_input' | 'output';
  toolNamePattern?: string;
  condition?: PolicyCondition;
  action: 'allow' | 'deny' | 'hitl' | 'warn';
  semanticDescription?: string;
}

export interface PolicyCondition {
  field: string;
  operator: 'eq' | 'neq' | 'gt' | 'gte' | 'lt' | 'lte' | 'contains' | 'regex' | 'in';
  value: unknown;
}

export interface HITLApprovalRequest {
  id: string;
  tenantId: string;
  goalId: string | null;
  agentId: string | null;
  stepId: string;
  requestPayload: Record<string, unknown>;
  riskLevel: 'low' | 'medium' | 'high' | 'critical';
  status: 'pending' | 'approved' | 'denied' | 'escalated' | 'timed_out' | 'auto_approved' | 'auto_denied';
  slaConfigId: string | null;
  slaDeadline: string | null;
  escalatedAt: string | null;
  resolvedBy: string | null;
  resolvedAt: string | null;
  resolutionNote: string | null;
  createdAt: string;
  // Computed
  minutesRemaining: number | null;
  isOverdue: boolean;
}

export interface ApprovalSLAConfig {
  id: string;
  name: string;
  riskLevel: string;
  responseSlaMinutes: number;
  escalationSlaMinutes: number;
  escalationRoles: string[];
  escalationChannels: EscalationChannel[];
  autoApproveOnTimeout: boolean;
  autoDenyOnTimeout: boolean;
  timeoutMinutes: number;
  businessHoursOnly: boolean;
  timezone: string;
}

export interface EscalationChannel {
  type: 'slack' | 'pagerduty' | 'email' | 'webhook';
  config: Record<string, string>;
}

export interface SLACountdownProps {
  deadline: string;
  createdAt: string;
  riskLevel: string;
  onExpired: () => void;
}
```

### 4.3 SLA Countdown Timer Component

```typescript
// src/features/governance/components/SLACountdown.tsx
import React, { useEffect, useState } from 'react';

export const SLACountdown: React.FC<SLACountdownProps> = ({
  deadline, riskLevel, onExpired,
}) => {
  const [secondsLeft, setSecondsLeft] = useState(() =>
    Math.max(0, Math.floor((new Date(deadline).getTime() - Date.now()) / 1000))
  );

  useEffect(() => {
    if (secondsLeft <= 0) { onExpired(); return; }
    const id = setInterval(() => {
      setSecondsLeft(prev => {
        if (prev <= 1) { onExpired(); clearInterval(id); return 0; }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(id);
  }, [deadline, onExpired]);

  const hours = Math.floor(secondsLeft / 3600);
  const mins  = Math.floor((secondsLeft % 3600) / 60);
  const secs  = secondsLeft % 60;

  const urgencyClass = secondsLeft < 300 ? 'countdown--urgent'
                     : secondsLeft < 1800 ? 'countdown--warning'
                     : 'countdown--normal';

  return (
    <span
      className={`sla-countdown ${urgencyClass}`}
      role="timer"
      aria-live="polite"
      aria-label={`${hours}h ${mins}m ${secs}s remaining`}
    >
      {hours > 0 && <span className="countdown-unit">{hours}h </span>}
      <span className="countdown-unit">{String(mins).padStart(2, '0')}m </span>
      <span className="countdown-unit">{String(secs).padStart(2, '0')}s</span>
    </span>
  );
};
```

### 4.4 Animation Specs

```css
/* src/features/governance/governance-animations.css */

/* Approval request card slide-in */
@keyframes approvalCardSlideIn {
  from { opacity: 0; transform: translateY(-8px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* Urgent countdown pulse */
@keyframes urgentCountdownPulse {
  0%, 100% { color: var(--color-danger-emphasis); }
  50%       { color: var(--color-danger-subtle); opacity: 0.7; }
}

/* Policy version diff highlight */
@keyframes diffLineHighlight {
  from { background-color: var(--color-accent-subtle); }
  to   { background-color: transparent; }
}

/* SLA bar drain */
@keyframes slaBarDrain {
  from { width: var(--sla-start-pct); }
  to   { width: 0%; }
}

/* Approval resolved checkmark */
@keyframes approvedCheckmark {
  0%   { transform: scale(0) rotate(-45deg); opacity: 0; }
  60%  { transform: scale(1.2) rotate(5deg); opacity: 1; }
  100% { transform: scale(1) rotate(0deg); opacity: 1; }
}

/* Denied X mark */
@keyframes deniedMark {
  0%   { transform: scale(0); opacity: 0; }
  50%  { transform: scale(1.15); opacity: 1; }
  100% { transform: scale(1); opacity: 1; }
}

/* Policy version history timeline */
@keyframes timelineReveal {
  from { height: 0; opacity: 0; }
  to   { height: var(--timeline-height); opacity: 1; }
}

.approval-card            { animation: approvalCardSlideIn 0.25s ease-out both; }
.countdown--urgent        { animation: urgentCountdownPulse 1s ease-in-out infinite; }
.diff-line-added          { animation: diffLineHighlight 2s ease-out both; }
.approval-resolved--ok    { animation: approvedCheckmark 0.35s cubic-bezier(0.175, 0.885, 0.32, 1.275) both; }
.approval-resolved--deny  { animation: deniedMark 0.25s ease-out both; }
.timeline-item            { animation: timelineReveal 0.3s ease-out both; }
```

### 4.5 Empty / Error States

```typescript
// No pending approvals
export const EmptyApprovalsState = () => (
  <div className="empty-state" role="status">
    <CheckCircleIcon size={48} className="empty-state__icon" aria-hidden />
    <h3>All caught up!</h3>
    <p>No pending approvals. Agents are running autonomously within policy.</p>
  </div>
);

// No policies configured
export const EmptyPoliciesState: React.FC<{ onCreate: () => void; onImport: () => void }> = ({
  onCreate, onImport,
}) => (
  <div className="empty-state" role="status">
    <ShieldIcon size={48} aria-hidden />
    <h3>No governance policies</h3>
    <p>Agents will run without policy constraints. Add policies to control which actions require approval.</p>
    <div className="empty-state__actions">
      <Button variant="primary" onClick={onCreate}>Create Policy</Button>
      <Button variant="secondary" onClick={onImport}>Import Domain Template</Button>
    </div>
  </div>
);
```

### 4.6 Dark Mode & Mobile

```css
.approval-card    { background: var(--color-surface-1); border: 1px solid var(--color-border-default); }
.sla-bar-track    { background: var(--color-border-muted); }
.sla-bar-fill     { background: var(--color-success-emphasis); }
.sla-bar-fill--warning { background: var(--color-warning-emphasis); }
.sla-bar-fill--urgent  { background: var(--color-danger-emphasis); }

@media (max-width: 640px) {
  .approvals-split { flex-direction: column; }
  .policy-diff     { font-size: var(--font-size-xs); overflow-x: auto; }
  .batch-toolbar   { position: sticky; bottom: 0; background: var(--color-surface-1); z-index: 10; }
}
```

---

## 5. Scale Architecture

**Target:** 10 M HITL requests/day across 500 k tenants

| Component | Bottleneck | Solution |
|-----------|-----------|----------|
| HITL waiter | `asyncio.Event` crashes on restart | Redis Pub/Sub: waiter subscribes to per-request channel, any replica unblocks it |
| Approval fan-out | Notify N approvers | Redis PUBLISH to `hitl:pending:{tenant_id}`; all connected UIs receive via SSE |
| SLA enforcement | Scheduled sweep accuracy | Celery beat task every 60s; Redis ZSET of {request_id: deadline_epoch} for O(log N) query |
| Policy evaluation | DB policy load per tool call | Policies cached in Redis per tenant, TTL=60s; invalidated on policy mutation |
| Semantic matching | LLM judge latency | Only invoked when structural match is inconclusive; async, non-blocking; rate-limited |
| Cross-region HITL | Approval on region A, waiter on region B | Redis Cluster with cross-region replication; channel message arrives on any subscriber |

---

## 6. Testing Strategy

```python
# agent-verse-backend/tests/governance/test_hitl_redis.py
"""
Tests for Redis Pub/Sub HITL gateway.
Tests cross-replica behavior using a real Redis mock and fake pub/sub.
"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.governance.hitl_redis import HITLGateway, SemanticPolicyEngine, PolicyVersionManager


# ---- Fixtures ---------------------------------------------------------------

class FakeRedisPubSub:
    """Simulates Redis pub/sub with in-process message delivery for tests."""

    def __init__(self):
        self._channels: dict[str, list] = {}
        self._subscribers: dict[str, list] = {}
        self._messages: dict[str, asyncio.Queue] = {}

    async def publish(self, channel: str, message: str) -> None:
        if channel not in self._messages:
            return
        await self._messages[channel].put({"type": "message", "data": message})

    def pubsub(self):
        return FakePubSubClient(self)

    async def get(self, key):
        return None

    async def setex(self, key, ttl, value):
        pass

    async def execute(self, *args):
        pass


class FakePubSubClient:
    def __init__(self, redis: FakeRedisPubSub):
        self._redis = redis
        self._channel: str | None = None

    async def subscribe(self, channel: str):
        self._channel = channel
        if channel not in self._redis._messages:
            self._redis._messages[channel] = asyncio.Queue()

    async def get_message(self, ignore_subscribe_messages=True, timeout=1.0):
        try:
            return await asyncio.wait_for(
                self._redis._messages[self._channel].get(), timeout=timeout
            )
        except asyncio.TimeoutError:
            return None

    async def unsubscribe(self, channel):
        pass

    async def aclose(self):
        pass


@pytest.fixture
def fake_redis():
    return FakeRedisPubSub()


@pytest.fixture
def mock_db_factory():
    db = AsyncMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=None)
    db.execute = AsyncMock(return_value=MagicMock(fetchone=lambda: None))
    db.commit = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return lambda: db


# ---- HITLGateway -----------------------------------------------------------

class TestHITLGateway:
    @pytest.mark.asyncio
    async def test_approval_unblocks_waiter(self, fake_redis, mock_db_factory):
        """
        Simulates: agent requests approval on one 'process',
        approver resolves it via publish_resolution on another 'process'.
        """
        gateway = HITLGateway(fake_redis, mock_db_factory)
        request_id = str(uuid4())

        # Schedule approval after a short delay
        async def delayed_approval():
            await asyncio.sleep(0.1)
            await fake_redis.publish(
                f"hitl:result:{request_id}",
                json.dumps({"approved": True, "note": "LGTM", "resolved_by": "user-1"}),
            )

        asyncio.create_task(delayed_approval())

        # Wait for result
        result = await gateway._wait_for_result(request_id, timeout_seconds=5)

        assert result is not None
        assert result["approved"] is True
        assert result["note"] == "LGTM"

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self, fake_redis, mock_db_factory):
        gateway = HITLGateway(fake_redis, mock_db_factory)
        request_id = str(uuid4())

        # No approval published — should timeout quickly
        result = await gateway._wait_for_result(request_id, timeout_seconds=0.3)
        assert result is None

    @pytest.mark.asyncio
    async def test_denial_propagated(self, fake_redis, mock_db_factory):
        gateway = HITLGateway(fake_redis, mock_db_factory)
        request_id = str(uuid4())

        async def delayed_deny():
            await asyncio.sleep(0.05)
            await fake_redis.publish(
                f"hitl:result:{request_id}",
                json.dumps({"approved": False, "note": "Risk too high", "resolved_by": "cro"}),
            )

        asyncio.create_task(delayed_deny())
        result = await gateway._wait_for_result(request_id, timeout_seconds=5)

        assert result["approved"] is False
        assert result["note"] == "Risk too high"


# ---- SemanticPolicyEngine --------------------------------------------------

class TestSemanticPolicyEngine:
    @pytest.mark.asyncio
    async def test_structural_match_gt_condition(self):
        policies = [{
            "id": str(uuid4()),
            "rules": [{
                "tool_name_pattern": "*transfer*",
                "condition": {"field": "amount", "operator": "gt", "value": 10000},
                "action": "hitl",
            }]
        }]
        engine = SemanticPolicyEngine("tenant-1", policies)
        allowed, policy_id, action = await engine.evaluate(
            "bank_transfer", {"amount": 15000}
        )
        assert allowed is False
        assert action == "hitl"

    @pytest.mark.asyncio
    async def test_below_threshold_allowed(self):
        policies = [{
            "id": str(uuid4()),
            "rules": [{
                "tool_name_pattern": "*transfer*",
                "condition": {"field": "amount", "operator": "gt", "value": 10000},
                "action": "hitl",
            }]
        }]
        engine = SemanticPolicyEngine("tenant-1", policies)
        allowed, policy_id, action = await engine.evaluate(
            "bank_transfer", {"amount": 5000}
        )
        assert allowed is True

    @pytest.mark.asyncio
    async def test_no_policies_always_allow(self):
        engine = SemanticPolicyEngine("tenant-1", [])
        allowed, policy_id, action = await engine.evaluate(
            "send_email", {"to": "user@example.com", "body": "Hello"}
        )
        assert allowed is True
        assert action == "allow"

    @pytest.mark.asyncio
    async def test_deny_action_blocks(self):
        policies = [{
            "id": str(uuid4()),
            "rules": [{
                "tool_name_pattern": "*override*",
                "action": "deny",
            }]
        }]
        engine = SemanticPolicyEngine("tenant-1", policies)
        allowed, _, action = await engine.evaluate("financial_override", {})
        assert allowed is False
        assert action == "deny"

    def test_nested_field_path_resolution(self):
        engine = SemanticPolicyEngine("t1", [])
        result = engine._get_nested({"nested": {"deep": {"value": 42}}}, "nested.deep.value")
        assert result == 42

    def test_condition_operators(self):
        engine = SemanticPolicyEngine("t1", [])
        assert engine._evaluate_condition(15000, "gt", 10000) is True
        assert engine._evaluate_condition(5000, "gt", 10000) is False
        assert engine._evaluate_condition(10000, "gte", 10000) is True
        assert engine._evaluate_condition("hello world", "contains", "world") is True
        assert engine._evaluate_condition("foo", "in", ["foo", "bar"]) is True


# ---- PolicyVersionManager --------------------------------------------------

class TestPolicyVersionManager:
    @pytest.mark.asyncio
    async def test_create_policy_version_1(self, mock_db_factory):
        mgr = PolicyVersionManager()
        db = mock_db_factory()
        result = await mgr.create_policy(
            db(),
            tenant_id=str(uuid4()),
            name="Test Policy",
            rules=[{"type": "tool_call", "action": "hitl"}],
            change_summary="Initial",
        )
        assert result["version_number"] == 1

    @pytest.mark.asyncio
    async def test_domain_templates_present(self):
        from app.governance.hitl_redis import DOMAIN_GOVERNANCE_TEMPLATES
        assert "hipaa" in DOMAIN_GOVERNANCE_TEMPLATES
        assert "sox" in DOMAIN_GOVERNANCE_TEMPLATES
        assert "gdpr" in DOMAIN_GOVERNANCE_TEMPLATES
        assert "legal" in DOMAIN_GOVERNANCE_TEMPLATES

    def test_evaluate_condition_type_error_returns_false(self):
        engine = SemanticPolicyEngine("t1", [])
        result = engine._evaluate_condition("not_a_number", "gt", 1000)
        assert result is False
```

---

## 7. Domain Extensibility

### Healthcare (HIPAA)
```python
# HIPAA-specific policies:
# 1. Minimum necessary rule: deny any single query returning >100 PHI records
# 2. Access without authorization: HITL on any PHI export
# 3. Workforce training gate: deny operations for users whose HIPAA training has expired
#    (check user.hipaa_training_expires_at < now())
# SLA config: response_sla=30min for critical, auto_deny_on_timeout=True
```

### Legal
```python
# Matter conflict checks:
#   Policy: deny tool calls where args.matter_id matches an adverse-party matter for the user
# Court filing rules:
#   HITL on any file_document(court=True) — requires partner review
# Privilege review:
#   Semantic policy: "any disclosure of privileged communication to third party"
#   action: deny + alert
```

### Finance (SOX)
```python
# Segregation of duties enforcement via policy:
#   deny: same user who created a journal entry also approves it
#   Achieved via condition: args.created_by == request.user_id + action=deny
# Pre-clearance for trades:
#   HITL on all trades during earnings blackout periods
#   (check against blackout_periods table)
# Dollar-threshold escalation ladder:
#   $10k → 1 approver, $100k → 2 approvers, $1M → CFO
```

### Education
```python
# Age-appropriate content gate:
#   If tenant.student_age_group == 'K-12': hitl on any content generation tool
# Exam integrity: deny tool calls when active_exam=True for the course
# Parent notification: HITL + notify_parent on any discipline-related goal execution
```

### E-commerce
```python
# Inventory protection: deny bulk_delete_products without dual approval
# Pricing controls: HITL on price changes > 20% for any SKU
# Refund limits: HITL on refunds > $500 or >5 items in 24h for one customer
```
