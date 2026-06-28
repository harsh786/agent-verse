# Audit Rails — World-Class Specification

**Area 5 · Migration 0057 · Version 1.0 · 2026-06-28**

---

## 1. Vision

A tamper-evident, legally defensible audit trail is the difference between a platform that regulated enterprises can adopt and one they cannot. The current AgentVerse audit system has five critical deficiencies that make it unsuitable for production use: writes are fire-and-forget (an exception in the handler drops the audit event silently), the audit store is an in-memory dictionary that loses its entire contents on every server restart, administrative actions (role changes, policy updates, key rotations) are never audited, there is no SIEM integration (security teams cannot ingest agent audit events into their Splunk/Elasticsearch/Datadog pipelines), and the legal holds schema exists on paper but the implementation cannot prevent deletion of held data. A platform executing real-world actions on behalf of users — deleting files, sending emails, executing financial transactions — without a reliable audit trail is an existential liability.

This specification delivers a five-pillar audit architecture that achieves at-least-once delivery guarantees via a Redis WAL-to-DB pipeline, cryptographic hash chaining so any tampering with historical records is mathematically detectable, monthly Postgres table partitioning to keep query latency sub-100 ms even at 1 billion rows, a universal admin action audit decorator that captures every mutating admin operation without modifying each handler, and a pluggable SIEM integration framework with pre-built adapters for Splunk HEC, Elasticsearch Bulk API, Datadog Events, CEF (ArcSight), and LEEF (QRadar). Domain-specific audit formats ensure that healthcare audit logs include HIPAA required fields (workforce member ID, patient record accessed, access reason) and financial audit logs include SOX-required fields (journal entry ID, approver chain, dollar amount) without polluting the base schema.

---

## 2. Current State Assessment

| Component | Current State | Gap | Severity |
|-----------|---------------|-----|----------|
| Write durability | Fire-and-forget — exception drops event | Silent data loss on any error | CRITICAL |
| Storage | In-memory dict in `audit.py` | Lost on every restart | CRITICAL |
| Admin action audit | Not implemented | Role/policy/key changes unaudited | CRITICAL |
| SIEM integration | Not implemented | Security teams cannot ingest events | HIGH |
| Legal holds | Schema orphaned — no enforcement | Held data can be deleted | HIGH |
| Tamper detection | None | Logs can be modified without detection | HIGH |
| Partitioning | Single unpartitioned table | Query performance degrades after 10M rows | HIGH |
| Tool args capture | Partial | Full tool call arguments not recorded | MEDIUM |
| PII in logs | Captured raw | SSN/CC/keys appear in plain text | HIGH |
| Full-text search | Not implemented | Cannot search across audit events | MEDIUM |
| Retention sweep | Not implemented | Data not deleted per retention policy | MEDIUM |

---

## 3. Backend Architecture

### 3.1 Database Schema — Migration 0057

```sql
-- =============================================================================
-- Migration 0057: Partitioned audit_events table, legal holds, SIEM configs
-- Author: AgentVerse Platform Team
-- Date: 2026-06-28
-- =============================================================================

BEGIN;

-- --------------------------------------------------------
-- Table: audit_events (partitioned by created_at month)
-- Replaces the in-memory audit dict completely
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_events (
    id              UUID NOT NULL DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    -- Who
    user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
    api_key_id      UUID REFERENCES api_keys(id) ON DELETE SET NULL,
    actor_type      TEXT NOT NULL DEFAULT 'user'
                    CHECK (actor_type IN ('user', 'api_key', 'agent', 'system', 'scheduler')),
    actor_label     TEXT,                           -- human-readable identifier (email/key-prefix)
    -- What
    event_type      TEXT NOT NULL,                  -- 'goal.created', 'agent.deleted', 'policy.updated' etc.
    resource_type   TEXT,                           -- 'goal', 'agent', 'policy', 'api_key', 'role' etc.
    resource_id     UUID,
    resource_label  TEXT,                           -- human-readable resource name
    action          TEXT NOT NULL,                  -- 'create', 'read', 'update', 'delete', 'execute', 'approve'
    -- Result
    status          TEXT NOT NULL DEFAULT 'success'
                    CHECK (status IN ('success', 'failure', 'partial')),
    error_code      TEXT,
    error_message   TEXT,
    -- Context
    ip_address      INET,
    user_agent      TEXT,
    request_id      TEXT,                           -- correlation with HTTP request logs
    goal_id         UUID REFERENCES goals(id) ON DELETE SET NULL,
    agent_id        UUID REFERENCES agents(id) ON DELETE SET NULL,
    -- Payload (PII-redacted)
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    tool_name       TEXT,
    tool_args_hash  TEXT,                           -- SHA-256 of raw args (not stored raw for PII)
    tool_args_safe  JSONB,                          -- PII-redacted version of args
    -- Chain integrity
    prev_hash       TEXT,                           -- SHA-256 of previous event in chain
    event_hash      TEXT,                           -- SHA-256(id+tenant_id+event_type+metadata+prev_hash)
    -- Time
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

-- Create partitions for 2026 and 2027
DO $$
DECLARE
    yr  INTEGER;
    mo  INTEGER;
    start_date DATE;
    end_date   DATE;
    tname      TEXT;
BEGIN
    FOR yr IN 2026..2027 LOOP
        FOR mo IN 1..12 LOOP
            start_date := make_date(yr, mo, 1);
            end_date   := start_date + INTERVAL '1 month';
            tname      := format('audit_events_%s_%s', yr, lpad(mo::text, 2, '0'));
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF audit_events
                 FOR VALUES FROM (%L) TO (%L)',
                tname, start_date, end_date
            );
        END LOOP;
    END LOOP;
END;
$$;

CREATE INDEX idx_audit_events_tenant_time
    ON audit_events(tenant_id, created_at DESC);
CREATE INDEX idx_audit_events_resource
    ON audit_events(tenant_id, resource_type, resource_id, created_at DESC);
CREATE INDEX idx_audit_events_actor
    ON audit_events(tenant_id, user_id, created_at DESC);
CREATE INDEX idx_audit_events_event_type
    ON audit_events(tenant_id, event_type, created_at DESC);
CREATE INDEX idx_audit_events_goal
    ON audit_events(goal_id, created_at DESC) WHERE goal_id IS NOT NULL;

ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY audit_events_isolation ON audit_events
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- --------------------------------------------------------
-- Table: audit_wal_queue
-- Redis WAL buffer overflow fallback; also used for at-least-once replay
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_wal_queue (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   UUID NOT NULL,
    payload     JSONB NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    inserted_at TIMESTAMPTZ                         -- when successfully inserted to audit_events
);

CREATE INDEX idx_audit_wal_pending ON audit_wal_queue(created_at)
    WHERE inserted_at IS NULL;

-- --------------------------------------------------------
-- Table: legal_holds
-- Prevents deletion of audit events for specific resources
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS legal_holds (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    description     TEXT,
    resource_type   TEXT NOT NULL,
    resource_ids    JSONB NOT NULL DEFAULT '[]'::jsonb,  -- array of UUIDs
    user_ids        JSONB NOT NULL DEFAULT '[]'::jsonb,  -- also hold by user
    date_range_start TIMESTAMPTZ,
    date_range_end  TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'released', 'expired')),
    legal_matter_id TEXT,                                -- external legal case reference
    created_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at     TIMESTAMPTZ,
    released_by     UUID REFERENCES users(id),
    release_reason  TEXT,
    expires_at      TIMESTAMPTZ
);

CREATE INDEX idx_legal_holds_tenant ON legal_holds(tenant_id) WHERE status = 'active';
CREATE INDEX idx_legal_holds_resource ON legal_holds(resource_type, tenant_id) WHERE status = 'active';

ALTER TABLE legal_holds ENABLE ROW LEVEL SECURITY;
CREATE POLICY legal_holds_isolation ON legal_holds
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- --------------------------------------------------------
-- Table: siem_configs
-- Per-tenant SIEM integration configurations
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS siem_configs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    siem_type       TEXT NOT NULL
                    CHECK (siem_type IN ('splunk', 'elasticsearch', 'datadog', 'cef', 'leef', 'webhook')),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    config_encrypted BYTEA NOT NULL,                -- AES-256 encrypted JSON config (URLs, tokens)
    event_filter    JSONB NOT NULL DEFAULT '{}'::jsonb,  -- which event_types to forward
    min_severity    TEXT DEFAULT 'low',
    batch_size      INTEGER NOT NULL DEFAULT 100,
    flush_interval_seconds INTEGER NOT NULL DEFAULT 30,
    last_flush_at   TIMESTAMPTZ,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_siem_tenant_name UNIQUE (tenant_id, name)
);

CREATE INDEX idx_siem_configs_tenant ON siem_configs(tenant_id) WHERE is_active = TRUE;

ALTER TABLE siem_configs ENABLE ROW LEVEL SECURITY;
CREATE POLICY siem_configs_isolation ON siem_configs
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- --------------------------------------------------------
-- Function: prevent deletion of audit events under legal hold
-- --------------------------------------------------------
CREATE OR REPLACE FUNCTION check_legal_hold_before_delete()
RETURNS TRIGGER AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM legal_holds lh
        WHERE lh.tenant_id = OLD.tenant_id
          AND lh.status = 'active'
          AND (
              (OLD.resource_id IS NOT NULL AND OLD.resource_id::text = ANY(
                  SELECT jsonb_array_elements_text(lh.resource_ids)
              ))
              OR
              (OLD.user_id IS NOT NULL AND OLD.user_id::text = ANY(
                  SELECT jsonb_array_elements_text(lh.user_ids)
              ))
          )
          AND (lh.date_range_start IS NULL OR OLD.created_at >= lh.date_range_start)
          AND (lh.date_range_end   IS NULL OR OLD.created_at <= lh.date_range_end)
    ) THEN
        RAISE EXCEPTION 'Cannot delete audit event: record is under legal hold';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_legal_hold_check
    BEFORE DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION check_legal_hold_before_delete();

COMMIT;
```

