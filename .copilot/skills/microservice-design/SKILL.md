# Microservice & Distributed System Design Skill — AgentVerse

## Architecture Principles Applied

```
Principle               → AgentVerse Implementation
────────────────────────────────────────────────────
Single Responsibility   → Each app/<domain>/ owns ONE bounded context
API Gateway             → Universal Command Gateway (UCG) — single entry point
Event-Driven            → Redis Streams + Outbox pattern for reliable delivery
Bulkhead Isolation      → Per-tenant concurrency limits via Bulkhead class
Circuit Breaker         → All external calls wrapped in CircuitBreaker
Idempotency             → Every state-change endpoint + Celery task
Distributed Tracing     → OpenTelemetry propagated across all async boundaries
Saga Pattern            → Multi-step operations with automatic compensation
Backpressure            → Queue depth checks + 503 on overload
Graceful Degradation    → Semantic cache fallback, FakeProvider fallback
```

---

## Domain Boundaries (Bounded Contexts)

```
app/
  agent/          ← Agent execution engine (LangGraph loops)
  missions/       ← Mission lifecycle management
  knowledge/      ← Knowledge graph + semantic search
  mcp/            ← MCP protocol + connector registry
  governance/     ← Audit, cost, policy, HITL approvals
  tenancy/        ← Multi-tenant isolation, auth, rate limiting
  providers/      ← LLM provider abstraction
  reliability/    ← Cross-cutting: circuit breaker, bulkhead, idempotency
  observability/  ← Cross-cutting: metrics, alerts
  scaling/        ← Celery app, queue routing
  services/       ← Orchestration between domains
```

**Rule**: A domain module can depend on `core/`, `reliability/`, `tenancy/`.
It MUST NOT directly import from another domain's `repository.py`.
Cross-domain calls go through the domain's `service.py` public API only.

---

## Event-Driven Communication

### Domain Event Pattern

```python
# Events published on significant state changes:
# These are stored in outbox table and published reliably.

DOMAIN_EVENTS = {
    "mission.created":    "New mission created — triggers notification, billing",
    "mission.completed":  "Mission done — triggers lesson extraction, analytics",
    "agent.failed":       "Agent execution failed — triggers alert, replan",
    "graphify.completed": "Knowledge graph built — triggers UI refresh",
    "tenant.limit_reached": "Plan limit hit — triggers upgrade prompt",
}

# Publishing pattern (transactional outbox):
async def complete_mission(self, mission_id: str) -> None:
    async with session.begin():
        await session.execute(
            update(Mission).where(Mission.id == mission_id)
            .values(status="completed")
        )
        # Write event in SAME transaction — atomic
        session.add(OutboxEvent(
            tenant_id=self._tenant.id,
            aggregate_id=mission_id,
            event_type="mission.completed",
            payload={"mission_id": mission_id, "completed_at": utcnow().isoformat()},
        ))
```

### Message Ordering via Redis Streams

```python
# Redis XADD: ordered, consumer-group aware
await redis.xadd(
    f"org:{org_id}:events",
    {
        "seq":    str(next_sequence),   # monotonic counter per org
        "type":   "mission.completed",
        "data":   json.dumps(payload),
    }
)

# Consumer (SSE endpoint):
last_id = "0"  # or last delivered ID from client
while True:
    messages = await redis.xread(
        {f"org:{org_id}:events": last_id},
        count=10,
        block=1000,  # 1s timeout
    )
    for _, events in messages:
        for event_id, data in events:
            yield f"data: {data['data']}\n\n"
            last_id = event_id

# Client-side gap detection:
# If received seq jumps (5→8), client refetches missing events
```

---

## Horizontal Scaling Patterns

### Stateless API Pods (Scale to Any Number)

```yaml
# All state in PostgreSQL + Redis — API pods are stateless
# scale: kubectl scale deployment backend --replicas=N
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxSurge:       1
    maxUnavailable: 0   # zero-downtime rolling update
```

