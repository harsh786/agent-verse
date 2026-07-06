# AgentVerse Performance & Resilience Test Plan
**Date:** 2026-07-06
**Scope:** Backend API, Celery workers, Redis, Postgres, SSE streaming
**Purpose:** Phase 13 — validate the system holds up under real load and degrades gracefully
when dependencies fail

---

## 1. API Latency Smoke Tests

**Goal:** Verify that core API endpoints meet latency SLOs under single-user (baseline) conditions
before running any load tests. These should be automated in CI as `pytest-benchmark` or a simple
`httpx` timing harness.

### Target Endpoints and SLOs

| Endpoint | Method | Auth | P50 target | P99 target | Notes |
|---|---|---|---|---|---|
| `/health` | GET | None | < 20ms | < 50ms | Checks DB + Redis + Celery |
| `/tenants/me` | GET | API key | < 30ms | < 80ms | Tenant metadata read |
| `/goals` | GET | API key | < 80ms | < 200ms | Paginated list, RLS filtered |
| `POST /goals` | POST | API key | < 150ms | < 400ms | Write + Celery enqueue |
| `GET /goals/{id}` | GET | API key | < 50ms | < 120ms | Single row lookup |
| `GET /agents` | GET | API key | < 60ms | < 150ms | Agent list |
| `POST /connectors/{id}/test` | POST | API key | < 500ms | < 2s | External HTTP call (mocked in test) |
| `GET /knowledge/search` | GET | API key | < 200ms | < 600ms | pgvector hybrid search |
| `GET /observability/metrics` | GET | API key | < 100ms | < 300ms | Prometheus text output |

### Implementation

```python
# tests/perf/test_latency_smoke.py
import time, statistics
import httpx

BASE = "http://localhost:8000"
API_KEY = "test-tenant-key"

def measure(method, path, *, n=20, **kwargs):
    times = []
    with httpx.Client(base_url=BASE, headers={"X-API-Key": API_KEY}) as c:
        for _ in range(n):
            t0 = time.perf_counter()
            r = getattr(c, method)(path, **kwargs)
            times.append((time.perf_counter() - t0) * 1000)
            assert r.status_code < 500, f"{path} returned {r.status_code}"
    return {"p50": statistics.median(times), "p99": sorted(times)[int(n * 0.99)]}

def test_goals_list_latency():
    stats = measure("get", "/goals")
    assert stats["p50"] < 80, f"P50 {stats['p50']:.1f}ms exceeds SLO"
    assert stats["p99"] < 200, f"P99 {stats['p99']:.1f}ms exceeds SLO"
```

**Verification command:**
```bash
uv run pytest tests/perf/test_latency_smoke.py -v --tb=short
```

**Pre-conditions:** Local infra must be running (`colima start && docker-compose -f infra/docker-compose.yml up -d postgres redis`).

---

## 2. Rate Limit Behavior Tests

**Goal:** Verify that the Redis-backed sliding-window rate limiter correctly enforces per-plan RPM
limits and returns `429 Too Many Requests` at the boundary.

### Test Cases

#### 2.1 Free-Tier Limit Enforcement (30 RPM)

```python
# tests/tenancy/test_rate_limiter_behavior.py
async def test_free_tier_rate_limit_enforced(client, free_tenant_api_key):
    """Send 31 requests within 60 seconds; 31st must be 429."""
    responses = []
    for _ in range(31):
        r = await client.get("/goals", headers={"X-API-Key": free_tenant_api_key})
        responses.append(r.status_code)
    assert responses[-1] == 429, "31st request should be rate-limited"
    assert all(s == 200 for s in responses[:30]), "First 30 should succeed"
```

#### 2.2 Rate Limit Resets After Window

```python
async def test_rate_limit_resets_after_window(client, free_tenant_api_key, redis):
    """After consuming all tokens, wait for window to reset and verify requests succeed."""
    # consume all tokens
    for _ in range(30):
        await client.get("/goals", headers={"X-API-Key": free_tenant_api_key})
    # expire the sorted-set by fast-forwarding TTL in Redis (or freeze time)
    await redis.delete(f"rl:{free_tenant_api_key}:*")
    r = await client.get("/goals", headers={"X-API-Key": free_tenant_api_key})
    assert r.status_code == 200
```

#### 2.3 Signup Endpoint Rate Limit (5/minute per IP)