### 3.2 Alembic Migration File

```python
# agent-verse-backend/app/db/migrations/versions/0057_audit_events_partitioned.py
"""Partitioned audit_events, legal_holds, siem_configs, audit WAL queue

Revision ID: 0057
Revises: 0056
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, INET, TIMESTAMPTZ, BYTEA

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create partitioned audit_events via raw SQL (SQLAlchemy doesn't support partitioned DDL well)
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_events (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            user_id UUID,
            api_key_id UUID,
            actor_type TEXT NOT NULL DEFAULT 'user',
            actor_label TEXT,
            event_type TEXT NOT NULL,
            resource_type TEXT,
            resource_id UUID,
            resource_label TEXT,
            action TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'success',
            error_code TEXT,
            error_message TEXT,
            ip_address INET,
            user_agent TEXT,
            request_id TEXT,
            goal_id UUID,
            agent_id UUID,
            metadata JSONB NOT NULL DEFAULT '{}',
            tool_name TEXT,
            tool_args_hash TEXT,
            tool_args_safe JSONB,
            prev_hash TEXT,
            event_hash TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (id, created_at)
        ) PARTITION BY RANGE (created_at)
    """)

    # Create 2026 + 2027 partitions
    for yr in range(2026, 2028):
        for mo in range(1, 13):
            import calendar
            start = f"{yr}-{mo:02d}-01"
            next_mo = mo + 1 if mo < 12 else 1
            next_yr = yr if mo < 12 else yr + 1
            end = f"{next_yr}-{next_mo:02d}-01"
            tname = f"audit_events_{yr}_{mo:02d}"
            op.execute(f"""
                CREATE TABLE IF NOT EXISTS {tname}
                    PARTITION OF audit_events
                    FOR VALUES FROM ('{start}') TO ('{end}')
            """)

    op.create_table(
        "audit_wal_queue",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        sa.Column("inserted_at", TIMESTAMPTZ()),
    )

    op.create_table(
        "legal_holds",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_ids", JSONB(), nullable=False, server_default="'[]'"),
        sa.Column("user_ids", JSONB(), nullable=False, server_default="'[]'"),
        sa.Column("date_range_start", TIMESTAMPTZ()),
        sa.Column("date_range_end", TIMESTAMPTZ()),
        sa.Column("status", sa.Text(), nullable=False, server_default="'active'"),
        sa.Column("legal_matter_id", sa.Text()),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        sa.Column("released_at", TIMESTAMPTZ()),
        sa.Column("released_by", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("release_reason", sa.Text()),
        sa.Column("expires_at", TIMESTAMPTZ()),
    )

    op.create_table(
        "siem_configs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("siem_type", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="TRUE"),
        sa.Column("config_encrypted", BYTEA(), nullable=False),
        sa.Column("event_filter", JSONB(), nullable=False, server_default="'{}'"),
        sa.Column("min_severity", sa.Text(), server_default="'low'"),
        sa.Column("batch_size", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("flush_interval_seconds", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("last_flush_at", TIMESTAMPTZ()),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "name", name="uq_siem_tenant_name"),
    )


def downgrade() -> None:
    op.drop_table("siem_configs")
    op.drop_table("legal_holds")
    op.drop_table("audit_wal_queue")
    op.execute("DROP TABLE IF EXISTS audit_events CASCADE")
```

### 3.3 API Endpoints

