# Community 513

> 11 nodes · cohesion 0.18

## Key Concepts

- **ProviderCircuitBreaker** (9 connections) — `agent-verse-backend/app/providers/circuit_breaker.py`
- **.before_call()** (2 connections) — `agent-verse-backend/app/providers/circuit_breaker.py`
- **.is_open()** (2 connections) — `agent-verse-backend/app/providers/circuit_breaker.py`
- **.record_failure()** (2 connections) — `agent-verse-backend/app/providers/circuit_breaker.py`
- **.record_success()** (2 connections) — `agent-verse-backend/app/providers/circuit_breaker.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/providers/circuit_breaker.py`
- **Per-provider circuit breaker to prevent cascading LLM failures.** (1 connections) — `agent-verse-backend/app/providers/circuit_breaker.py`
- **Return True if the circuit is open (provider unavailable).** (1 connections) — `agent-verse-backend/app/providers/circuit_breaker.py`
- **Reset failure count and close the circuit.** (1 connections) — `agent-verse-backend/app/providers/circuit_breaker.py`
- **Increment failure count; open the circuit when threshold is reached.** (1 connections) — `agent-verse-backend/app/providers/circuit_breaker.py`
- **Track half-open probe calls.** (1 connections) — `agent-verse-backend/app/providers/circuit_breaker.py`

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (2 shared connections)
- [Community 599](Community_599.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/providers/circuit_breaker.py`

## Audit Trail

- EXTRACTED: 12 (92%)
- INFERRED: 1 (8%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*