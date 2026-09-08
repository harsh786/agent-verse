# Community 311

> 21 nodes · cohesion 0.14

## Key Concepts

- **IntentRouter** (16 connections) — `agent-verse-backend/app/chat/intent.py`
- **.classify()** (9 connections) — `agent-verse-backend/app/chat/intent.py`
- **intent.py** (8 connections) — `agent-verse-backend/app/chat/intent.py`
- **Intent** (4 connections) — `agent-verse-backend/app/chat/intent.py`
- **.generate_clarifying_question()** (3 connections) — `agent-verse-backend/app/chat/intent.py`
- **.generate_schedule_confirmation()** (3 connections) — `agent-verse-backend/app/chat/intent.py`
- **._has_goal_verb()** (3 connections) — `agent-verse-backend/app/chat/intent.py`
- **._previous_was_goal()** (3 connections) — `agent-verse-backend/app/chat/intent.py`
- **ClarifyRequest** (2 connections) — `agent-verse-backend/app/chat/intent.py`
- **.available_models()** (2 connections) — `agent-verse-backend/app/chat/intent.py`
- **._has_file_context()** (2 connections) — `agent-verse-backend/app/chat/intent.py`
- **._is_qa()** (2 connections) — `agent-verse-backend/app/chat/intent.py`
- **._is_schedule()** (2 connections) — `agent-verse-backend/app/chat/intent.py`
- **._is_underspecified()** (2 connections) — `agent-verse-backend/app/chat/intent.py`
- **ScheduleConfirmation** (2 connections) — `agent-verse-backend/app/chat/intent.py`
- **Intent router — classifies chat messages into QA / GOAL / CLARIFY / SCHEDULE.…** (1 connections) — `agent-verse-backend/app/chat/intent.py`
- **Classify a chat message into Intent.QA / GOAL / CLARIFY / SCHEDULE. Rules (in…** (1 connections) — `agent-verse-backend/app/chat/intent.py`
- **Return the intent for *message*.** (1 connections) — `agent-verse-backend/app/chat/intent.py`
- **Return a clarifying question based on what's missing in *message*.** (1 connections) — `agent-verse-backend/app/chat/intent.py`
- **Parse a natural-language schedule expression and return a confirmation.** (1 connections) — `agent-verse-backend/app/chat/intent.py`
- **Return list of model IDs available for selection.** (1 connections) — `agent-verse-backend/app/chat/intent.py`

## Relationships

- [Community 125](Community_125.md) (5 shared connections)
- [Community 48](Community_48.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Community 310](Community_310.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/chat/intent.py`

## Audit Trail

- EXTRACTED: 37 (95%)
- INFERRED: 2 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*