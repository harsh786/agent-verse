---
description: "Add complete OTel instrumentation, structlog events, and Prometheus metrics to a module"
---

# Add Observability to Module

Add complete, world-class observability to `app/{{module}}/`.

## Target
- **Module**: `app/{{module}}/`
- **Service class**: `{{ClassName}}Service`
- **Operations**: {{operations}}

## What to Add

### 1. Imports (top of service.py)
```python
from opentelemetry import trace, metrics
import structlog
import time

tracer = trace.get_tracer(__name__)
meter  = metrics.get_meter(__name__)
log    = structlog.get_logger(__name__)
```

### 2. Metrics (module-level, created once)
Create for each operation:
- `Counter`: agentverse.{{module}}.{{verb}}_total (labels: tenant_id, status)
- `Histogram`: agentverse.{{module}}.{{verb}}_seconds (labels: tenant_id)

### 3. Every Service Method Gets
```python
async def {{verb}}(self, ...) -> ...:
    start = time.monotonic()
    with tracer.start_as_current_span("{{module}}.{{verb}}") as span:
        span.set_attribute("tenant_id", str(self._tenant.id))
        # ... domain-specific attributes
        log.info("{{module}}.{{verb}}.start", tenant_id=str(self._tenant.id))
        try:
            result = await ...
            # Record success metrics
            {{module}}_counter.add(1, {"tenant_id": ..., "status": "success"})
            {{module}}_duration.record(time.monotonic() - start, ...)
            span.set_attribute("result.id", str(result.id))
            log.info("{{module}}.{{verb}}.done", ...)
            return result
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(trace.StatusCode.ERROR)
            {{module}}_counter.add(1, {"tenant_id": ..., "status": "error"})
            log.error("{{module}}.{{verb}}.failed", error=str(exc), ...)
            raise
```

### 4. Celery Tasks
Add same pattern with `celery.task_id` attribute.

### 5. Tests
Write tests that verify:
- `test_{{verb}}_emits_otel_span_with_tenant_id`
- `test_{{verb}}_records_prometheus_counter`
- `test_{{verb}}_error_records_exception_in_span`
- `test_{{verb}}_logs_structured_event`

## Constraints
- Span names: `{domain}.{verb}` — all lowercase, dot-separated
- Log events: `{domain}.{verb}.{state}` — start, done, failed
- NEVER log: password, api_key, token, secret, email (auto-redacted by LogSanitizer)
- Metric names: `agentverse.{domain}.{noun}_{unit}` pattern