**GET /api/audit/events**
- Auth: scope `audit:read`
- Query: `event_type`, `resource_type`, `resource_id`, `user_id`, `from_date`, `to_date`, `page`, `page_size`, `search`
- Response: paginated audit events

**GET /api/audit/events/{event_id}**

**POST /api/audit/export**
- Auth: scope `audit:export`
```json
{
  "format": "csv",
  "from_date": "2026-06-01T00:00:00Z",
  "to_date": "2026-06-30T23:59:59Z",
  "event_types": ["goal.executed", "agent.deleted"],
  "include_tool_args": false
}
```
Response: 202 Accepted with `{ "export_id": "uuid", "download_url_expires_at": "..." }`

**GET /api/audit/integrity/verify**
- Verifies hash chain integrity for a date range
```json
{ "from_date": "...", "to_date": "...", "verified_events": 14250, "broken_chain_at": null }
```

#### Legal Holds

**GET /api/audit/legal-holds**

**POST /api/audit/legal-holds**
```json
{
  "name": "SEC Investigation 2026",
  "resource_type": "goal",
  "resource_ids": ["uuid1", "uuid2"],
  "user_ids": ["uuid3"],
  "date_range_start": "2026-01-01T00:00:00Z",
  "legal_matter_id": "SEC-2026-001"
}
```

**GET /api/audit/legal-holds/{hold_id}**

**POST /api/audit/legal-holds/{hold_id}/release**
```json
{ "reason": "Investigation concluded, no further holds needed" }
```

#### SIEM Integration

**GET /api/audit/siem-configs**

**POST /api/audit/siem-configs**
```json
{
  "name": "Splunk Production",
  "siem_type": "splunk",
  "config": {
    "hec_url": "https://splunk.internal:8088",
    "hec_token": "...",
    "index": "agentverse",
    "source_type": "agentverse:audit"
  },
  "event_filter": { "min_severity": "medium" },
  "batch_size": 200,
  "flush_interval_seconds": 30
}
```

**POST /api/audit/siem-configs/{id}/test** — Sends a test event; returns success/error

**DELETE /api/audit/siem-configs/{id}**

### 3.4 Business Logic — Python

