---
applyTo: "agent-verse-backend/**/*.py"
---

# Observability Instructions — AgentVerse

## OpenTelemetry Setup (Already Configured — Just Use It)

The OTel SDK is initialized in `app/main.py`. You only need to import and use:

```python
from opentelemetry import trace, metrics
import structlog

tracer  = trace.get_tracer(__name__)    # one per module
meter   = metrics.get_meter(__name__)   # one per module
log     = structlog.get_logger(__name__)
```

## Tracing — Every Service Method Gets a Span

```python
class MissionService:
    async def create_mission(self, req: CreateMissionRequest) -> Mission:
        with tracer.start_as_current_span("mission.create") as span:
            # Set standard attributes
            span.set_attribute("tenant_id", str(self._tenant.id))
            span.set_attribute("mission.priority", req.priority)

            try:
                result = await self._repo.create(req)
                # Set result attributes
                span.set_attribute("mission.id", str(result.id))
                span.set_attribute("result.status", "created")
                return result
            except Exception as exc:
                # Always record exceptions in spans
                span.record_exception(exc)
                span.set_status(trace.StatusCode.ERROR, str(exc))
                raise
```

### Span Naming Convention
- Format: `{domain}.{verb}` — all lowercase, dot-separated
- Examples: `mission.create`, `agent.execute`, `graphify.build_graph`, `mcp.call_tool`
- LangGraph nodes: `agent.node.{node_name}` e.g. `agent.node.planner`, `agent.node.executor`
- Celery tasks: `celery.{task_name}` e.g. `celery.graphify.process`

### Standard Span Attributes
```python
# Always set these:
span.set_attribute("tenant_id", str(tenant_id))
span.set_attribute("service.name", "agentverse-backend")

# Set domain-specific:
span.set_attribute("mission.id", str(mission_id))
span.set_attribute("agent.id", str(agent_id))
span.set_attribute("model.provider", "anthropic")
span.set_attribute("model.name", "claude-3-5-sonnet-20241022")
span.set_attribute("tokens.input", input_tokens)
span.set_attribute("tokens.output", output_tokens)
span.set_attribute("result.count", len(results))
```

## Metrics — Prometheus Counters/Histograms

```python
# Define metrics at module level:
mission_counter = meter.create_counter(
    name="agentverse.missions.total",
    description="Total missions created",
    unit="1",
)
mission_duration = meter.create_histogram(
    name="agentverse.missions.duration_seconds",
    description="Mission execution duration",
    unit="s",
)
llm_cost_counter = meter.create_counter(
    name="agentverse.llm.cost_usd",
    description="Total LLM cost in USD",
    unit="$",
)

# Record metrics with labels:
mission_counter.add(1, {
    "tenant_id": str(tenant_id),
    "priority": priority,
    "status": "created",
})
mission_duration.record(elapsed_seconds, {"tenant_id": str(tenant_id)})
```

### Standard Metrics to Emit

| Metric | Type | Labels |
|--------|------|--------|
| `agentverse.missions.total` | Counter | tenant_id, status, priority |
| `agentverse.missions.duration_seconds` | Histogram | tenant_id |
| `agentverse.agent.calls.total` | Counter | tenant_id, model, status |
| `agentverse.llm.tokens.total` | Counter | tenant_id, provider, model, direction |
| `agentverse.llm.cost_usd` | Counter | tenant_id, provider |
| `agentverse.mcp.calls.total` | Counter | tenant_id, connector, tool, status |
| `agentverse.http.requests.total` | Counter | method, path, status_code |
| `agentverse.http.duration_seconds` | Histogram | method, path |
| `agentverse.celery.tasks.total` | Counter | task_name, status |
| `agentverse.db.queries.total` | Counter | operation, table |

## Logging — Structlog Always

```python
import structlog
log = structlog.get_logger(__name__)

# ✅ CORRECT — structured, filterable, searchable
log.info("mission.created",
    mission_id=str(mission.id),
    tenant_id=str(tenant_id),
    priority=mission.priority,
    duration_ms=int(elapsed * 1000),
)

log.error("mission.failed",
    mission_id=str(mission_id),
    error=str(exc),
    error_type=type(exc).__name__,
    tenant_id=str(tenant_id),
)

# ❌ NEVER — unstructured, unsearchable
print(f"Mission {mission_id} created")
logger.info(f"Processing {tenant_id}")
```

### Log Event Naming
- Format: `{domain}.{verb}.{state}` — all lowercase, dot-separated
- States: `start`, `done`, `failed`, `retry`, `skipped`
- Examples: `mission.create.start`, `mission.create.done`, `agent.execute.failed`

### Log Levels
```python
log.debug(...)    # Dev only — sampling 1% in prod
log.info(...)     # Business events — 10% sampling in prod
log.warning(...)  # Recoverable issues — 100% in prod
log.error(...)    # Failures — 100% in prod (alert on these)
```

### Sensitive Field Sanitisation
```python
# The LogSanitizer processor is already in the structlog chain.
# Never log these fields — they will be REDACTED automatically:
# password, api_key, token, secret, credit_card, ssn, email, ip_address

# For extra safety, explicitly mask:
log.info("user.login", email="[REDACTED]", tenant_id=str(tenant_id))
```

## Celery Task Observability

```python
@celery_app.task(bind=True, name="domain.my_task")
def my_task(self, tenant_id: str, entity_id: str) -> dict:
    with tracer.start_as_current_span("celery.domain.my_task") as span:
        span.set_attribute("tenant_id", tenant_id)
        span.set_attribute("entity_id", entity_id)
        span.set_attribute("celery.task_id", self.request.id)
        span.set_attribute("celery.retries", self.request.retries)

        log.info("task.start",
            task_id=self.request.id,
            tenant_id=tenant_id,
            entity_id=entity_id,
        )
        # ... work ...
```

## LangGraph Node Tracing

```python
# In every LangGraph node function:
async def plan_node(state: AgentState) -> AgentState:
    with tracer.start_as_current_span("agent.node.planner") as span:
        span.set_attribute("tenant_id", state["tenant_id"])
        span.set_attribute("goal.id", state["goal_id"])
        span.set_attribute("iteration", state["iteration"])
        # ... planning ...
        span.set_attribute("plan.steps_count", len(state["plan"]))
    return state
```

## Health Check Endpoints

```python
# /health — process alive (no DB, no Redis)
@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "agentverse-backend"}

# /ready — dependencies checked
@router.get("/ready")
async def ready(db: AsyncSession = Depends(get_db)) -> dict:
    # Check DB
    await db.execute(text("SELECT 1"))
    # Check Redis
    await redis.ping()
    return {"status": "ready", "checks": {"db": "ok", "redis": "ok"}}
```

## Alerting Rules (Reference)

```yaml
# These rules are pre-configured in Prometheus — emit the correct metrics
# and alerts fire automatically:
alerts:
  - name: HighErrorRate
    expr: rate(agentverse_http_requests_total{status_code=~"5.."}[5m]) > 0.01
    severity: critical

  - name: HighP99Latency
    expr: histogram_quantile(0.99, agentverse_http_duration_seconds_bucket) > 2
    severity: warning

  - name: LLMCostBudgetExceeded
    expr: sum(agentverse_llm_cost_usd) by (tenant_id) > 100
    severity: warning

  - name: CeleryQueueBacklog
    expr: celery_queue_length > 1000
    severity: critical
```
