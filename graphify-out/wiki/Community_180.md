# Community 180

> 32 nodes · cohesion 0.07

## Key Concepts

- **evaluator.py** (7 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **CounterThresholdEvaluator** (7 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **CELEvaluator** (6 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **WindowAggregateEvaluator** (5 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **.evaluate()** (3 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **._to_cel()** (3 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **CompoundTriggerEvaluator** (3 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **.record()** (3 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **TemplateRenderer** (3 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **.evaluate_compound()** (2 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **.check()** (2 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **._count()** (2 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **.render()** (2 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **.check()** (2 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **.record()** (2 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **.reset()** (1 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **CEL condition evaluator and Jinja2 sandboxed template renderer.** (1 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **Sliding-window counter backed by a dict (production: use Redis INCR+EXPIRE).…** (1 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **Record an event occurrence and return the current window count.** (1 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **Evaluate CEL expressions against a payload dict. Falls back to a permissive…** (1 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **Return True if count in window >= threshold.** (1 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **Time-window aggregation evaluator (sum/avg/max/min) against a threshold.…** (1 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **Record a metric sample.** (1 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- **Return True if the aggregate of values in window meets the threshold condition.** (1 connections) — `agent-verse-backend/app/triggers/condition/evaluator.py`
- *... and 7 more nodes in this community*

## Relationships

- [Community 83](Community_83.md) (2 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/triggers/condition/evaluator.py`

## Audit Trail

- EXTRACTED: 35 (97%)
- INFERRED: 1 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*