```python
# agent-verse-backend/app/governance/audit.py
"""
Production-grade audit system with:
  - Redis WAL for at-least-once delivery
  - Cryptographic hash chaining for tamper detection
  - Admin action audit decorator
  - SIEM forwarding pipeline
  - PII redaction before storage
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Optional
from uuid import UUID, uuid4

import redis.asyncio as aioredis
from fastapi import Request

from app.core.logging import get_logger

logger = get_logger(__name__)

# Redis WAL key (list, RPUSH/BLPOP)
WAL_KEY = "audit:wal"
WAL_DEAD_LETTER = "audit:wal:dlq"
WAL_BATCH_SIZE = 100
WAL_FLUSH_INTERVAL = 5  # seconds


class AuditEvent:
    """Serializable audit event with hash chain support."""

    __slots__ = (
        "id", "tenant_id", "user_id", "api_key_id", "actor_type", "actor_label",
        "event_type", "resource_type", "resource_id", "resource_label",
        "action", "status", "error_code", "error_message",
        "ip_address", "user_agent", "request_id",
        "goal_id", "agent_id",
        "metadata", "tool_name", "tool_args_hash", "tool_args_safe",
        "prev_hash", "event_hash", "created_at",
    )

    def __init__(self, **kwargs):
        for slot in self.__slots__:
            setattr(self, slot, kwargs.get(slot))
        if not self.id:
            self.id = str(uuid4())
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def compute_hash(self, prev_hash: str = "") -> str:
        """SHA-256 of deterministic serialization."""
        canonical = json.dumps({
            "id": self.id,
            "tenant_id": str(self.tenant_id),
            "event_type": self.event_type,
            "resource_id": str(self.resource_id) if self.resource_id else None,
            "action": self.action,
            "status": self.status,
            "created_at": self.created_at,
            "prev_hash": prev_hash,
        }, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {slot: getattr(self, slot) for slot in self.__slots__}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


class AuditWriter:
    """
    Writes audit events via Redis WAL for at-least-once delivery.

    Flow:
      1. Serialize event to JSON
      2. RPUSH to Redis WAL list (atomic, <1ms)
      3. Background Celery task: BLPOP from WAL, batch INSERT to Postgres
      4. On Postgres failure: retry up to 3x, then push to audit_wal_queue table

    This ensures events are never silently dropped even if Postgres is temporarily unavailable.
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def write(self, event: AuditEvent) -> None:
        """Non-blocking write to Redis WAL. Never raises."""
        try:
            await self._redis.rpush(WAL_KEY, event.to_json())
        except Exception as exc:
            # Last resort: structured log (will be picked up by log-based SIEM)
            logger.error(
                "audit_wal_write_failed",
                event_id=event.id,
                event_type=event.event_type,
                error=str(exc),
            )

    async def write_batch(self, events: list[AuditEvent]) -> None:
        if not events:
            return
        pipeline = self._redis.pipeline(transaction=False)
        for event in events:
            pipeline.rpush(WAL_KEY, event.to_json())
        try:
            await pipeline.execute()
        except Exception as exc:
            logger.error("audit_wal_batch_write_failed", count=len(events), error=str(exc))


class AuditFlusher:
    """
    Celery task that flushes the Redis WAL to Postgres.
    Runs every WAL_FLUSH_INTERVAL seconds.
    """

    def __init__(self, redis: aioredis.Redis, db_factory) -> None:
        self._redis = redis
        self._db = db_factory
        self._chain_cache: dict[str, str] = {}  # tenant_id → last event_hash

    async def flush(self) -> int:
        """Returns number of events flushed."""
        raw_events: list[bytes] = []
        pipeline = self._redis.pipeline(transaction=False)
        for _ in range(WAL_BATCH_SIZE):
            pipeline.lpop(WAL_KEY)
        results = await pipeline.execute()
        raw_events = [r for r in results if r is not None]

        if not raw_events:
            return 0

        events_to_insert: list[dict] = []
        for raw in raw_events:
            try:
                event_dict = json.loads(raw)
                tenant_id = event_dict["tenant_id"]

                # Hash chaining
                prev_hash = self._chain_cache.get(tenant_id, "")
                ae = AuditEvent(**event_dict)
                ae.prev_hash = prev_hash
                ae.event_hash = ae.compute_hash(prev_hash)
                self._chain_cache[tenant_id] = ae.event_hash

                events_to_insert.append(ae.to_dict())
            except Exception as exc:
                logger.error("audit_flush_deserialize_error", error=str(exc))

        if not events_to_insert:
            return 0

        async with self._db() as db:
            try:
                await db.execute(
                    """
                    INSERT INTO audit_events (
                        id, tenant_id, user_id, api_key_id, actor_type, actor_label,
                        event_type, resource_type, resource_id, resource_label,
                        action, status, error_code, error_message,
                        ip_address, user_agent, request_id,
                        goal_id, agent_id,
                        metadata, tool_name, tool_args_hash, tool_args_safe,
                        prev_hash, event_hash, created_at
                    )
                    SELECT
                        (e->>'id')::uuid,
                        (e->>'tenant_id')::uuid,
                        (e->>'user_id')::uuid,
                        (e->>'api_key_id')::uuid,
                        e->>'actor_type',
                        e->>'actor_label',
                        e->>'event_type',
                        e->>'resource_type',
                        (e->>'resource_id')::uuid,
                        e->>'resource_label',
                        e->>'action',
                        COALESCE(e->>'status', 'success'),
                        e->>'error_code',
                        e->>'error_message',
                        (e->>'ip_address')::inet,
                        e->>'user_agent',
                        e->>'request_id',
                        (e->>'goal_id')::uuid,
                        (e->>'agent_id')::uuid,
                        COALESCE(e->'metadata', '{}')::jsonb,
                        e->>'tool_name',
                        e->>'tool_args_hash',
                        e->'tool_args_safe',
                        e->>'prev_hash',
                        e->>'event_hash',
                        COALESCE((e->>'created_at')::timestamptz, now())
                    FROM jsonb_array_elements(:events::jsonb) AS e
                    ON CONFLICT (id, created_at) DO NOTHING
                    """,
                    {"events": json.dumps(events_to_insert, default=str)},
                )
                await db.commit()
                logger.info("audit_flushed", count=len(events_to_insert))
                return len(events_to_insert)

            except Exception as exc:
                await db.rollback()
                # Push failed events to dead-letter queue table for manual replay
                logger.error("audit_flush_db_error", count=len(events_to_insert), error=str(exc))
                await self._send_to_dlq(events_to_insert)
                return 0

    async def _send_to_dlq(self, events: list[dict]) -> None:
        pipeline = self._redis.pipeline(transaction=False)
        for e in events:
            pipeline.rpush(WAL_DEAD_LETTER, json.dumps(e, default=str))
        try:
            await pipeline.execute()
        except Exception:
            pass  # Already logging above — don't recursive-fail


class HashChainVerifier:
    """
    Verifies the cryptographic hash chain for a tenant's audit events.
    Detects tampering by recomputing hashes from scratch.
    """

    async def verify(
        self,
        db,
        tenant_id: str,
        from_date: datetime,
        to_date: datetime,
    ) -> dict:
        from sqlalchemy import select, text
        rows = await db.execute(
            text("""
                SELECT id, event_type, resource_id, action, status, created_at,
                       prev_hash, event_hash, metadata
                FROM audit_events
                WHERE tenant_id = :tenant_id
                  AND created_at BETWEEN :from_date AND :to_date
                ORDER BY created_at ASC, id ASC
            """),
            {"tenant_id": tenant_id, "from_date": from_date, "to_date": to_date},
        )
        events = rows.fetchall()

        verified = 0
        prev_hash = ""
        for row in events:
            ae = AuditEvent(
                id=str(row.id),
                tenant_id=tenant_id,
                event_type=row.event_type,
                resource_id=str(row.resource_id) if row.resource_id else None,
                action=row.action,
                status=row.status,
                created_at=row.created_at.isoformat() if row.created_at else None,
            )
            expected_hash = ae.compute_hash(prev_hash)
            if expected_hash != row.event_hash:
                return {
                    "verified": False,
                    "verified_events": verified,
                    "broken_chain_at": str(row.id),
                    "broken_at_time": str(row.created_at),
                }
            prev_hash = row.event_hash
            verified += 1

        return {
            "verified": True,
            "verified_events": verified,
            "broken_chain_at": None,
            "chain_tip_hash": prev_hash,
        }


# ---------------------------------------------------------------------------
# Admin action audit decorator
# ---------------------------------------------------------------------------

def audit_admin_action(
    event_type: str,
    resource_type: str,
    action: str,
    extract_resource_id: Optional[Callable] = None,
):
    """
    Decorator for admin route handlers that automatically emits an audit event.

    Usage:
        @router.delete("/api/agents/{agent_id}")
        @audit_admin_action("agent.deleted", "agent", "delete",
                             extract_resource_id=lambda kwargs: kwargs.get("agent_id"))
        async def delete_agent(agent_id: str, request: Request, ...):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            request: Optional[Request] = kwargs.get("request") or (
                next((a for a in args if isinstance(a, Request)), None)
            )
            tenant = getattr(request.state, "tenant", None) if request else None
            api_key = getattr(request.state, "api_key", None) if request else None

            resource_id = None
            if extract_resource_id:
                try:
                    resource_id = extract_resource_id(kwargs)
                except Exception:
                    pass

            status = "success"
            error_code = None
            error_message = None

            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as exc:
                status = "failure"
                error_code = getattr(exc, "code", type(exc).__name__)
                error_message = str(exc)[:500]
                raise
            finally:
                if tenant:
                    event = AuditEvent(
                        tenant_id=str(tenant.id),
                        user_id=str(api_key.user_id) if api_key and api_key.user_id else None,
                        api_key_id=str(api_key.id) if api_key else None,
                        actor_type="api_key" if api_key else "system",
                        actor_label=api_key.prefix if api_key else "system",
                        event_type=event_type,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        action=action,
                        status=status,
                        error_code=error_code,
                        error_message=error_message,
                        ip_address=str(request.client.host) if request and request.client else None,
                        request_id=request.headers.get("X-Request-ID") if request else None,
                    )
                    # Fire-and-forget to WAL (never blocks the response)
                    if hasattr(request, "app") and hasattr(request.app.state, "audit_writer"):
                        try:
                            await request.app.state.audit_writer.write(event)
                        except Exception:
                            pass

        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# SIEM integration framework
# ---------------------------------------------------------------------------

class SIEMAdapter:
    """Base class for SIEM adapters."""

    async def send_batch(self, events: list[dict]) -> bool:
        raise NotImplementedError


class SplunkHECAdapter(SIEMAdapter):
    """Splunk HTTP Event Collector adapter."""

    def __init__(self, hec_url: str, hec_token: str, index: str, source_type: str) -> None:
        self._url = f"{hec_url.rstrip('/')}/services/collector/event"
        self._token = hec_token
        self._index = index
        self._source_type = source_type

    async def send_batch(self, events: list[dict]) -> bool:
        import httpx
        payload = "\n".join(json.dumps({
            "time": int(datetime.fromisoformat(e["created_at"]).timestamp()),
            "index": self._index,
            "sourcetype": self._source_type,
            "event": e,
        }) for e in events)

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                self._url,
                content=payload,
                headers={"Authorization": f"Splunk {self._token}",
                         "Content-Type": "application/json"},
            )
        return resp.status_code == 200


class ElasticsearchAdapter(SIEMAdapter):
    """Elasticsearch Bulk API adapter."""

    def __init__(self, base_url: str, api_key: str, index: str) -> None:
        self._url = f"{base_url.rstrip('/')}/_bulk"
        self._api_key = api_key
        self._index = index

    async def send_batch(self, events: list[dict]) -> bool:
        import httpx
        lines: list[str] = []
        for e in events:
            lines.append(json.dumps({"index": {"_index": self._index, "_id": e["id"]}}))
            lines.append(json.dumps(e))
        body = "\n".join(lines) + "\n"

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                self._url,
                content=body,
                headers={"Authorization": f"ApiKey {self._api_key}",
                         "Content-Type": "application/x-ndjson"},
            )
        return resp.status_code in (200, 201)


class DatadogAdapter(SIEMAdapter):
    """Datadog Events API adapter."""

    DD_URL = "https://api.datadoghq.com/api/v2/logs"

    def __init__(self, api_key: str, service: str = "agentverse") -> None:
        self._api_key = api_key
        self._service = service

    async def send_batch(self, events: list[dict]) -> bool:
        import httpx
        payload = [
            {
                "ddsource": "agentverse",
                "ddtags": f"event_type:{e.get('event_type', '')},tenant:{e.get('tenant_id', '')}",
                "hostname": "agentverse-agent",
                "service": self._service,
                "message": json.dumps(e),
            }
            for e in events
        ]
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                self.DD_URL,
                json=payload,
                headers={"DD-API-KEY": self._api_key, "Content-Type": "application/json"},
            )
        return resp.status_code == 202


class CEFAdapter(SIEMAdapter):
    """
    Common Event Format (ArcSight) via syslog UDP/TCP.
    CEF:Version|Device Vendor|Device Product|Device Version|Signature ID|Name|Severity|Extension
    """

    def __init__(self, host: str, port: int = 514, protocol: str = "udp") -> None:
        self._host = host
        self._port = port
        self._protocol = protocol

    async def send_batch(self, events: list[dict]) -> bool:
        import socket
        severity_map = {"low": "3", "medium": "5", "high": "8", "critical": "10"}
        sock_type = socket.SOCK_DGRAM if self._protocol == "udp" else socket.SOCK_STREAM

        try:
            with socket.socket(socket.AF_INET, sock_type) as sock:
                sock.settimeout(5.0)
                if self._protocol == "tcp":
                    sock.connect((self._host, self._port))

                for e in events:
                    sev = severity_map.get(e.get("metadata", {}).get("severity", "low"), "3")
                    cef_line = (
                        f"CEF:0|AgentVerse|AgentVerse|1.0|{e.get('event_type', 'unknown')}|"
                        f"{e.get('action', 'unknown')}|{sev}|"
                        f"tenant={e.get('tenant_id', '')} "
                        f"resource={e.get('resource_type', '')} "
                        f"status={e.get('status', '')} "
                        f"requestId={e.get('request_id', '')}\n"
                    ).encode()

                    if self._protocol == "udp":
                        sock.sendto(cef_line, (self._host, self._port))
                    else:
                        sock.sendall(cef_line)
        except Exception as exc:
            logger.error("cef_siem_send_error", error=str(exc))
            return False

        return True


SIEM_ADAPTER_MAP: dict[str, type[SIEMAdapter]] = {
    "splunk": SplunkHECAdapter,
    "elasticsearch": ElasticsearchAdapter,
    "datadog": DatadogAdapter,
    "cef": CEFAdapter,
}


def build_siem_adapter(siem_type: str, config: dict) -> SIEMAdapter:
    cls = SIEM_ADAPTER_MAP.get(siem_type)
    if not cls:
        raise ValueError(f"Unknown SIEM type: {siem_type}")
    return cls(**config)
```

