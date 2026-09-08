# Community 428

> 15 nodes · cohesion 0.23

## Key Concepts

- **GuardrailEngine** (14 connections) — `agent-verse-backend/app/intelligence/guardrail_engine.py`
- **GuardrailResult** (8 connections) — `agent-verse-backend/app/intelligence/guardrail_engine.py`
- **Any** (8 connections)
- **._build_result()** (6 connections) — `agent-verse-backend/app/intelligence/guardrail_engine.py`
- **.evaluate_tool_args()** (6 connections) — `agent-verse-backend/app/intelligence/guardrail_engine.py`
- **.evaluate_goal()** (5 connections) — `agent-verse-backend/app/intelligence/guardrail_engine.py`
- **.evaluate_tool_output()** (5 connections) — `agent-verse-backend/app/intelligence/guardrail_engine.py`
- **.evaluate_output()** (4 connections) — `agent-verse-backend/app/intelligence/guardrail_engine.py`
- **.clean()** (1 connections) — `agent-verse-backend/app/intelligence/guardrail_engine.py`
- **Orchestrates all six guardrail layers and returns a single GuardrailResult.…** (1 connections) — `agent-verse-backend/app/intelligence/guardrail_engine.py`
- **Layer 4 — recursively scan tool call arguments before execution.** (1 connections) — `agent-verse-backend/app/intelligence/guardrail_engine.py`
- **Layer 5 — scan tool output for PII/secrets before passing to agent.** (1 connections) — `agent-verse-backend/app/intelligence/guardrail_engine.py`
- **Evaluate free-text goal input before agent processing.** (1 connections) — `agent-verse-backend/app/intelligence/guardrail_engine.py`
- **Layer 6 — scan final agent output before returning to caller.** (1 connections) — `agent-verse-backend/app/intelligence/guardrail_engine.py`
- **scan_output_for_anomalies** (1 connections) — `agent-verse-backend/app/intelligence/output_anomaly.py`

## Relationships

- [Community 237](Community_237.md) (5 shared connections)
- [Community 574](Community_574.md) (5 shared connections)
- [Community 245](Community_245.md) (3 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)

## Source Files

- `agent-verse-backend/app/intelligence/guardrail_engine.py`
- `agent-verse-backend/app/intelligence/output_anomaly.py`

## Audit Trail

- EXTRACTED: 37 (95%)
- INFERRED: 2 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*