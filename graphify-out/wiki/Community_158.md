# Community 158

> 34 nodes · cohesion 0.09

## Key Concepts

- **PromptOptimizer** (19 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **PromptVariant** (11 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **Any** (7 connections)
- **.persist_variant()** (6 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **.add_variant()** (5 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **.load_from_db()** (5 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **.register_variant()** (5 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **.get_report()** (4 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **.maybe_promote()** (4 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **._is_significant()** (3 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **.persist_outcome()** (3 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **.select_variant()** (3 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **.set_redis()** (3 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **PromptOptimizer (intelligence)** (3 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **.invalidate_cache()** (2 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **._percentile()** (2 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **.record_result()** (2 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **SelfOptimizer (v1, deprecated)** (2 connections) — `agent-verse-backend/app/intelligence/self_optimization.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **.list_all_keys()** (1 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **Register a new prompt variant for A/B testing. If *db* is provided (an async…** (1 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **Set Redis client for cache invalidation between replicas.** (1 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **Publish cache invalidation to other replicas.** (1 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **Persist a variant to the prompt_variants table.** (1 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **Update win/loss counts in DB after A/B test result.** (1 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- *... and 9 more nodes in this community*

## Relationships

- [Community 567](Community_567.md) (4 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Community 97](Community_97.md) (1 shared connections)
- [Community 185](Community_185.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- `agent-verse-backend/app/intelligence/self_optimization.py`
- `agent-verse-backend/app/optimization/prompt_optimizer.py`

## Audit Trail

- EXTRACTED: 54 (95%)
- INFERRED: 2 (4%)
- AMBIGUOUS: 1 (2%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*