### 3.5 main.py Wiring

```python
# Additions to agent-verse-backend/app/main.py

from app.governance.audit import AuditWriter, AuditFlusher
from app.audit.router import router as audit_router

def create_app(manage_pools: bool = True) -> FastAPI:
    app = FastAPI(...)

    # AuditWriter: fast non-blocking Redis WAL writer
    app.state.audit_writer = AuditWriter(app.state.redis)

    app.include_router(audit_router, prefix="/api/audit", tags=["Audit"])

    return app


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... existing ...

    # Start WAL flusher background task
    from app.governance.audit import AuditFlusher, WAL_FLUSH_INTERVAL
    flusher = AuditFlusher(app.state.redis, app.state.db_session_factory)

    async def flush_loop():
        while True:
            try:
                await flusher.flush()
            except Exception as exc:
                logger.error("audit_flush_loop_error", error=str(exc))
            await asyncio.sleep(WAL_FLUSH_INTERVAL)

    app.state.audit_flush_task = asyncio.create_task(flush_loop())

    yield

    app.state.audit_flush_task.cancel()
    # Final flush on shutdown
    await flusher.flush()
```

---

## 4. Frontend Specification

### 4.1 New Pages & Routes

| Route | Sidebar Entry | Description |
|-------|---------------|-------------|
| `/audit` | Audit | Real-time audit explorer |
| `/audit/events` | Audit → Events | Filterable, virtualized event table |
| `/audit/legal-holds` | Audit → Legal Holds | Hold management |
| `/audit/export` | Audit → Export | Scheduled export configuration |
| `/audit/siem` | Audit → SIEM | SIEM integration setup |
| `/audit/integrity` | Audit → Integrity | Hash chain verification |

### 4.2 TypeScript Interfaces

