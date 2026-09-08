# Community 304

> 22 nodes · cohesion 0.12

## Key Concepts

- **RedisCircuitBreaker** (21 connections) — `agent-verse-backend/app/reliability/redis_circuit_breaker.py`
- **CircuitState** (6 connections) — `agent-verse-backend/app/reliability/circuit_breaker.py`
- **redis_circuit_breaker.py** (5 connections) — `agent-verse-backend/app/reliability/redis_circuit_breaker.py`
- **._key()** (5 connections) — `agent-verse-backend/app/reliability/redis_circuit_breaker.py`
- **.can_call_async()** (4 connections) — `agent-verse-backend/app/reliability/redis_circuit_breaker.py`
- **.get_state()** (4 connections) — `agent-verse-backend/app/reliability/redis_circuit_breaker.py`
- **.state()** (3 connections) — `agent-verse-backend/app/reliability/circuit_breaker.py`
- **.__init__()** (3 connections) — `agent-verse-backend/app/reliability/redis_circuit_breaker.py`
- **.record_failure_async()** (3 connections) — `agent-verse-backend/app/reliability/redis_circuit_breaker.py`
- **.record_success_async()** (3 connections) — `agent-verse-backend/app/reliability/redis_circuit_breaker.py`
- **.state()** (2 connections) — `agent-verse-backend/app/reliability/redis_circuit_breaker.py`
- **Any** (1 connections)
- **Redis-backed circuit breaker — state shared across all worker replicas. Each…** (1 connections) — `agent-verse-backend/app/reliability/redis_circuit_breaker.py`
- **Record a failure. Opens the circuit once ``failure_threshold`` is reached.** (1 connections) — `agent-verse-backend/app/reliability/redis_circuit_breaker.py`
- **Record a success — resets the circuit to CLOSED and clears all counters.** (1 connections) — `agent-verse-backend/app/reliability/redis_circuit_breaker.py`
- **Circuit breaker backed by Redis for cross-replica state sharing. Args:…** (1 connections) — `agent-verse-backend/app/reliability/redis_circuit_breaker.py`
- **Return the current circuit state from Redis.** (1 connections) — `agent-verse-backend/app/reliability/redis_circuit_breaker.py`
- **Return True if a call is allowed now (checks Redis state). Handles the OPEN →…** (1 connections) — `agent-verse-backend/app/reliability/redis_circuit_breaker.py`
- **.can_call()** (1 connections) — `agent-verse-backend/app/reliability/redis_circuit_breaker.py`
- **.is_closed()** (1 connections) — `agent-verse-backend/app/reliability/redis_circuit_breaker.py`
- **.record_failure()** (1 connections) — `agent-verse-backend/app/reliability/redis_circuit_breaker.py`
- **.record_success()** (1 connections) — `agent-verse-backend/app/reliability/redis_circuit_breaker.py`

## Relationships

- [Community 255](Community_255.md) (4 shared connections)
- [MCP A2A Protocol](MCP_A2A_Protocol.md) (3 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (3 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (2 shared connections)
- [Step Execution & Semantic Cache](Step_Execution_&_Semantic_Cache.md) (1 shared connections)
- [Community 271](Community_271.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/reliability/circuit_breaker.py`
- `agent-verse-backend/app/reliability/redis_circuit_breaker.py`

## Audit Trail

- EXTRACTED: 39 (93%)
- INFERRED: 3 (7%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*