```python
async def test_signup_rate_limited(client):
    """6th signup from same IP in 1 minute returns 429."""
    for i in range(5):
        r = await client.post("/tenants/signup", json={
            "name": f"tenant-{i}", "email": f"t{i}@example.com", "plan": "free"
        })
        assert r.status_code in (201, 409)
    r = await client.post("/tenants/signup", json={
        "name": "tenant-x", "email": "tx@example.com", "plan": "free"
    })
    assert r.status_code == 429, "6th signup should be rate-limited"
```

#### 2.4 Enterprise Tenant Headroom (6000 RPM)

```python
async def test_enterprise_tenant_high_rpm(client, enterprise_api_key):
    """Enterprise tenants can make 100 requests without hitting 429."""
    for _ in range(100):
        r = await client.get("/goals", headers={"X-API-Key": enterprise_api_key})
        assert r.status_code == 200
```

**Verification command:**
```bash
uv run pytest tests/tenancy/test_rate_limiter_behavior.py -v
```

---

## 3. Circuit Breaker Tests

**Goal:** Verify that the circuit breaker in `app/reliability/circuit_breaker.py` correctly
transitions between CLOSED → OPEN → HALF-OPEN → CLOSED states.

### State Machine

```
CLOSED  ──[N failures]──▶  OPEN  ──[timeout]──▶  HALF-OPEN  ──[success]──▶  CLOSED
                                                               ──[failure]──▶  OPEN
```

### Test Cases

#### 3.1 Circuit Opens After Threshold Failures

```python
# tests/reliability/test_circuit_breaker.py
import asyncio
from app.reliability.circuit_breaker import CircuitBreaker, CircuitState

async def test_circuit_opens_after_failures():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1.0)
    assert cb.state == CircuitState.CLOSED

    for _ in range(3):
        with pytest.raises(Exception):
            async with cb:
                raise RuntimeError("simulated failure")

    assert cb.state == CircuitState.OPEN
```

#### 3.2 Circuit Rejects Calls When Open

```python
async def test_circuit_rejects_when_open():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=10.0)
    with pytest.raises(Exception):
        async with cb:
            raise RuntimeError("trip")
    # subsequent call should fail fast with CircuitOpenError, not execute the block
    with pytest.raises(CircuitOpenError):
        async with cb:
            pass  # should not be reached
```

#### 3.3 Circuit Transitions to Half-Open After Timeout

```python
async def test_circuit_half_open_after_timeout():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)
    with pytest.raises(Exception):
        async with cb:
            raise RuntimeError("trip")
    await asyncio.sleep(0.15)
    assert cb.state == CircuitState.HALF_OPEN
```

#### 3.4 Successful Call in Half-Open Closes Circuit

```python
async def test_circuit_closes_on_success_in_half_open():
    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)
    with pytest.raises(Exception):
        async with cb:
            raise RuntimeError("trip")
    await asyncio.sleep(0.15)
    async with cb:  # probe call succeeds
        pass
    assert cb.state == CircuitState.CLOSED
```

**Verification command:**
```bash
uv run pytest tests/reliability/test_circuit_breaker.py -v
```

---

## 4. SSE Reconnect Tests

**Goal:** Verify that the SSE goal stream correctly resumes delivery after a client disconnect,
honouring `Last-Event-ID` for missed-event replay.

### Test Cases

#### 4.1 Stream Delivers Events to Connected Client

```python
# tests/api/test_sse_reconnect.py
import httpx_sse

async def test_sse_delivers_goal_events(async_client, api_key, goal_id):
    """Basic SSE delivery — at least one event is received within 5 seconds."""
    events = []
    async with httpx_sse.aconnect_sse(
        async_client, "GET",
        f"/goals/{goal_id}/stream",
        headers={"X-API-Key": api_key}
    ) as source:
        async for sse in source.aiter_sse():
            events.append(sse)
            if len(events) >= 3:
                break
    assert len(events) >= 3
```

#### 4.2 Last-Event-ID Resumes from Correct Position

```python
async def test_sse_last_event_id_resume(async_client, api_key, goal_id):
    """
    Simulate a disconnect after receiving 3 events.
    Reconnect with Last-Event-ID = id_of_event_3.
    Verify events 4+ are delivered (not events 1-3 again).
    """
    first_events = []
    # First connection: collect 3 events
    async with httpx_sse.aconnect_sse(
        async_client, "GET", f"/goals/{goal_id}/stream",
        headers={"X-API-Key": api_key}
    ) as source:
        async for sse in source.aiter_sse():
            first_events.append(sse)
            if len(first_events) >= 3:
                break
    last_id = first_events[-1].id
    assert last_id is not None

    # Second connection: send Last-Event-ID header
    resumed_events = []
    async with httpx_sse.aconnect_sse(
        async_client, "GET", f"/goals/{goal_id}/stream",
        headers={"X-API-Key": api_key, "Last-Event-ID": last_id}
    ) as source:
        async for sse in source.aiter_sse():
            resumed_events.append(sse)
            if len(resumed_events) >= 2:
                break
    # Resumed events should come after the last seen event
    assert all(e.id > last_id for e in resumed_events if e.id)
```