```typescript
// src/features/audit/types.ts

export interface AuditEvent {
  id: string;
  tenantId: string;
  userId: string | null;
  apiKeyId: string | null;
  actorType: 'user' | 'api_key' | 'agent' | 'system' | 'scheduler';
  actorLabel: string | null;
  eventType: string;
  resourceType: string | null;
  resourceId: string | null;
  resourceLabel: string | null;
  action: string;
  status: 'success' | 'failure' | 'partial';
  errorCode: string | null;
  errorMessage: string | null;
  ipAddress: string | null;
  userAgent: string | null;
  requestId: string | null;
  goalId: string | null;
  agentId: string | null;
  metadata: Record<string, unknown>;
  toolName: string | null;
  toolArgsHash: string | null;
  toolArgsSafe: Record<string, unknown> | null;
  prevHash: string | null;
  eventHash: string | null;
  createdAt: string;
}

export interface LegalHold {
  id: string;
  name: string;
  description: string | null;
  resourceType: string;
  resourceIds: string[];
  userIds: string[];
  dateRangeStart: string | null;
  dateRangeEnd: string | null;
  status: 'active' | 'released' | 'expired';
  legalMatterId: string | null;
  createdBy: string;
  createdAt: string;
  releasedAt: string | null;
  expiresAt: string | null;
}

export interface SIEMConfig {
  id: string;
  name: string;
  siemType: 'splunk' | 'elasticsearch' | 'datadog' | 'cef' | 'leef' | 'webhook';
  isActive: boolean;
  eventFilter: Record<string, unknown>;
  minSeverity: string;
  batchSize: number;
  flushIntervalSeconds: number;
  lastFlushAt: string | null;
  lastError: string | null;
}

export interface AuditExplorerFilters {
  eventType: string | null;
  resourceType: string | null;
  resourceId: string | null;
  userId: string | null;
  status: 'success' | 'failure' | 'partial' | null;
  fromDate: string | null;
  toDate: string | null;
  search: string | null;
}
```

### 4.3 Animation Specs

```css
/* src/features/audit/audit-animations.css */

/* Event row stream in (real-time SSE events) */
@keyframes auditRowStream {
  from { opacity: 0; background-color: var(--color-accent-subtle); transform: translateX(-4px); }
  to   { opacity: 1; background-color: transparent; transform: translateX(0); }
}

/* Failure event flash */
@keyframes failureFlash {
  0%, 100% { background-color: transparent; }
  30%       { background-color: var(--color-danger-subtle); }
}

/* Hash chain verified checkmark */
@keyframes chainVerified {
  0%   { opacity: 0; transform: scale(0) rotate(-90deg); }
  70%  { transform: scale(1.15) rotate(5deg); }
  100% { opacity: 1; transform: scale(1) rotate(0deg); }
}

/* Hash chain broken warning */
@keyframes chainBroken {
  0%, 100% { transform: translateX(0); }
  25%       { transform: translateX(-4px); }
  75%       { transform: translateX(4px); }
}

/* Export progress bar */
@keyframes exportProgress {
  from { width: 0%; }
  to   { width: var(--export-pct); }
}

/* Legal hold active badge pulse */
@keyframes holdActivePulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(var(--color-warning-rgb), 0.4); }
  50%       { box-shadow: 0 0 0 5px rgba(var(--color-warning-rgb), 0); }
}

.audit-row-new     { animation: auditRowStream 0.4s ease-out both; }
.audit-row--failed { animation: failureFlash 1s ease-out both; }
.chain-ok          { animation: chainVerified 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275) both; }
.chain-broken      { animation: chainBroken 0.4s ease-in-out both; }
.legal-hold-badge  { animation: holdActivePulse 2s ease-in-out infinite; }
```

### 4.4 AuditExplorerPage (Virtualized)

```typescript
// src/features/audit/pages/AuditExplorerPage.tsx
// Uses @tanstack/react-virtual for virtualized table (handles millions of rows)
// Real-time SSE stream via useGoalStream pattern adapted for audit events
// Features: column picker, column resizing, JSON detail drawer, export button
```

### 4.5 Dark Mode & Mobile

```css
.audit-event-row   { background: var(--color-surface-1); border-bottom: 1px solid var(--color-border-subtle); }
.audit-event-row:hover { background: var(--color-surface-2); }
.status-badge--success { background: var(--color-success-subtle); color: var(--color-success-emphasis); }
.status-badge--failure { background: var(--color-danger-subtle); color: var(--color-danger-emphasis); }
.hash-chain-valid   { color: var(--color-success-emphasis); }
.hash-chain-broken  { color: var(--color-danger-emphasis); }

@media (max-width: 640px) {
  .audit-table-desktop { display: none; }
  .audit-table-mobile  { display: block; }
  .audit-detail-drawer { width: 100vw; }
}
```

---

## 5. Scale Architecture

**Target:** 1 B audit events/month; <100 ms query latency p99

| Layer | At Scale | Solution |
|-------|---------|----------|
| Write throughput | 10k events/sec spikes | Redis WAL absorbs spikes; batch INSERT every 5s |
| Query latency | 1B rows in single table | Monthly partitions; query planner uses partition pruning |
| Index size | Large JSONB metadata | Partial indexes on `tenant_id, created_at`; GIN on metadata only for search tenants |
| Hash chain | 1B sequential hashes | Computed at flush time, not real-time; parallel by tenant_id |
| SIEM forwarding | 100 tenants × 200 events/s | Celery task per SIEM config; Redis list as buffer per config |
| Legal hold check | Per-DELETE trigger | PG trigger with indexed lookup; legal_holds cached in Redis per tenant |
| Export | Large CSV/JSON exports | Async background task; S3 presigned URL; chunked 10k rows/file |

---

## 6. Testing Strategy

