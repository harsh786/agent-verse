# Community 567

> 9 nodes · cohesion 0.25

## Key Concepts

- **PromptVariantSelector** (5 connections) — `agent-verse-backend/app/context/prompt_variant_selector.py`
- **intelligence/prompt_optimizer.py** (5 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **prompt_variant_selector.py** (3 connections) — `agent-verse-backend/app/context/prompt_variant_selector.py`
- **.get_active_variant()** (3 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **PromptVariant** (2 connections) — `agent-verse-backend/app/context/prompt_variant_selector.py`
- **.select()** (2 connections) — `agent-verse-backend/app/context/prompt_variant_selector.py`
- **PromptVariantSelector — deterministic A/B variant selection per goal_id.** (1 connections) — `agent-verse-backend/app/context/prompt_variant_selector.py`
- **PromptOptimizer — A/B tests prompt variants and auto-promotes the winner.…** (1 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`
- **Get the currently active prompt variant ID for A/B testing via…** (1 connections) — `agent-verse-backend/app/intelligence/prompt_optimizer.py`

## Relationships

- [Community 158](Community_158.md) (4 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/context/prompt_variant_selector.py`
- `agent-verse-backend/app/intelligence/prompt_optimizer.py`

## Audit Trail

- EXTRACTED: 13 (93%)
- INFERRED: 1 (7%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*