### Celery: Per-Plan Queue Routing

```python
# Enterprise tasks never blocked by free-tier workloads
CELERY_TASK_ROUTES = {
    "goals.free.*":         {"queue": "goals.free"},
    "goals.starter.*":      {"queue": "goals.starter"},
    "goals.professional.*": {"queue": "goals.professional"},
    "goals.enterprise.*":   {"queue": "goals.enterprise"},
    "maintenance.*":        {"queue": "maintenance"},
}
# Workers can be scaled independently per queue:
# kubectl scale deployment celery-enterprise --replicas=10
```

### LangGraph: Distributed Checkpointing

```python
# Agent state survives pod restarts and node failures
# State stored in Redis, recoverable by any pod

if redis_available:
    checkpointer = AsyncRedisSaver(redis_client)   # distributed
else:
    checkpointer = MemorySaver()                    # fallback (single pod)

graph = StateGraph(AgentState)
graph = graph.compile(checkpointer=checkpointer)
# State automatically persisted between LangGraph nodes
```

---

## Resilience Patterns Reference

```python
# These all exist in app/reliability/ — USE THEM, don't re-implement:

# 1. Circuit Breaker (wraps LLM/MCP/external HTTP calls)
from app.reliability.circuit_breaker import CircuitBreaker
async with CircuitBreaker(name="anthropic", redis=redis, threshold=5):
    response = await provider.complete(request)

# 2. Bulkhead (per-tenant concurrency limit)
from app.reliability.bulkhead import Bulkhead
async with Bulkhead(name=f"agent:{tenant_id}", max_concurrent=5):
    await run_agent_loop(goal_id)

# 3. Idempotency (duplicate request protection)
from app.reliability.idempotency import IdempotencyGuard
guard = IdempotencyGuard(redis=redis)
if cached := await guard.get(idempotency_key):
    return cached
result = await do_work()
await guard.set(idempotency_key, result, ttl=86400)

# 4. Distributed Lock (singleton operations)
from app.reliability.distributed_lock import DistributedLock
async with DistributedLock(redis, f"graphify:{org_id}", ttl=300):
    await run_graphify(org_id)  # only one pod runs this at a time

# 5. Rollback (compensating actions for multi-step operations)
from app.reliability.rollback import RollbackEngine
async with RollbackEngine() as engine:
    engine.register_compensation(undo_step1, step1_args)
    await step1()
    engine.register_compensation(undo_step2, step2_args)
    await step2()
    # On failure: compensations run in reverse order
```

---

## Service Mesh (Future: Istio/Linkerd)

```yaml
# When scaling to multiple services, add:
# - mTLS between services (automatic with Istio)
# - Traffic shaping: canary releases at mesh level
# - Observability: automatic span propagation
# For now: single-service monolith with internal domain isolation
```

---

## Anti-Patterns (Never Do)

```python
# ❌ WRONG — cross-domain repo access
from app.missions.repository import MissionRepository
class GraphifyService:
    async def get_mission_data(self):
        repo = MissionRepository(...)   # crossing domain boundary!
        return await repo.get_by_id(id)

# ✅ CORRECT — cross-domain via service API
from app.missions.service import MissionService
class GraphifyService:
    async def get_mission_data(self):
        return await self._mission_service.get(id)   # via public API

# ❌ WRONG — sync I/O blocks the event loop
import requests
response = requests.get("https://api.example.com")   # blocks all other requests

# ✅ CORRECT — async always
async with httpx.AsyncClient() as client:
    response = await client.get("https://api.example.com", timeout=10)

# ❌ WRONG — sharing mutable state between requests
class MissionService:
    cache = {}   # module-level — shared between all requests, not tenant-isolated!

# ✅ CORRECT — state per-request via tenant context
class MissionService:
    def __init__(self, tenant: TenantContext):
        self._tenant = tenant   # isolated per request
```