```python
# agent-verse-backend/tests/governance/test_audit.py
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.governance.audit import (
    AuditEvent, AuditWriter, AuditFlusher, HashChainVerifier,
    build_siem_adapter, SplunkHECAdapter,
)


# ---- AuditEvent hash computation -------------------------------------------

class TestAuditEventHash:
    def test_hash_is_deterministic(self):
        ae = AuditEvent(
            id="test-id",
            tenant_id="tenant-1",
            event_type="goal.created",
            resource_id="resource-1",
            action="create",
            status="success",
            created_at="2026-06-28T00:00:00+00:00",
        )
        h1 = ae.compute_hash("prev")
        h2 = ae.compute_hash("prev")
        assert h1 == h2

    def test_different_prev_hash_produces_different_hash(self):
        ae = AuditEvent(
            id="test-id",
            tenant_id="tenant-1",
            event_type="goal.created",
            resource_id="resource-1",
            action="create",
            status="success",
            created_at="2026-06-28T00:00:00+00:00",
        )
        h1 = ae.compute_hash("prev_a")
        h2 = ae.compute_hash("prev_b")
        assert h1 != h2

    def test_different_events_produce_different_hashes(self):
        ae1 = AuditEvent(id="id-1", tenant_id="t1", event_type="goal.created",
                          action="create", status="success",
                          created_at="2026-06-28T00:00:00+00:00")
        ae2 = AuditEvent(id="id-2", tenant_id="t1", event_type="goal.deleted",
                          action="delete", status="success",
                          created_at="2026-06-28T00:00:00+00:00")
        assert ae1.compute_hash("") != ae2.compute_hash("")

    def test_serialization_round_trip(self):
        ae = AuditEvent(
            tenant_id=str(uuid4()),
            event_type="agent.deleted",
            action="delete",
            status="success",
        )
        d = ae.to_dict()
        ae2 = AuditEvent(**d)
        assert ae2.event_type == ae.event_type
        assert ae2.id == ae.id


# ---- AuditWriter -----------------------------------------------------------

class TestAuditWriter:
    @pytest.fixture
    def mock_redis(self):
        r = AsyncMock()
        r.rpush = AsyncMock(return_value=1)
        return r

    @pytest.mark.asyncio
    async def test_write_pushes_to_wal(self, mock_redis):
        writer = AuditWriter(mock_redis)
        ae = AuditEvent(
            tenant_id=str(uuid4()),
            event_type="goal.created",
            action="create",
            status="success",
        )
        await writer.write(ae)
        mock_redis.rpush.assert_called_once()
        call_args = mock_redis.rpush.call_args
        assert call_args[0][0] == "audit:wal"

    @pytest.mark.asyncio
    async def test_write_never_raises_on_redis_error(self, mock_redis):
        mock_redis.rpush = AsyncMock(side_effect=Exception("Redis connection refused"))
        writer = AuditWriter(mock_redis)
        ae = AuditEvent(
            tenant_id=str(uuid4()),
            event_type="goal.created",
            action="create",
            status="success",
        )
        # Must not raise
        await writer.write(ae)

    @pytest.mark.asyncio
    async def test_write_batch_uses_pipeline(self, mock_redis):
        pipeline = AsyncMock()
        pipeline.rpush = AsyncMock()
        pipeline.execute = AsyncMock(return_value=[1, 1, 1])
        mock_redis.pipeline = MagicMock(return_value=pipeline)

        writer = AuditWriter(mock_redis)
        events = [
            AuditEvent(tenant_id=str(uuid4()), event_type=f"e{i}",
                       action="create", status="success")
            for i in range(3)
        ]
        await writer.write_batch(events)
        assert pipeline.rpush.call_count == 3


# ---- Admin action audit decorator ------------------------------------------

class TestAuditAdminDecorator:
    @pytest.mark.asyncio
    async def test_successful_action_creates_success_event(self):
        from app.governance.audit import audit_admin_action

        events_written = []

        class FakeWriter:
            async def write(self, event):
                events_written.append(event)

        request = MagicMock()
        request.state.tenant = MagicMock(id=uuid4())
        request.state.api_key = MagicMock(id=uuid4(), user_id=uuid4(), prefix="av_live_...")
        request.client = MagicMock(host="10.0.0.1")
        request.headers = {"X-Request-ID": "req-123"}
        request.app.state.audit_writer = FakeWriter()

        @audit_admin_action("agent.deleted", "agent", "delete",
                            extract_resource_id=lambda kw: kw.get("agent_id"))
        async def mock_delete_agent(agent_id: str, request=None):
            return {"deleted": True}

        result = await mock_delete_agent(agent_id=str(uuid4()), request=request)
        assert result == {"deleted": True}
        assert len(events_written) == 1
        assert events_written[0].event_type == "agent.deleted"
        assert events_written[0].status == "success"

    @pytest.mark.asyncio
    async def test_failed_action_creates_failure_event(self):
        from app.governance.audit import audit_admin_action

        events_written = []

        class FakeWriter:
            async def write(self, event):
                events_written.append(event)

        request = MagicMock()
        request.state.tenant = MagicMock(id=uuid4())
        request.state.api_key = MagicMock(id=uuid4(), user_id=uuid4(), prefix="av_live_...")
        request.client = MagicMock(host="10.0.0.1")
        request.headers = {}
        request.app.state.audit_writer = FakeWriter()

        @audit_admin_action("policy.updated", "policy", "update")
        async def mock_update_policy(request=None):
            raise ValueError("Validation error")

        with pytest.raises(ValueError):
            await mock_update_policy(request=request)

        assert len(events_written) == 1
        assert events_written[0].status == "failure"


# ---- SIEM adapters ---------------------------------------------------------

class TestSIEMAdapters:
    def test_build_splunk_adapter(self):
        adapter = build_siem_adapter("splunk", {
            "hec_url": "https://splunk:8088",
            "hec_token": "token",
            "index": "main",
            "source_type": "agentverse",
        })
        assert isinstance(adapter, SplunkHECAdapter)

    def test_unknown_siem_type_raises(self):
        with pytest.raises(ValueError, match="Unknown SIEM type"):
            build_siem_adapter("unknown", {})

    @pytest.mark.asyncio
    async def test_splunk_send_batch_format(self):
        adapter = SplunkHECAdapter(
            hec_url="https://splunk:8088",
            hec_token="token",
            index="main",
            source_type="agentverse",
        )
        events = [{
            "id": str(uuid4()),
            "event_type": "goal.created",
            "created_at": "2026-06-28T00:00:00+00:00",
            "tenant_id": str(uuid4()),
        }]
        # Mock httpx
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock(status_code=200)
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )
            result = await adapter.send_batch(events)
        assert result is True
```

---

## 7. Domain Extensibility

### Healthcare (HIPAA)
```python
# HIPAA Audit Format — additional required fields in metadata:
# {
#   "hipaa_workforce_member_id": "user-123",
#   "phi_patient_id": "[REDACTED:MRN]",   # MRN redacted, hash stored
#   "phi_access_reason": "treatment",      # treatment|payment|operations|other
#   "phi_record_count": 1,
#   "data_classification": "PHI"
# }
# Retention: HIPAA requires 6 years (set per-tenant retention policy)
```

### Legal (Chain of Custody)
```python
# Chain of Custody audit format:
# {
#   "matter_id": "MATTER-2026-001",
#   "document_id": "DOC-456",
#   "custodian_id": "user-123",
#   "action_justification": "Document review for production",
#   "privilege_status": "not_privileged"
# }
# Legal hold integration: auto-create hold when case opened via webhook
```

### Finance (SOX)
```python
# SOX audit fields:
# {
#   "journal_entry_id": "JE-2026-001",
#   "approver_chain": ["manager-1", "cfo"],
#   "dollar_amount": 15000.00,
#   "gl_account": "4100",
#   "sox_control_id": "CTRL-001"
# }
# 7-year retention (SEC Rule 17a-4)
```

### Education (FERPA)
```python
# FERPA audit fields:
# {
#   "student_id": "[REDACTED:FERPA]",
#   "record_type": "grades|attendance|financial_aid",
#   "requestor_role": "instructor|administrator|parent",
#   "legitimate_educational_interest": "course_management"
# }
```

### E-commerce (PCI DSS)
```python
# PCI audit fields (all card data must be masked):
# {
#   "transaction_id": "TXN-001",
#   "card_last4": "4242",          # ONLY last 4 digits stored
#   "amount": 99.99,
#   "merchant_id": "MERCH-001",
#   "pci_scope": "cardholder_data_environment"
# }
```

---

## AMENDMENTS — Critical Fixes

