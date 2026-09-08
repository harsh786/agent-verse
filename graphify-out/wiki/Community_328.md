# Community 328

> 20 nodes · cohesion 0.13

## Key Concepts

- **tracing.py** (12 connections) — `agent-verse-backend/app/observability/tracing.py`
- **configure_tracing()** (6 connections) — `agent-verse-backend/app/observability/tracing.py`
- **_NoOpSpanContext** (6 connections) — `agent-verse-backend/app/observability/tracing.py`
- **get_tracer()** (5 connections) — `agent-verse-backend/app/observability/tracing.py`
- **_add_console_span_processor()** (4 connections) — `agent-verse-backend/app/observability/tracing.py`
- **_NoOpTracer** (4 connections) — `agent-verse-backend/app/observability/tracing.py`
- **.start_as_current_span()** (3 connections) — `agent-verse-backend/app/observability/tracing.py`
- **Any** (3 connections)
- **.record_exception()** (2 connections) — `agent-verse-backend/app/observability/tracing.py`
- **safe_pattern_attributes()** (2 connections) — `agent-verse-backend/app/observability/tracing.py`
- **.__enter__()** (1 connections) — `agent-verse-backend/app/observability/tracing.py`
- **.__exit__()** (1 connections) — `agent-verse-backend/app/observability/tracing.py`
- **.set_attribute()** (1 connections) — `agent-verse-backend/app/observability/tracing.py`
- **Exception** (1 connections)
- **OpenTelemetry tracing bootstrap. Instruments the FastAPI app and configures an…** (1 connections) — `agent-verse-backend/app/observability/tracing.py`
- **Configure OpenTelemetry tracing. When OTLP endpoint is set: exports to…** (1 connections) — `agent-verse-backend/app/observability/tracing.py`
- **Add an in-memory span store for local tracing without OTLP.** (1 connections) — `agent-verse-backend/app/observability/tracing.py`
- **Get a named OTel tracer. No-ops gracefully when OTel is not installed.** (1 connections) — `agent-verse-backend/app/observability/tracing.py`
- **Return a bounded trace-attribute map that cannot contain tenant or content data.** (1 connections) — `agent-verse-backend/app/observability/tracing.py`
- **Fallback tracer when opentelemetry is unavailable (tests, minimal envs).** (1 connections) — `agent-verse-backend/app/observability/tracing.py`

## Relationships

- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (3 shared connections)
- [Community 65](Community_65.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (2 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Community 352](Community_352.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/observability/tracing.py`

## Audit Trail

- EXTRACTED: 33 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*