# Community 263

> 24 nodes · cohesion 0.10

## Key Concepts

- **CommandScheduler** (8 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **dedup_scheduler.py** (6 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **CommandDeduplicator** (6 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **ScheduledCommand** (6 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **.check_and_reserve()** (4 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **.get_due()** (3 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **.schedule()** (3 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **._expire_key()** (2 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **._make_key()** (2 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **.cancel()** (2 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **.list_pending()** (2 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **.mark_executed()** (1 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **Any** (1 connections)
- **Command deduplication + scheduling — QA8 + QA9 of spec. CommandDeduplicator…** (1 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **A command scheduled for future execution. Created by users via any channel with…** (1 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **Manages scheduled commands (QA9). In production: backed by DB + Celery Beat.…** (1 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **Store a scheduled command for future execution.** (1 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **Cancel a scheduled command.** (1 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **Return commands that are due for execution.** (1 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **Prevents duplicate command execution. Critical for: button double-taps, network…** (1 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **Returns True if command is new (safe to process). Returns False if command was…** (1 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`
- **.__post_init__()** (1 connections) — `agent-verse-backend/app/gateway/dedup_scheduler.py`

## Relationships

- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/gateway/dedup_scheduler.py`

## Audit Trail

- EXTRACTED: 30 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*