#### 4.3 Stream Closes Cleanly on Goal Completion

```python
async def test_sse_stream_closes_on_terminal_status(async_client, api_key, completed_goal_id):
    """SSE stream for a completed goal should send a final event and close."""
    events = []
    async with httpx_sse.aconnect_sse(
        async_client, "GET", f"/goals/{completed_goal_id}/stream",
        headers={"X-API-Key": api_key}
    ) as source:
        async for sse in source.aiter_sse():
            events.append(sse)
    # Should have received a terminal event type
    terminal_types = {e.event for e in events}
    assert "complete" in terminal_types or "failed" in terminal_types
```

#### 4.4 Stream Rejects Cross-Tenant Goal ID

```python
async def test_sse_rejects_wrong_tenant_goal(async_client, tenant_a_key, tenant_b_goal_id):
    """Tenant A cannot stream tenant B's goal events — must get 404."""
    r = await async_client.get(
        f"/goals/{tenant_b_goal_id}/stream",
        headers={"X-API-Key": tenant_a_key}
    )
    assert r.status_code == 404
```

**Verification command:**
```bash
uv run pytest tests/api/test_sse_reconnect.py -v --tb=short
```

---

## 5. Redis Failure Tests

**Goal:** Verify that the system degrades gracefully when Redis is unavailable — rate limiting
falls back to in-process counters, SSE streams terminate cleanly, Celery connectivity is
restored on Redis recovery.

### Test Cases

#### 5.1 Rate Limiter Falls Back to In-Process Counter on Redis Failure

```python
# tests/tenancy/test_redis_degradation.py
from unittest.mock import patch, AsyncMock

async def test_rate_limiter_fallback_when_redis_down(client, api_key):
    """When Redis raises ConnectionError, rate limiter uses in-process fallback."""
    with patch("app.tenancy.rate_limiter.SlidingWindowRateLimiter._redis_check",
               side_effect=ConnectionError("Redis down")):
        # Should succeed — falls back to in-process counter
        r = await client.get("/goals", headers={"X-API-Key": api_key})
        assert r.status_code == 200
```

#### 5.2 GoalService Handles Redis Pub/Sub Failure Gracefully

```python
async def test_goal_service_sse_on_redis_failure(goal_service_with_broken_redis):
    """GoalService should log warning and not crash when Redis pub/sub is unavailable."""
    # Publish an event — should not raise, should log a warning
    import logging
    with pytest.warns(RuntimeWarning) or caplog.at_level(logging.WARNING):
        await goal_service_with_broken_redis.publish_event(
            goal_id="g-123", tenant_id="t-1",
            event={"type": "plan", "data": {}}
        )
```

#### 5.3 Cost Controller Blocks in Production on Redis Failure

```python
async def test_cost_controller_blocks_on_redis_failure_in_production(cost_controller):
    """In production mode, Redis failure on budget check should block (not allow)."""
    with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
        with patch.object(cost_controller, "_redis", side_effect=ConnectionError):
            result = await cost_controller.check_and_record(
                tenant_id="t-1", goal_id="g-1", cost_usd=0.01
            )
            assert result is False, "Should block when Redis unavailable in production"
```

#### 5.4 Health Check Reports Unhealthy When Redis Is Down

```python
async def test_health_returns_503_when_redis_down(client, broken_redis):
    """GET /health should return 503 when Redis is unreachable."""
    r = await client.get("/health")
    assert r.status_code == 503
    data = r.json()
    assert any(check["name"] == "redis" and not check["healthy"] for check in data["checks"])
```

**Verification command:**
```bash
uv run pytest tests/tenancy/test_redis_degradation.py tests/api/test_health.py -v
```

---

## 6. Provider Fallback Tests (FakeProvider)

**Goal:** Verify that when no real LLM API key is configured, `FakeProvider` kicks in as the
deterministic fallback and the agent loop completes without errors.

### Test Cases

#### 6.1 FakeProvider Is Selected When No API Keys Are Set

```python
# tests/providers/test_provider_fallback.py
from app.providers.base import get_provider
import os

def test_fake_provider_selected_without_keys(monkeypatch):
    """No API keys → FakeProvider is returned."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    provider = get_provider()
    assert provider.__class__.__name__ == "FakeProvider"
```

