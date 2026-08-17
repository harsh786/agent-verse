# Monitoring, APM & Logging Skill — AgentVerse

## Stack (Already Configured — Just Use It)

```
Tracing:   OpenTelemetry → Jaeger  (spans per request, LLM call, DB query)
Metrics:   Prometheus → Grafana    (counters, histograms, gauges)
Logging:   structlog → ELK/Loki    (structured JSON, searchable)
APM:       OTel APM → correlate traces + metrics + logs by trace_id
Alerting:  Prometheus AlertManager → PagerDuty (P1) / Slack (P2)
```

---

## OpenTelemetry — Mandatory Instrumentation

### Every New Service Method

```python
from opentelemetry import trace, metrics
import structlog

tracer = trace.get_tracer(__name__)
meter  = metrics.get_meter(__name__)
log    = structlog.get_logger(__name__)

# Counters and histograms at module level (created once):
mission_created_counter = meter.create_counter(
    "agentverse.missions.created",
    description="Total missions created",
)
mission_duration = meter.create_histogram(
    "agentverse.missions.execution_seconds",
    description="Mission execution wall-clock time",
    unit="s",
)

class MissionService:
    async def create(self, req: CreateMissionRequest) -> Mission:
        start = time.monotonic()
        with tracer.start_as_current_span("mission.create") as span:
            span.set_attribute("tenant_id",       str(self._tenant.id))
            span.set_attribute("mission.priority", req.priority)

            log.info("mission.create.start",
                tenant_id=str(self._tenant.id),
                priority=req.priority,
            )

            try:
                result = await self._repo.create(req)

                # Record metrics
                mission_created_counter.add(1, {
                    "tenant_id": str(self._tenant.id),
                    "priority":  req.priority,
                })
                mission_duration.record(
                    time.monotonic() - start,
                    {"tenant_id": str(self._tenant.id)},
                )

                span.set_attribute("mission.id", str(result.id))
                log.info("mission.create.done",
                    mission_id=str(result.id),
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
                return result

            except Exception as exc:
                span.record_exception(exc)
                span.set_status(trace.StatusCode.ERROR)
                log.error("mission.create.failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    tenant_id=str(self._tenant.id),
                )
                raise
```

### LLM Call Instrumentation (Critical for Cost Tracking)

```python
async def _call_llm(self, request: CompletionRequest) -> CompletionResponse:
    with tracer.start_as_current_span("llm.call") as span:
        span.set_attribute("llm.provider",     request.provider)
        span.set_attribute("llm.model",        request.model)
        span.set_attribute("llm.temperature",  request.temperature)
        span.set_attribute("tenant_id",        str(self._tenant.id))

        start = time.monotonic()
        response = await self._provider.complete(request)
        elapsed = time.monotonic() - start

        # Track token usage for billing
        span.set_attribute("llm.input_tokens",  response.input_tokens)
        span.set_attribute("llm.output_tokens", response.output_tokens)
        span.set_attribute("llm.latency_ms",    int(elapsed * 1000))

        # Metrics
        llm_token_counter.add(response.input_tokens, {
            "direction": "input", "model": request.model, "tenant_id": str(self._tenant.id)
        })
        llm_token_counter.add(response.output_tokens, {
            "direction": "output", "model": request.model, "tenant_id": str(self._tenant.id)
        })
        llm_cost_counter.add(response.cost_usd, {
            "provider": request.provider, "tenant_id": str(self._tenant.id)
        })

        return response
```

---

## Structured Logging Rules

```python
# ALWAYS use structlog — NEVER print() or logging.info()
log = structlog.get_logger(__name__)

# Log event naming: domain.verb.state
# domain: mission, agent, graphify, auth, knowledge, webhook
# verb:   create, update, delete, execute, complete
# state:  start, done, failed, retry, skip, timeout

# Required fields on EVERY log event:
log.info("mission.create.done",
    # Mandatory:
    tenant_id=str(tenant_id),          # always
    # Domain-specific:
    mission_id=str(mission.id),
    duration_ms=elapsed_ms,
    # Optional context:
    request_id=request_id,
    correlation_id=correlation_id,
)

# Sensitive fields — NEVER log these:
# password, api_key, token, secret, credit_card, ssn, email (unless business need)
# Use log.bind(request_id=...) to add context for all subsequent logs in a request:
log = log.bind(request_id=x_request_id, tenant_id=str(tenant_id))
```

### Log Levels