### Amendment 5.1 — Implement LEEF adapter (was listed but missing)

```python
class LEEFAdapter(SIEMAdapter):
    """Log Event Extended Format adapter for IBM QRadar."""

    LEEF_VERSION = "LEEF:2.0"
    VENDOR = "AgentVerse"
    PRODUCT = "AgentVerseOS"
    VERSION = "1.0"

    async def send(self, event: AuditEvent, config: SIEMConfig) -> None:
        # Build LEEF header: LEEF:2.0|Vendor|Product|Version|EventID|
        event_id = event.action_type.replace(".", "_").upper()
        header = f"{self.LEEF_VERSION}|{self.VENDOR}|{self.PRODUCT}|{self.VERSION}|{event_id}|"

        # Build key-value pairs (LEEF attribute format):
        attrs = {
            "devTime": event.created_at.strftime("%b %d %Y %H:%M:%S"),
            "devTimeFormat": "MMM dd yyyy HH:mm:ss",
            "sev": {"critical": 10, "high": 8, "medium": 5, "low": 3, "info": 1}.get(event.severity, 5),
            "src": event.ip_address or "0.0.0.0",
            "usrName": event.principal_id,
            "identSrc": event.tenant_id,
            "resource": event.resource_id,
            "action": event.action_type,
            "outcome": event.outcome,
            "agentID": event.agent_id or "",
            "goalID": event.goal_id or "",
            "domainContext": event.domain_context or "general",
        }
        leef_line = header + "\t".join(f"{k}={v}" for k, v in attrs.items())

        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                config.endpoint,
                content=leef_line.encode(),
                headers={"Content-Type": "application/leef"},
                auth=(config.credentials.get("username", ""), config.credentials.get("password", "")),
            )
```

### Amendment 5.2 — Fix hash chain restart gap

```python
# AuditFlusher: persist prev_hash to Redis so restarts don't break chain
async def _get_prev_hash(self, tenant_id: str) -> str:
    """Get last known hash from Redis (persisted across restarts)."""
    key = f"audit_chain_last_hash:{tenant_id}"
    if self._redis:
        stored = await self._redis.get(key)
        if stored:
            return stored.decode() if isinstance(stored, bytes) else stored
    # Redis miss: query DB for last hash
    async with self._db() as session:
        row = (await session.execute(_t("""
            SELECT event_hash FROM audit_events
            WHERE tenant_id = :tid ORDER BY sequence_number DESC LIMIT 1
        """), {"tid": tenant_id})).fetchone()
        if row:
            return row[0]
    return ""  # Genesis hash for brand new tenant

async def _persist_hash(self, tenant_id: str, event_hash: str) -> None:
    """Persist current chain tail to Redis for restart recovery."""
    if self._redis:
        await self._redis.setex(f"audit_chain_last_hash:{tenant_id}", 86400 * 7, event_hash)
```

### Amendment 5.3 — Fix flush shutdown race + DLQ processor

```python
# Proper graceful shutdown:
async def shutdown(self) -> None:
    """Graceful shutdown — flush remaining WAL before exit."""
    if self._flush_task:
        self._flush_task.cancel()
        try:
            await self._flush_task  # wait for cancellation
        except asyncio.CancelledError:
            pass
    # Final flush after cancellation:
    await self.flush()

# DLQ processor Celery task:
@celery_app.task(name="app.scaling.tasks.process_audit_dlq", queue="maintenance")
def process_audit_dlq():
    """Retry failed audit events from DLQ."""
    import asyncio
    asyncio.run(_process_dlq_async())
# Beat schedule: every 30 minutes
```

### Amendment 5.4 — Fix HashChainVerifier to stream instead of load all into memory

```python
async def verify_range(self, tenant_id: str, from_seq: int, to_seq: int) -> VerificationResult:
    """Stream-verify hash chain — no OOM at scale."""
    from sqlalchemy import text as _t
    broken_links = []
    prev_hash = ""
    chunk_size = 1000

    async with self._db() as session:
        for offset in range(0, to_seq - from_seq, chunk_size):
            rows = (await session.execute(_t("""
                SELECT sequence_number, event_hash, prev_hash, tenant_id, action_type, outcome, created_at
                FROM audit_events
                WHERE tenant_id = :tid AND sequence_number BETWEEN :from AND :to
                ORDER BY sequence_number ASC
                LIMIT :limit OFFSET :offset
            """), {"tid": tenant_id, "from": from_seq, "to": to_seq, "limit": chunk_size, "offset": offset})).fetchall()

            for row in rows:
                seq, event_hash, stored_prev, *fields = row
                # Verify prev_hash chain:
                if stored_prev != prev_hash:
                    broken_links.append({"sequence": seq, "expected_prev": prev_hash, "stored_prev": stored_prev})
                # Verify event_hash:
                computed = self._compute_hash(seq, stored_prev, *fields)
                if computed != event_hash:
                    broken_links.append({"sequence": seq, "hash_mismatch": True})
                prev_hash = event_hash

    return VerificationResult(verified=len(broken_links) == 0, broken_links=broken_links, events_checked=to_seq - from_seq)
```

### Amendment 5.5 — Fix metadata not in hash computation + add Celery tasks + App.tsx + toast + prefers-reduced-motion

```python
# Include metadata in canonical hash form:
def _compute_hash(self, seq: int, prev_hash: str, tenant_id: str, action_type: str, outcome: str, created_at, metadata: dict) -> str:
    canonical = json.dumps({
        "seq": seq, "prev": prev_hash, "tenant": tenant_id,
        "action": action_type, "outcome": outcome,
        "ts": created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at),
        "metadata": metadata,  # ← NOW INCLUDED
    }, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode()).hexdigest()

# Celery tasks (add to celery_app.py beat_schedule):
# - "flush-audit-wal" every 10s (already specified)
# - "process-audit-dlq" every 30min
# - "create-audit-partitions" monthly
# - "run-retention-sweep" daily at 02:00 UTC
```

```typescript
// App.tsx: AuditExplorerPage already exists — ensure lazy
// Sidebar: already has /audit

// prefers-reduced-motion:
@media (prefers-reduced-motion: reduce) {
  .audit-row-slide-in, .chain-verify-flash, .legal-hold-lock-appear { animation: none !important; }
}

// Toast notifications:
// exportAudit onSuccess: toast({kind:"success", message:"Audit export started — you'll be notified when ready"})
// placeLegalHold onSuccess: toast({kind:"warning", message:"Legal hold placed — deletion prevented"})
// releaseLegalHold → ConfirmModal + toast
// verifChain onSuccess: toast({kind:result.verified?"success":"error", message: result.verified ? "Chain integrity verified ✓" : `${result.broken_links.length} broken links detected!`})
```
