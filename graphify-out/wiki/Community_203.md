# Community 203

> 29 nodes · cohesion 0.10

## Key Concepts

- **GoalPersistenceEngine (retry/escalate)** (20 connections) — `agent-verse-backend/app/agent/persistence.py`
- **.run()** (11 connections) — `agent-verse-backend/app/agent/persistence.py`
- **agent/persistence.py** (8 connections) — `agent-verse-backend/app/agent/persistence.py`
- **RetryStrategy** (6 connections) — `agent-verse-backend/app/agent/persistence.py`
- **.score_cost()** (5 connections) — `agent-verse-backend/app/evals/model_score.py`
- **AttemptRecord** (4 connections) — `agent-verse-backend/app/agent/persistence.py`
- **._build_enriched_goal()** (4 connections) — `agent-verse-backend/app/agent/persistence.py`
- **._pick_strategy()** (4 connections) — `agent-verse-backend/app/agent/persistence.py`
- **._write_attempt_end()** (4 connections) — `agent-verse-backend/app/agent/persistence.py`
- **Any** (4 connections)
- **PLANNER_SYSTEM prompt** (4 connections) — `agent-verse-backend/app/agent/prompts.py`
- **._backoff_seconds()** (3 connections) — `agent-verse-backend/app/agent/persistence.py`
- **.__init__()** (3 connections) — `agent-verse-backend/app/agent/persistence.py`
- **._write_attempt_start()** (3 connections) — `agent-verse-backend/app/agent/persistence.py`
- **.attempts()** (2 connections) — `agent-verse-backend/app/agent/persistence.py`
- **.consecutive_failures()** (2 connections) — `agent-verse-backend/app/agent/persistence.py`
- **.total_cost_usd()** (2 connections) — `agent-verse-backend/app/agent/persistence.py`
- **StrEnum** (1 connections)
- **Agent goal persistence — keeps trying until goal is achieved or explicitly…** (1 connections) — `agent-verse-backend/app/agent/persistence.py`
- **Manages persistent goal execution with intelligent retry strategies. Wraps an…** (1 connections) — `agent-verse-backend/app/agent/persistence.py`
- **Count trailing consecutive failures.** (1 connections) — `agent-verse-backend/app/agent/persistence.py`
- **Choose the next retry strategy based on failure history.** (1 connections) — `agent-verse-backend/app/agent/persistence.py`
- **Exponential backoff with jitter: base * 2^attempt ± 20% jitter.** (1 connections) — `agent-verse-backend/app/agent/persistence.py`
- **Enrich the goal prompt with strategy hints for the planner.** (1 connections) — `agent-verse-backend/app/agent/persistence.py`
- **Write attempt start record to DB. Returns attempt record ID.** (1 connections) — `agent-verse-backend/app/agent/persistence.py`
- *... and 4 more nodes in this community*

## Relationships

- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (7 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (5 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Community 215](Community_215.md) (1 shared connections)
- [Org Department Memory](Org_Department_Memory.md) (1 shared connections)
- [Community 366](Community_366.md) (1 shared connections)
- [Community 164](Community_164.md) (1 shared connections)
- [Eval Scoring](Eval_Scoring.md) (1 shared connections)
- [Runtime Profile & Sandbox](Runtime_Profile_&_Sandbox.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/agent/persistence.py`
- `agent-verse-backend/app/agent/prompts.py`
- `agent-verse-backend/app/evals/model_score.py`

## Audit Trail

- EXTRACTED: 51 (85%)
- INFERRED: 9 (15%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*