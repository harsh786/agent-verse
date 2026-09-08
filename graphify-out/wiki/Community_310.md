# Community 310

> 21 nodes · cohesion 0.10

## Key Concepts

- **ConversationContext** (12 connections) — `agent-verse-backend/app/chat/context.py`
- **chat/context.py** (4 connections) — `agent-verse-backend/app/chat/context.py`
- **.build_for_goal()** (3 connections) — `agent-verse-backend/app/chat/context.py`
- **.build_for_qa()** (3 connections) — `agent-verse-backend/app/chat/context.py`
- **.compress_long_session()** (3 connections) — `agent-verse-backend/app/chat/context.py`
- **Any** (3 connections)
- **.__init__()** (3 connections) — `agent-verse-backend/app/chat/service.py`
- **.inject_file_context()** (2 connections) — `agent-verse-backend/app/chat/context.py`
- **.inject_long_term_memory()** (2 connections) — `agent-verse-backend/app/chat/context.py`
- **.inject_system_prompt()** (2 connections) — `agent-verse-backend/app/chat/context.py`
- **.inject_workspace_rag()** (2 connections) — `agent-verse-backend/app/chat/context.py`
- **ConversationContext — builds LLM context from chat history. Handles: - Last 20…** (1 connections) — `agent-verse-backend/app/chat/context.py`
- **Prepend session system_prompt before all other turns.** (1 connections) — `agent-verse-backend/app/chat/context.py`
- **Prepend uploaded file content as a system turn.** (1 connections) — `agent-verse-backend/app/chat/context.py`
- **Inject top-5 codebase snippets as a system turn.** (1 connections) — `agent-verse-backend/app/chat/context.py`
- **Build LLM message lists from stored chat history.** (1 connections) — `agent-verse-backend/app/chat/context.py`
- **Return last MAX_TURNS messages as OpenAI-style message list.** (1 connections) — `agent-verse-backend/app/chat/context.py`
- **Return compact context string for goal dispatch (max_tokens chars ~= tokens).** (1 connections) — `agent-verse-backend/app/chat/context.py`
- **Summarize oldest messages when session exceeds COMPRESS_THRESHOLD.** (1 connections) — `agent-verse-backend/app/chat/context.py`
- **Prepend top memories as a system turn.** (1 connections) — `agent-verse-backend/app/chat/context.py`
- **Turn** (1 connections) — `agent-verse-backend/app/chat/context.py`

## Relationships

- [Community 125](Community_125.md) (4 shared connections)
- [Community 311](Community_311.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/chat/context.py`
- `agent-verse-backend/app/chat/service.py`

## Audit Trail

- EXTRACTED: 26 (96%)
- INFERRED: 1 (4%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*