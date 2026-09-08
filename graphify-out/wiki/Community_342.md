# Community 342

> 19 nodes · cohesion 0.16

## Key Concepts

- **OrgLearningPipeline** (10 connections) — `agent-verse-backend/app/org/org_learning.py`
- **.extract_lessons()** (6 connections) — `agent-verse-backend/app/org/org_learning.py`
- **.process_mission_learnings()** (6 connections) — `agent-verse-backend/app/org/org_learning.py`
- **OrgLesson** (6 connections) — `agent-verse-backend/app/org/org_learning.py`
- **.validate()** (5 connections) — `agent-verse-backend/app/org/org_learning.py`
- **.promote_to_memory()** (4 connections) — `agent-verse-backend/app/org/org_learning.py`
- **.list_quarantine()** (3 connections) — `agent-verse-backend/app/org/org_learning.py`
- **Any** (3 connections)
- **.__init__()** (2 connections) — `agent-verse-backend/app/org/org_learning.py`
- **._lid()** (2 connections) — `agent-verse-backend/app/org/org_learning.py`
- **ValidationResult** (2 connections) — `agent-verse-backend/app/org/org_learning.py`
- **.clear_quarantine()** (1 connections) — `agent-verse-backend/app/org/org_learning.py`
- **Extract candidate lessons from a completed/failed mission.** (1 connections) — `agent-verse-backend/app/org/org_learning.py`
- **Anti-poisoning validation before promotion.** (1 connections) — `agent-verse-backend/app/org/org_learning.py`
- **Promote validated lesson to the appropriate memory tier.** (1 connections) — `agent-verse-backend/app/org/org_learning.py`
- **Full pipeline: extract → validate → promote. Returns count promoted.** (1 connections) — `agent-verse-backend/app/org/org_learning.py`
- **Return lessons in quarantine pending human review.** (1 connections) — `agent-verse-backend/app/org/org_learning.py`
- **A validated lesson ready for promotion to org/dept memory tier.** (1 connections) — `agent-verse-backend/app/org/org_learning.py`
- **PART 26 — Org learning pipeline. Flow: 1. Mission completes →…** (1 connections) — `agent-verse-backend/app/org/org_learning.py`

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (3 shared connections)

## Source Files

- `agent-verse-backend/app/org/org_learning.py`

## Audit Trail

- EXTRACTED: 30 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*