#### 6.2 FakeProvider Returns Deterministic Completion

```python
async def test_fake_provider_deterministic_response():
    """FakeProvider always returns the same response for the same input."""
    from app.providers.base import FakeProvider, CompletionRequest, Message
    fp = FakeProvider()
    req = CompletionRequest(messages=[Message(role="user", content="Hello")])
    r1 = await fp.complete(req)
    r2 = await fp.complete(req)
    assert r1.content == r2.content, "FakeProvider should be deterministic"
```

#### 6.3 Agent Loop Completes With FakeProvider (No Keys)

```python
async def test_agent_loop_completes_with_fake_provider(
    goal_service, fake_agent, monkeypatch
):
    """Submit a goal with no LLM keys; agent loop should complete (not crash)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    goal_id = await goal_service.submit_goal(
        description="List files in /tmp",
        agent_id=fake_agent.agent_id,
        tenant_ctx=test_tenant_ctx(),
    )
    # Allow the loop to run (FakeProvider responds immediately)
    import asyncio
    await asyncio.sleep(0.5)
    goal = await goal_service.get_goal(goal_id, tenant_ctx=test_tenant_ctx())
    assert goal.status in ("complete", "failed"), f"Unexpected status: {goal.status}"
```

#### 6.4 FakeProvider Embedding Returns Correct Dimension

```python
async def test_fake_provider_embedding_dimension():
    """FakeProvider embeddings must match the pgvector dimension (1536 by default)."""
    from app.providers.base import FakeProvider
    fp = FakeProvider()
    emb = await fp.embed(["test sentence"])
    assert len(emb[0]) == 1536
```

**Verification command:**
```bash
uv run pytest tests/providers/test_provider_fallback.py -v -m "not slow"
```

---

## 7. Load Test Plan

> **Note:** These load tests should NOT be run in CI automatically. Run them manually in a
> staging environment against a deployed stack (not local dev). The plan uses **k6** as the
> primary tool; Locust scripts are provided as alternatives for Python teams.

### Pre-conditions

- Staging environment running (Kubernetes or docker-compose full stack)
- At least 2 API replicas, 2 Celery workers
- Redis Sentinel configured (or accept degraded single-node)
- Postgres with pgvector extension
- 5 pre-created tenants: 4 free-tier, 1 enterprise
- FakeProvider configured (no real LLM keys in load test environment)

---

### 7.1 Goal Submission Load Test — 100 Concurrent Users

**Objective:** Verify the system accepts 100 concurrent goal submissions without significant
error rate or latency degradation.

**Target:**
- Throughput: ≥ 80 goals/second
- P95 submission latency: < 500ms
- Error rate: < 1%
- No Celery queue depth > 500 after 60 seconds

**k6 script sketch:**
```javascript
// k6/goal_submission_load.js
import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 100,           // 100 virtual users
  duration: "60s",
  thresholds: {
    http_req_duration: ["p(95)<500"],
    http_req_failed: ["rate<0.01"],
  },
};

const TENANTS = [
  { key: __ENV.TENANT_1_KEY },
  { key: __ENV.TENANT_2_KEY },
  // ... 5 tenants
];

export default function () {
  const tenant = TENANTS[__VU % TENANTS.length];
  const res = http.post(
    `${__ENV.BASE_URL}/goals`,
    JSON.stringify({ description: `Load test goal ${Date.now()}`, agent_id: __ENV.AGENT_ID }),
    { headers: { "Content-Type": "application/json", "X-API-Key": tenant.key } }
  );
  check(res, {
    "goal submitted 202": (r) => r.status === 202,
  });
  sleep(0.5);
}
```

**Locust alternative:**
```python
# locust/goal_submission.py
from locust import HttpUser, task, between

class GoalSubmitter(HttpUser):
    wait_time = between(0.5, 1.0)
    host = "http://staging.agentverse.local"

    @task
    def submit_goal(self):
        self.client.post(
            "/goals",
            json={"description": "Load test goal", "agent_id": "agent-123"},
            headers={"X-API-Key": self.environment.parsed_options.api_key},
        )
```

**Run command:**
```bash
k6 run -e BASE_URL=http://staging.agentverse.local \
       -e TENANT_1_KEY=key1 -e AGENT_ID=agent-123 \
       k6/goal_submission_load.js
```

---

### 7.2 SSE Stream Load Test — 100 Concurrent Streams

**Objective:** Verify 100 simultaneous SSE connections can be sustained without memory leak or
dropped events.

**Target:**
- All 100 streams receive events for their respective goals
- API process RSS memory does not grow by more than 50MB over 120 seconds
- No `QueueFull` errors in logs
- All streams receive a terminal event (complete/failed) within 30 seconds

