# Community 471

> 13 nodes · cohesion 0.17

## Key Concepts

- **CircuitBreaker** (6 connections) — `agent-verse-backend/app/triggers/circuit_breaker.py`
- **CircuitBreakerRegistry** (6 connections) — `agent-verse-backend/app/triggers/circuit_breaker.py`
- **TriggerCircuitBreaker** (6 connections) — `agent-verse-backend/app/triggers/circuit_breaker.py`
- **.get()** (2 connections) — `agent-verse-backend/app/triggers/circuit_breaker.py`
- **.is_open()** (2 connections) — `agent-verse-backend/app/triggers/circuit_breaker.py`
- **.prometheus_state_value()** (2 connections) — `agent-verse-backend/app/triggers/circuit_breaker.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/triggers/circuit_breaker.py`
- **Per-trigger circuit breaker state machine. States: closed → open → half_open →…** (1 connections) — `agent-verse-backend/app/triggers/circuit_breaker.py`
- **Return True if the circuit is open (blocking).** (1 connections) — `agent-verse-backend/app/triggers/circuit_breaker.py`
- **0=closed, 1=half_open, 2=open — for Prometheus gauge.** (1 connections) — `agent-verse-backend/app/triggers/circuit_breaker.py`
- **In-process registry of per-trigger circuit breakers.** (1 connections) — `agent-verse-backend/app/triggers/circuit_breaker.py`
- **.record_failure()** (1 connections) — `agent-verse-backend/app/triggers/circuit_breaker.py`
- **.record_success()** (1 connections) — `agent-verse-backend/app/triggers/circuit_breaker.py`

## Relationships

- [Community 79](Community_79.md) (4 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/triggers/circuit_breaker.py`

## Audit Trail

- EXTRACTED: 17 (94%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 1 (6%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*