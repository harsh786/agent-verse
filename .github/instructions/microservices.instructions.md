---
applyTo: "agent-verse-backend/**/*.py,agent-verse-backend/helm/**"
---

# Microservices & Distributed Design — Mandatory Patterns

## Domain Isolation Rules (Always Enforced)

```python
# RULE 1: Never cross domain boundaries via repository.
# app/<domain_a> MUST NOT import from app/<domain_b>/repository.py

# ❌ WRONG — direct cross-domain repo call
from app.missions.repository import MissionRepository   # in graphify module!

# ✅ CORRECT — via public service API only
from app.missions.service import MissionService

# RULE 2: All cross-domain calls go through service.py interface
# The service.py is the public API of a domain. Repository is private.

# RULE 3: Circular imports = architecture violation
# If app/A imports app/B AND app/B imports app/A → extract to app/core/
```

## Event-Driven Patterns (Use for Cross-Domain Side Effects)

```python
# RULE: Never call another domain's service directly for side effects.
# Use events so domains stay decoupled.

# ❌ WRONG — direct coupling
class MissionService:
    async def complete_mission(self, id):
        await mission_repo.update_status(id, "completed")
        await notification_service.notify_all(id)   # coupled!
        await analytics_service.record(id)          # coupled!

# ✅ CORRECT — event-driven decoupling
class MissionService:
    async def complete_mission(self, id):
        async with session.begin():
            await mission_repo.update_status(id, "completed")
            session.add(OutboxEvent(event_type="mission.completed", ...))
        # Notification and Analytics subscribe to the event independently
```

## Idempotency (Every State-Changing Operation)

```python
# RULE: Every Celery task and every state-changing API endpoint
#       MUST be safe to execute multiple times with the same input.

@celery_app.task(bind=True, name="domain.process")
def process(self, tenant_id: str, entity_id: str) -> dict:
    # Check if already done (idempotency guard):
    if await repo.is_already_processed(entity_id):
        return {"status": "already_done", "entity_id": entity_id}
    # Process
    ...

# In API endpoints:
@router.post("", operation_id="mission_create")
async def create(
    body: CreateRequest,
    x_idempotency_key: str | None = Header(default=None),
    service: MissionService = Depends(get_service),
):
    return await service.create(body, idempotency_key=x_idempotency_key)
```

## Horizontal Scalability (Design for N Pods From Day 1)

```python
# RULE: No in-process shared mutable state between requests.
# ALL state must live in Postgres or Redis.

# ❌ WRONG — module-level mutable state (breaks in multi-pod)
_rate_limit_counters: dict = {}   # only works in 1 pod!

# ✅ CORRECT — Redis-backed (works across all pods)
limiter = SlidingWindowRateLimiter(redis=redis_client)

# RULE: LangGraph checkpointer must use Redis (not MemorySaver) in production
checkpointer = AsyncRedisSaver(redis_client)   # ✅ survives pod restart
# NOT: checkpointer = MemorySaver()            # ❌ lost on pod restart
```

## Bulkhead Isolation (Prevent Noisy Neighbour)

```python
# RULE: Per-tenant execution bulkhead on all heavy operations.
# Prevents one tenant from consuming all resources.

from app.reliability.bulkhead import Bulkhead

# In agent execution:
async with Bulkhead(
    name=f"agent:{tenant_id}",
    max_concurrent=5,   # plan-based limit
):
    await run_agent_loop(goal_id)

# If full: return 503 immediately (don't queue indefinitely)
```

## Circuit Breaker (All External I/O)

```python
# RULE: Every external call (LLM, MCP, webhook, S3, third-party API)
#       MUST be wrapped in a CircuitBreaker.

from app.reliability.circuit_breaker import CircuitBreaker

# ❌ WRONG — no circuit breaker
response = await anthropic_client.complete(request)

# ✅ CORRECT
async with CircuitBreaker(name="anthropic", redis=redis, threshold=5):
    response = await anthropic_client.complete(request)
```

## Timeout Matrix (Enforce on Every External Call)

```python
# RULE: ALL external I/O has an explicit timeout.
import asyncio

TIMEOUTS = {
    "llm_streaming": 120,   "llm_sync":  30,
    "mcp_tool":       30,   "db_query":  10,
    "http_api":       10,   "redis":      1,
    "health_check":    3,   "webhook":   10,
}

async with asyncio.timeout(TIMEOUTS["llm_streaming"]):
    response = await provider.complete(request)
```

## Graceful Degradation (Always Have a Fallback)

```python
# RULE: Non-critical operations must degrade gracefully.

# ❌ WRONG — crash on analytics failure
async def create_mission(self, req):
    mission = await self._repo.create(req)
    await analytics.track("mission_created", ...)  # crashes mission creation!
    return mission

# ✅ CORRECT — non-critical fails silently
async def create_mission(self, req):
    mission = await self._repo.create(req)
    try:
        await analytics.track("mission_created", ...)
    except Exception:
        log.warning("analytics.track.failed", mission_id=str(mission.id))
    return mission  # mission still created!

# RULE: Critical operations (DB writes) propagate exceptions.
# Non-critical operations (analytics, notifications) absorb exceptions.
```