```python
log.debug(...)    # Dev-only, sampled 1% in prod. For verbose tracing.
log.info(...)     # Business events. 10% sampled in prod.
log.warning(...)  # Recoverable issues: retries, fallbacks, degraded.
log.error(...)    # Failures needing attention. 100% in prod. Creates alert.
log.critical(...) # System failure. Pages on-call immediately.
```

---

## Prometheus Metrics

### Standard Metrics Every Domain Must Emit

```python
# In app/<domain>/metrics.py  (create per domain)
from opentelemetry import metrics
meter = metrics.get_meter("agentverse.<domain>")

# Pattern: agentverse.<domain>.<noun>_<unit>
# Example for missions domain:
MISSIONS_CREATED = meter.create_counter(
    "agentverse.missions.created_total",
    description="Total missions created",
)
MISSIONS_DURATION = meter.create_histogram(
    "agentverse.missions.duration_seconds",
    description="Mission execution duration (seconds)",
    unit="s",
    explicit_bucket_boundaries=[0.1, 0.5, 1.0, 5.0, 30.0, 120.0, 600.0],
)
MISSIONS_ACTIVE = meter.create_observable_gauge(
    "agentverse.missions.active_count",
    description="Currently active missions",
    callbacks=[lambda options: [(active_count(), {"tenant_id": t}) for t in tenants]],
)
```

### Alert Rules (Already in Prometheus Config)

```yaml
# Reference: these alerts fire based on your metrics
groups:
  - name: agentverse
    rules:
      # Page on-call if error rate > 1% for 5 minutes
      - alert: HighAPIErrorRate
        expr: rate(agentverse_http_requests_total{status_code=~"5.."}[5m]) /
              rate(agentverse_http_requests_total[5m]) > 0.01
        for: 5m
        labels: { severity: critical }
        annotations:
          runbook: https://runbooks.agentverse.io/high-error-rate

      # Slack alert if P99 latency > 2s
      - alert: HighP99Latency
        expr: histogram_quantile(0.99, rate(agentverse_http_duration_seconds_bucket[5m])) > 2
        for: 10m
        labels: { severity: warning }

      # LLM cost budget exceeded
      - alert: LLMCostBudgetExceeded
        expr: sum(agentverse_llm_cost_usd_total) by (tenant_id) > 100
        labels: { severity: warning }
```

---

## APM Correlation

```python
# Every request carries a trace_id that correlates:
# - HTTP access logs
# - structlog events
# - OTel spans
# - Celery task logs
# - Database slow query logs

# Middleware automatically injects trace context:
# X-Trace-ID: <trace_id>
# X-Span-ID:  <span_id>

# To manually correlate in logs:
from opentelemetry import trace as ot_trace

def get_trace_context() -> dict:
    span = ot_trace.get_current_span()
    ctx = span.get_span_context()
    return {
        "trace_id": format(ctx.trace_id, "032x") if ctx.is_valid else "",
        "span_id":  format(ctx.span_id, "016x") if ctx.is_valid else "",
    }

log.info("mission.created", **get_trace_context(), mission_id=str(mission.id))
```

---

## Frontend APM (Core Web Vitals + Error Tracking)

```typescript
// src/lib/monitoring.ts
import { onLCP, onFID, onCLS, onINP, onTTFB } from 'web-vitals';
import * as Sentry from '@sentry/react';

// Initialize Sentry error tracking
Sentry.init({
  dsn:         import.meta.env.VITE_SENTRY_DSN,
  environment: import.meta.env.VITE_ENV,
  tracesSampleRate: 0.1,    // 10% performance traces
  profilesSampleRate: 0.1,
  integrations: [
    Sentry.browserTracingIntegration(),
    Sentry.replayIntegration({ maskAllText: true, blockAllMedia: true }),
  ],
});

// Report Core Web Vitals to analytics
function reportWebVital(metric: any) {
  // Send to your analytics endpoint
  fetch('/api/v1/analytics/vitals', {
    method: 'POST',
    body: JSON.stringify({
      metric: metric.name,
      value: metric.value,
      rating: metric.rating,   // 'good' | 'needs-improvement' | 'poor'
      id: metric.id,
    }),
  });
}

onLCP(reportWebVital);    // Largest Contentful Paint — target: < 2.5s
onINP(reportWebVital);    // Interaction to Next Paint — target: < 200ms
onCLS(reportWebVital);    // Cumulative Layout Shift  — target: < 0.1
onTTFB(reportWebVital);   // Time to First Byte       — target: < 800ms
```
