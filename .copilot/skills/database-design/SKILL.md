# Database Design Skill — AgentVerse (Best Technology per Use Case)

## Database Technology Selection Matrix

| Use Case | Database | Why | AgentVerse Usage |
|---------|----------|-----|-----------------|
| Core relational (missions, agents, orgs) | **PostgreSQL 16** | ACID, JSONB, pgvector, RLS | Primary store |
| Vector similarity search | **pgvector** (built into Postgres) | Collocated with data, no extra hop | Embeddings, semantic search |
| Session cache, pub/sub, rate limiting | **Redis 7** | Sub-ms, TTL, Streams, Pub/Sub | Cache, queues, SSE fanout |
| Full-text search | **Postgres GIN + pg_trgm** | Avoid separate ES at this scale | Mission/knowledge search |
| Agent working memory (short TTL) | **Redis HASH + TTL** | Auto-expiry, no cleanup needed | LangGraph checkpoints |
| High-throughput event streaming | **Redis Streams** | Ordered, consumer groups, lightweight | Org events, gateway |
| Object/file storage | **S3 / MinIO** | Cost-effective, infinite scale | Artifacts, vault files |
| Time-series metrics | **Prometheus + TimescaleDB** | Purpose-built retention | API latency, costs |
| Graph traversal (future) | **Apache AGE** (Postgres extension) | Same Postgres, graph queries | Knowledge graph queries |

> **Rule**: PostgreSQL + Redis solves 95% of problems at this scale.
> Add a new database ONLY with a written ADR justifying why these two cannot handle the use case.

---

## PostgreSQL — World-Class Design

### Schema Completeness Checklist

```python
class WorldClassModel(Base):
    __tablename__ = "domain_entities"

    # Identifiers
    id          = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid7)   # UUIDv7 (sortable)
    tenant_id   = Column(PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)

    # State
    status      = Column(String(50), nullable=False, default="active")
    version     = Column(Integer, nullable=False, default=1)    # optimistic locking

    # Soft delete
    deleted_at  = Column(DateTime(timezone=True), nullable=True)

    # Audit
    created_at  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by  = Column(PG_UUID(as_uuid=True), nullable=True)  # user_id if known

    # Indexing strategy
    __table_args__ = (
        Index("idx_de_tenant_id",            "tenant_id"),                  # FK
        Index("idx_de_tenant_status",        "tenant_id", "status"),        # hot composite
        Index("idx_de_tenant_created",       "tenant_id", "created_at"),    # list sorting
        Index("idx_de_status_active",        "tenant_id", "created_at",
              postgresql_where="deleted_at IS NULL AND status = 'active'"),  # partial
    )
```

### Partitioning High-Volume Tables

```sql
-- org_events: millions of rows/month — partition by month
CREATE TABLE org_events (
    id          UUID DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL,
    event_type  TEXT NOT NULL,
    payload     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
) PARTITION BY RANGE (created_at);

-- Create partition for each month:
CREATE TABLE org_events_y2026m08 PARTITION OF org_events
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE org_events_y2026m09 PARTITION OF org_events
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');

-- Auto-create next month's partition via Celery Beat (1st of every month):
-- maintenance.create_next_partition()
```

### pgvector — Embedding Storage & Search

```python
from pgvector.sqlalchemy import Vector

class KnowledgeEmbedding(Base):
    __tablename__ = "knowledge_embeddings"

    id           = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid7)
    tenant_id    = Column(PG_UUID(as_uuid=True), nullable=False, index=True)
    node_id      = Column(PG_UUID(as_uuid=True), ForeignKey("graph_nodes.id"))
    content_hash = Column(String(64), nullable=False)
    embedding    = Column(Vector(1536), nullable=False)  # voyage-3 dimension

    __table_args__ = (
        # HNSW index for fast approximate nearest neighbor search
        Index("idx_ke_embedding_hnsw", "embedding",
              postgresql_using="hnsw",
              postgresql_with={"m": 16, "ef_construction": 64}),
    )

# Similarity search (cosine distance):
async def search_similar(self, query_embedding: list[float], limit: int = 10):
    return await session.scalars(
        select(KnowledgeEmbedding)
        .where(KnowledgeEmbedding.tenant_id == self._tenant.id)
        .order_by(KnowledgeEmbedding.embedding.cosine_distance(query_embedding))
        .limit(limit)
    )
```

---

## Redis — Patterns for Each Use Case

```python
# 1. Semantic Cache (LLM dedup — save 40%+ on costs)
class SemanticCache:
    TTL = 3600  # 1 hour

    async def get(self, query_embedding: list[float]) -> str | None:
        # Store: key=embedding_hash, value=LLM_response
        key = f"sem:{self._tenant.id}:{hash_embedding(query_embedding)}"
        return await self.redis.get(key)

# 2. Rate Limiter (sliding window, per-tenant)
class SlidingWindowRateLimiter:
    async def is_allowed(self, tenant_id: str, limit: int) -> bool:
        key = f"rate:{tenant_id}:{int(time.time() // 60)}"
        count = await self.redis.incr(key)
        await self.redis.expire(key, 120)
        return count <= limit

# 3. Pub/Sub for SSE fanout (mission events to all connected clients)
# Publisher (in Celery task):
await redis.publish(f"mission:{mission_id}:events", json.dumps(event))
# Subscriber (in FastAPI SSE endpoint):
async with redis.pubsub() as ps:
    await ps.subscribe(f"mission:{mission_id}:events")
    async for msg in ps.listen():
        yield f"data: {msg['data']}\n\n"

# 4. Distributed lock (prevent duplicate processing)
async with DistributedLock(redis, f"graphify:{org_id}", ttl=300):
    await run_graphify(org_id)

# 5. Redis Streams for ordered event delivery
await redis.xadd(f"org:{org_id}:events",
    {"type": "mission.completed", "id": mission_id})
```

---

## Migrations Best Practices

```python
# Naming convention: NNNN_verb_noun.py (sequential)
# Next after 0103: 0104_add_missions_deadline.py

# Template for zero-downtime migration:
"""Add deadline column to org_missions.

Phase 1 (this migration): Add nullable column.
Phase 2 (next deploy): Code reads the column.
Phase 3 (separate migration): Add NOT NULL after backfill.
"""

def upgrade():
    # Phase 1: nullable column (no lock, instant)
    op.add_column("org_missions",
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True))

    # Index (CONCURRENTLY — non-locking):
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "idx_org_missions_deadline ON org_missions(tenant_id, deadline_at) "
        "WHERE deadline_at IS NOT NULL"
    )

def downgrade():
    op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_org_missions_deadline")
    op.drop_column("org_missions", "deadline_at")
```

---

## Connection Pooling

```python
# PgBouncer config (transaction mode — best for short async requests):
PGBOUNCER = {
    "pool_mode":       "transaction",   # release connection after each tx
    "max_client_conn": 1000,            # max frontend connections
    "default_pool_size": 25,            # backend connections per user/db
    "min_pool_size":   5,
    "server_idle_timeout": 600,
    "server_connect_timeout": 10,
}

# SQLAlchemy pool (per-pod):
ENGINE_POOL = {
    "pool_size":    20,
    "max_overflow": 10,    # burst to 30 total
    "pool_timeout": 30,    # fail fast if pool exhausted
    "pool_recycle": 1800,  # recycle every 30min
    "pool_pre_ping": True, # validate connections
}
```
