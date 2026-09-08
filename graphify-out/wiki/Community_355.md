# Community 355

> 18 nodes · cohesion 0.18

## Key Concepts

- **export_training_data()** (9 connections) — `agent-verse-backend/app/api/training_export.py`
- **training_export.py** (8 connections) — `agent-verse-backend/app/api/training_export.py`
- **preview_training_data()** (7 connections) — `agent-verse-backend/app/api/training_export.py`
- **_collect_training_examples_db()** (5 connections) — `agent-verse-backend/app/api/training_export.py`
- **_collect_training_examples_memory()** (5 connections) — `agent-verse-backend/app/api/training_export.py`
- **Any** (5 connections)
- **_to_anthropic_format()** (4 connections) — `agent-verse-backend/app/api/training_export.py`
- **_to_openai_format()** (4 connections) — `agent-verse-backend/app/api/training_export.py`
- **Request** (2 connections)
- **get** (1 connections)
- **StreamingResponse** (1 connections)
- **Fine-tuning data export endpoint. Exports high-scoring goal executions as JSONL…** (1 connections) — `agent-verse-backend/app/api/training_export.py`
- **Query completed, high-scoring goals from PostgreSQL via the evaluations table.** (1 connections) — `agent-verse-backend/app/api/training_export.py`
- **Fallback: extract high-scoring goal executions from the GoalService in-memory…** (1 connections) — `agent-verse-backend/app/api/training_export.py`
- **Convert a goal execution to OpenAI fine-tuning JSONL format.** (1 connections) — `agent-verse-backend/app/api/training_export.py`
- **Convert a goal execution to Anthropic fine-tuning JSONL format.** (1 connections) — `agent-verse-backend/app/api/training_export.py`
- **Preview training data stats without triggering a download. Returns count, score…** (1 connections) — `agent-verse-backend/app/api/training_export.py`
- **Export successful goal executions as JSONL for LLM fine-tuning. Query params:…** (1 connections) — `agent-verse-backend/app/api/training_export.py`

## Relationships

- [Community 320](Community_320.md) (1 shared connections)
- [Community 82](Community_82.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/api/training_export.py`

## Audit Trail

- EXTRACTED: 30 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*