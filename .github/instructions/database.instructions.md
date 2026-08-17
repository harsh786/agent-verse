---
applyTo: "agent-verse-backend/app/db/**"
---

# Database Design Instructions — AgentVerse

## Schema Conventions

### Required Columns (EVERY table must have all four)
```python
id         = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid7)   # UUIDv7 (sortable!)
tenant_id  = Column(PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
```

### Naming
- Tables: `snake_case`, **plural** (`org_missions`, `agent_tasks`, `knowledge_nodes`)
- Columns: `snake_case`, singular meaning (`user_id`, `created_at`, `is_active`)
- PKs: `id` (UUID v7)
- FKs: `<singular_table>_id` (`mission_id`, `agent_id`, `tenant_id`)
- Indexes: `idx_<table>_<columns>` (`idx_org_missions_tenant_status`)
- Migrations: `NNNN_description.py` sequential (next is last+1)

## Index Strategy

### Always Index
```python
__table_args__ = (
    # 1. FK columns (ALWAYS — every FK must have an index)
    Index("idx_missions_tenant_id", "tenant_id"),
    Index("idx_missions_agent_id", "agent_id"),

    # 2. Status/type columns (for filtering)
    Index("idx_missions_status", "status"),

    # 3. Hot composite queries (tenant + status = most common pattern)
    Index("idx_missions_tenant_status", "tenant_id", "status"),
    Index("idx_missions_tenant_priority_created", "tenant_id", "priority", "created_at"),

    # 4. Partial indexes (for sparse conditions — much smaller, faster)
    Index("idx_missions_active_only", "tenant_id", "created_at",
          postgresql_where="status = 'active'"),

    # 5. Full-text search (GIN)
    Index("idx_missions_title_fts", "title",
          postgresql_using="gin",
          postgresql_ops={"title": "gin_trgm_ops"}),
)
```

### Concurrent Index Creation (Migrations)
```python
# In Alembic migration — never lock the table:
def upgrade():
    # op.create_index() locks the table — use raw SQL for CONCURRENTLY
    op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_name ON table(col)")
    # NEVER: op.create_index("idx_name", "table", ["col"])  ← locks table!
```

## Partitioning Strategy

### Time-series / High-volume tables
```python
# Partition by month for event/audit tables (millions of rows)
# Do in raw SQL migration:
op.execute("""
    CREATE TABLE org_events_y2026m08 PARTITION OF org_events
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
""")

# Auto-create partitions monthly via Celery Beat:
# maintenance.create_next_partition() — runs 1st of every month
```

### When to partition
- > 10M rows expected per year → partition by month
- Tables: `org_events`, `agent_task_logs`, `audit_entries`, `stream_events`
- Do NOT partition small tables (< 1M rows) — overhead not worth it

## Row-Level Security (RLS)

### All tenant-scoped tables MUST have RLS
```python
# In migration:
def upgrade():
    # 1. Enable RLS
    op.execute("ALTER TABLE my_table ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE my_table FORCE ROW LEVEL SECURITY")

    # 2. Create policy (uses SET LOCAL app.tenant_id in session)
    op.execute("""
        CREATE POLICY tenant_isolation ON my_table
        USING (tenant_id = current_setting('app.tenant_id')::uuid)
    """)

    # 3. Grant access to app user
    op.execute("GRANT ALL ON my_table TO agentverse_app")

# Usage (set in every request via middleware):
# SET LOCAL app.tenant_id = '<uuid>';
```

## Migration Patterns

### Zero-Downtime Migration (Expand-Contract)
```python
# PHASE 1: Add nullable column (instant, no lock)
def upgrade():
    op.add_column("missions", sa.Column("priority_score", Float, nullable=True))
    # CREATE INDEX CONCURRENTLY separately (non-locking)

# PHASE 2: Backfill (in batches, separate migration)
def upgrade():
    op.execute("""
        UPDATE missions SET priority_score = 0.5
        WHERE priority_score IS NULL
        LIMIT 10000
    """)  # Run repeatedly until 0 rows affected

# PHASE 3: Add NOT NULL constraint (after all rows backfilled)
def upgrade():
    op.alter_column("missions", "priority_score", nullable=False)
```

### Migration File Template
```python
"""<description>

Revision ID: XXXX
Revises: YYYY
Create Date: YYYY-MM-DD
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "XXXX"
down_revision = "YYYY"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Always CONCURRENTLY for indexes
    # Always check IF NOT EXISTS
    pass

def downgrade() -> None:
    # Must be reversible
    pass
```

## Optimistic Locking
```python
# Add version column to resources that may be concurrently updated
version = Column(Integer, nullable=False, default=1, server_default="1")

# In repository — check version on update:
result = await session.execute(
    update(MyModel)
    .where(MyModel.id == entity_id, MyModel.version == expected_version)
    .values(**updates, version=expected_version + 1)
    .returning(MyModel)
)
if result.scalar_one_or_none() is None:
    raise ConcurrentModificationError(entity_id)
```

## Query Patterns

### N+1 Prevention — Always Eager Load
```python
# WRONG — N+1:
missions = await session.scalars(select(Mission))
for m in missions:
    agent = await m.awaitable_attrs.assigned_agent  # 1 query per mission!

# CORRECT — single query:
from sqlalchemy.orm import selectinload, joinedload
missions = await session.scalars(
    select(Mission)
    .options(
        selectinload(Mission.assigned_agent),
        selectinload(Mission.tasks),
    )
    .where(Mission.tenant_id == tenant_id)
)
```

### Query Counting in Tests
```python
# Catch N+1 regressions automatically:
with count_queries(session) as qc:
    await service.list_missions(org_id=org_id)
assert qc.total <= 3, f"N+1 detected: {qc.total} queries"
```

## Database Selection Guide

| Use Case | Database | Why |
|---------|----------|-----|
| Core relational data (missions, agents, orgs) | **PostgreSQL + pgvector** | ACID, RLS, vector search |
| Session cache, pub/sub, rate limiting | **Redis** | Sub-ms latency |
| Long-term key-value (agent memories) | **Redis + TTL** | Auto-expiry |
| Time-series metrics | **Prometheus** | Purpose-built |
| Full-text search | **Postgres GIN/trigram** | Avoid separate ES for this scale |
| Object storage (files, artifacts) | **S3 / MinIO** | Cost-effective |
| Event streaming (high throughput) | **Redis Streams / Kafka** | Ordered, consumer groups |

> **Rule**: Don't add a new database technology without a documented ADR explaining
> why PostgreSQL + Redis cannot handle the use case.

## Connection Management
```python
# Always use pool — never create connections per-request
POOL_CONFIG = {
    "pool_size": 20,           # base pool
    "max_overflow": 10,        # burst to 30 total
    "pool_timeout": 30,        # fail fast
    "pool_recycle": 1800,      # recycle connections every 30min
    "pool_pre_ping": True,     # detect stale connections
}
# PgBouncer sits in front for connection multiplexing at scale
```
