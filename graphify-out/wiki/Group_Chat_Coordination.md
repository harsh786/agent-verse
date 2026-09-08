# Group Chat Coordination

> 81 nodes · cohesion 0.05

## Key Concepts

- **Classification** (30 connections) — `agent-verse-backend/app/coordination/contracts.py`
- **TranscriptMessage** (21 connections) — `agent-verse-backend/app/coordination/transcript/models.py`
- **group_chat/adapter.py** (16 connections) — `agent-verse-backend/app/coordination/group_chat/adapter.py`
- **transcript/repository.py** (14 connections) — `agent-verse-backend/app/coordination/transcript/repository.py`
- **transcript/service.py** (13 connections) — `agent-verse-backend/app/coordination/transcript/service.py`
- **TranscriptService** (12 connections) — `agent-verse-backend/app/coordination/transcript/service.py`
- **transcript/models.py** (10 connections) — `agent-verse-backend/app/coordination/transcript/models.py`
- **GroupChatRuntime** (9 connections) — `agent-verse-backend/app/coordination/group_chat/adapter.py`
- **.execute()** (9 connections) — `agent-verse-backend/app/coordination/group_chat/adapter.py`
- **compaction.py** (9 connections) — `agent-verse-backend/app/coordination/group_chat/compaction.py`
- **group_chat/state_machine.py** (8 connections) — `agent-verse-backend/app/coordination/group_chat/state_machine.py`
- **group_chat/__init__.py** (7 connections) — `agent-verse-backend/app/coordination/group_chat/__init__.py`
- **GroupChatExecutionState** (7 connections) — `agent-verse-backend/app/coordination/group_chat/models.py`
- **transcript/__init__.py** (7 connections) — `agent-verse-backend/app/coordination/transcript/__init__.py`
- **InMemoryTranscriptRepository** (7 connections) — `agent-verse-backend/app/coordination/transcript/repository.py`
- **PostgresTranscriptRepository** (7 connections) — `agent-verse-backend/app/coordination/transcript/repository.py`
- **.resume_human_turn()** (6 connections) — `agent-verse-backend/app/coordination/group_chat/adapter.py`
- **group_chat/models.py** (6 connections) — `agent-verse-backend/app/coordination/group_chat/models.py`
- **speaker_policy.py** (6 connections) — `agent-verse-backend/app/coordination/group_chat/speaker_policy.py`
- **GroupChatState** (6 connections) — `agent-verse-backend/app/coordination/group_chat/state_machine.py`
- **transition_group_chat()** (6 connections) — `agent-verse-backend/app/coordination/group_chat/state_machine.py`
- **_message_from_row()** (6 connections) — `agent-verse-backend/app/coordination/transcript/repository.py`
- **TranscriptRepository** (6 connections) — `agent-verse-backend/app/coordination/transcript/repository.py`
- **._emit()** (5 connections) — `agent-verse-backend/app/coordination/group_chat/adapter.py`
- **._invoke()** (5 connections) — `agent-verse-backend/app/coordination/group_chat/adapter.py`
- *... and 56 more nodes in this community*

## Relationships

- [Coordination Contracts](Coordination_Contracts.md) (9 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (6 shared connections)
- [Community 68](Community_68.md) (5 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (5 shared connections)
- [Memory-driven Improvement](Memory-driven_Improvement.md) (4 shared connections)
- [Agent Pattern Adapters (AutoGPT/BabyAGI/CodeAct)](Agent_Pattern_Adapters_AutoGPT-BabyAGI-CodeAct.md) (4 shared connections)
- [Reliability & Audit](Reliability_&_Audit.md) (3 shared connections)
- [Community 477](Community_477.md) (2 shared connections)
- [Community 370](Community_370.md) (2 shared connections)
- [Community 249](Community_249.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/coordination/contracts.py`
- `agent-verse-backend/app/coordination/group_chat/__init__.py`
- `agent-verse-backend/app/coordination/group_chat/adapter.py`
- `agent-verse-backend/app/coordination/group_chat/compaction.py`
- `agent-verse-backend/app/coordination/group_chat/models.py`
- `agent-verse-backend/app/coordination/group_chat/speaker_policy.py`
- `agent-verse-backend/app/coordination/group_chat/state_machine.py`
- `agent-verse-backend/app/coordination/transcript/__init__.py`
- `agent-verse-backend/app/coordination/transcript/models.py`
- `agent-verse-backend/app/coordination/transcript/projections.py`
- `agent-verse-backend/app/coordination/transcript/repository.py`
- `agent-verse-backend/app/coordination/transcript/service.py`

## Audit Trail

- EXTRACTED: 191 (95%)
- INFERRED: 11 (5%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*