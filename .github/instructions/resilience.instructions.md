---
applyTo: "agent-verse-backend/**/*.py"
---

# Resilience Pattern Instructions — AgentVerse

## Existing Resilience Modules (Use These — Never Re-implement)

```python
# All resilience utilities live in app/reliability/
from app.reliability.circuit_breaker import CircuitBreaker, redis_circuit_breaker
from app.reliability.bulkhead import Bulkhead, BulkheadFullError
from app.reliability.idempotency import IdempotencyGuard
from app.reliability.distributed_lock import DistributedLock
from app.reliability.dedup import RequestDeduplicator
from app.reliability.rollback import RollbackEngine
```

## Circuit Breaker — All External Calls

Apply to: LLM provider calls, MCP tool calls, external HTTP APIs, webhook delivery.

```python
class LLMProviderService:
    def __init__(self, redis_client):
        self._cb = CircuitBreaker(
            name="anthropic",
            redis=redis_client,
            failure_threshold=5,      # open after 5 failures in window
            recovery_timeout=30,      # half-open after 30s
            success_threshold=2,      # close after 2 successes in half-open
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        async with self._cb:           # raises CircuitOpenError if open
            return await self._anthropic_client.complete(request)
```

## Bulkhead — Per-Tenant Concurrency

Apply to: LangGraph agent execution, Graphify jobs, heavy async tasks.

```python
class AgentExecutionService:
    def __init__(self, tenant_id: str):
        self._bulkhead = Bulkhead(
            name=f"agent_execution:{tenant_id}",
            max_concurrent=5,          # max 5 concurrent agent runs per tenant
        )

    async def execute_goal(self, goal_id: str) -> GoalResult:
        async with self._bulkhead:     # raises BulkheadFullError if full
            return await self._run_langgraph(goal_id)
```

## Retry with Exponential Backoff

Apply to: transient failures (network, rate limits, temporary DB errors).

```python
import asyncio
from typing import TypeVar, Callable, Awaitable

T = TypeVar("T")

async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exceptions: tuple = (Exception,),
) -> T:
    delay = base_delay
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except exceptions as exc:
            if attempt == max_retries:
                raise
            jitter = delay * 0.1 * (2 * __import__('random').random() - 1)
            await asyncio.sleep(min(delay + jitter, max_delay))
            delay *= 2     # exponential backoff
```

## Idempotency — State-Changing Operations

Apply to: mission creation, task dispatch, payment processing, webhook delivery.

```python
class MissionService:
    async def create_mission(
        self, req: CreateMissionRequest, *, idempotency_key: str | None = None
    ) -> Mission:
        if idempotency_key:
            guard = IdempotencyGuard(redis=self._redis)
            existing = await guard.get(idempotency_key)
            if existing:
                return existing  # Return cached result — same input, same output

        mission = await self._repo.create(req)

        if idempotency_key:
            await guard.set(idempotency_key, mission, ttl_seconds=86400)

        return mission
```

## Distributed Lock — Cross-Replica Coordination

Apply to: scheduled job leader election, singleton operations, resource locking.

```python
from app.reliability.distributed_lock import DistributedLock

async def process_scheduled_job(job_id: str) -> None:
    lock = DistributedLock(
        redis=redis_client,
        key=f"job:lock:{job_id}",
        ttl_seconds=300,       # auto-release after 5 min (prevents deadlock)
    )
    async with lock:           # raises LockNotAcquiredError if held elsewhere
        # Only one replica runs this at a time
        await do_work(job_id)
```

## Outbox Pattern — Reliable Event Delivery

Apply to: all domain events that must be delivered reliably (mission.completed, etc.)

```python
# In the same transaction as state change:
async def complete_mission(self, mission_id: str) -> None:
    async with session.begin():
        # 1. Update state
        await session.execute(
            update(Mission).where(Mission.id == mission_id)
            .values(status="completed")
        )
        # 2. Write outbox event (same tx — atomic)
        session.add(OutboxEvent(
            tenant_id=self._tenant.id,
            aggregate_id=mission_id,
            event_type="mission.completed",
            payload={"mission_id": mission_id},
        ))
    # Worker picks up and delivers asynchronously
```

## Timeout — All External Calls

```python
import asyncio

# ALWAYS set timeouts on external I/O:
async def call_external_api(url: str) -> dict:
    try:
        async with asyncio.timeout(10):   # 10s max
            async with httpx.AsyncClient() as client:
                response = await client.get(url)
                return response.json()
    except asyncio.TimeoutError:
        raise ExternalServiceTimeoutError(url)
```

### Timeout Matrix
| Operation | Timeout |
|-----------|---------|
| HTTP API request | 10s |
| LLM call (streaming) | 120s |
| MCP tool call | 30s |
| DB query | 10s |
| Redis operation | 1s |
| Health check | 3s |
| Celery task (web) | 300s |
| Celery task (batch) | 3600s |

## Backpressure — Queue Protection

```python
from app.reliability.bulkhead import Bulkhead

# In API endpoint — reject if system is overloaded:
@router.post("/goals")
async def create_goal(
    body: CreateGoalRequest,
    bulkhead: Bulkhead = Depends(get_tenant_bulkhead),
) -> GoalResponse:
    try:
        async with bulkhead:
            return await goal_service.create(body)
    except BulkheadFullError:
        raise HTTPException(
            status_code=503,
            detail={"type": "service-unavailable",
                    "title": "System overloaded",
                    "retry_after": 30},
        )
```

## Graceful Shutdown — SIGTERM Handling

```python
# Already implemented in app/main.py via GracefulShutdownMiddleware
# DO NOT bypass it — ensure your tasks respect the 30s drain window:

@celery_app.task(bind=True)
def my_task(self):
    # Check for shutdown signal periodically in long-running tasks:
    for item in large_batch:
        if self.request.called_directly:
            break  # Celery signals task cancellation via this flag
        process(item)
```

## Fallback Strategy

```python
# Always define a fallback for non-critical operations:
async def get_org_health_score(org_id: str) -> float:
    try:
        return await health_score_service.calculate(org_id)
    except Exception:
        log.warning("health_score.fallback", org_id=org_id)
        return 0.5  # neutral fallback — never crash the response

# For critical operations — fail loudly, don't silently return wrong data
async def get_mission(mission_id: str) -> Mission:
    result = await repo.get(mission_id)
    if result is None:
        raise MissionNotFoundError(mission_id)  # propagate — never return None
    return result
```

## Saga — Multi-Step Compensation

Apply to: multi-step operations spanning multiple services.

```python
class MultiStepSaga:
    """Template for distributed multi-step operations."""
    steps: list[tuple[str, str]]  # [(step_name, compensate_name), ...]

    async def execute(self, context: dict) -> SagaResult:
        completed = []
        try:
            for step, _ in self.steps:
                await getattr(self, step)(context)
                completed.append(step)
            return SagaResult(success=True)
        except Exception as exc:
            # Compensate in reverse order
            for step in reversed(completed):
                undo = dict(self.steps)[step]
                with suppress(Exception):  # best-effort compensation
                    await getattr(self, undo)(context)
            return SagaResult(success=False, error=str(exc))
```