**k6 script sketch:**
```javascript
// k6/sse_load.js — k6 has native SSE support via k6-sse
import { EventSourceClient } from "k6/x/sse";

export const options = {
  vus: 100,
  duration: "120s",
  thresholds: {
    "sse_events_received": ["count>500"],
  },
};

export default function () {
  // Pre-submit a goal, then open SSE stream for it
  const goalRes = http.post(`${BASE_URL}/goals`, ...);
  const goalId = JSON.parse(goalRes.body).goal_id;

  const client = new EventSourceClient(`${BASE_URL}/goals/${goalId}/stream`, {
    headers: { "X-API-Key": API_KEY },
  });
  client.connect();
  // Wait for terminal event or 30s timeout
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    const event = client.nextEvent(1000);
    if (!event) continue;
    if (event.type === "complete" || event.type === "failed") break;
  }
  client.close();
}
```

**Memory monitoring:**
```bash
# While k6 is running, track API pod RSS every 5s:
watch -n5 "kubectl top pods -l app=agentverse-api --no-headers | awk '{print \$3}'"
```

---

### 7.3 Knowledge Search Load Test — 50 Concurrent Searches

**Objective:** Verify that pgvector hybrid search handles 50 concurrent searches with acceptable
latency.

**Target:**
- P50 search latency: < 300ms
- P95 search latency: < 800ms
- Error rate: < 0.5%
- No Postgres connection pool exhaustion (pool_size=20 per replica)

**k6 script sketch:**
```javascript
// k6/knowledge_search_load.js
export const options = {
  vus: 50,
  duration: "60s",
  thresholds: {
    http_req_duration: ["p(50)<300", "p(95)<800"],
    http_req_failed: ["rate<0.005"],
  },
};

const QUERIES = [
  "how to configure API rate limiting",
  "agent execution loop state machine",
  "tenant isolation postgres RLS",
  "LangGraph checkpointing Redis",
  "HITL approval workflow",
];

export default function () {
  const query = QUERIES[Math.floor(Math.random() * QUERIES.length)];
  const res = http.get(
    `${BASE_URL}/knowledge/search?q=${encodeURIComponent(query)}&strategy=HYBRID&limit=10`,
    { headers: { "X-API-Key": API_KEY } }
  );
  check(res, { "search 200": (r) => r.status === 200 });
  sleep(1);
}
```

**Pre-condition:** Load at least 50 documents into the knowledge store before running.
```bash
# Seed knowledge store:
uv run python scripts/seed_knowledge.py --tenant-key $TENANT_KEY --docs-dir docs/
```

---

### 7.4 Load Test Reporting

After each load test run, capture:

1. **k6 summary JSON:**
   ```bash
   k6 run --out json=results/load-$(date +%Y%m%d-%H%M%S).json k6/goal_submission_load.js
   ```

2. **Celery queue depth at peak:**
   ```bash
   redis-cli llen goals && redis-cli llen goals.enterprise
   ```

3. **Postgres connection count:**
   ```sql
   SELECT count(*) FROM pg_stat_activity WHERE application_name LIKE '%agentverse%';
   ```

4. **Error logs from API pods:**
   ```bash
   kubectl logs -l app=agentverse-api --since=2m | grep -E "ERROR|CRITICAL|500"
   ```

5. **Alembic / schema sanity check after test:**
   ```bash
   uv run alembic current
   ```

---

## 8. Resilience Test Matrix Summary

| Test Category | Automated | Manual | Priority | CI? |
|---|---|---|---|---|
| API latency smoke | Yes (pytest) | No | P0 | Yes |
| Rate limit enforcement | Yes (pytest) | No | P0 | Yes |
| Circuit breaker state machine | Yes (pytest) | No | P1 | Yes |
| SSE reconnect + Last-Event-ID | Yes (pytest) | No | P1 | Yes |
| SSE cross-tenant rejection | Yes (pytest) | No | P0 | Yes |
| Redis failure degradation | Yes (pytest + mocks) | No | P1 | Yes |
| FakeProvider fallback | Yes (pytest) | No | P1 | Yes |
| Goal submission 100-user | No (k6) | Yes (staging) | P1 | No |
| SSE 100-stream load | No (k6) | Yes (staging) | P1 | No |
| Knowledge search 50-user | No (k6) | Yes (staging) | P2 | No |
| Celery worker crash + resume | Yes (pytest + testcontainers) | No | P1 | Integration |
| Redis restart + recovery | No | Yes (staging) | P1 | No |
