# Community 333

> 20 nodes · cohesion 0.18

## Key Concepts

- **intent_router.py** (15 connections) — `agent-verse-backend/app/voice/intent_router.py`
- **route_voice_command** (11 connections) — `agent-verse-backend/app/voice/intent_router.py`
- **voice/streaming.py** (10 connections) — `agent-verse-backend/app/voice/streaming.py`
- **handle_create_mission (voice-to-mission)** (8 connections) — `agent-verse-backend/app/voice/intent_router.py`
- **_get_health()** (6 connections) — `agent-verse-backend/app/voice/intent_router.py`
- **handle_approve (voice-driven approval)** (6 connections) — `agent-verse-backend/app/voice/intent_router.py`
- **Any** (6 connections)
- **classify_intent (rule-based voice intent classifier)** (5 connections) — `agent-verse-backend/app/voice/intent_router.py`
- **_handle_summarize()** (5 connections) — `agent-verse-backend/app/voice/intent_router.py`
- **_handle_status()** (4 connections) — `agent-verse-backend/app/voice/intent_router.py`
- **IntentResult** (2 connections) — `agent-verse-backend/app/voice/intent_router.py`
- **VoiceIntent** (2 connections) — `agent-verse-backend/app/voice/intent_router.py`
- **StrEnum** (1 connections)
- **D-3: Voice Command Intent Router. Classifies a transcript into an intent and…** (1 connections) — `agent-verse-backend/app/voice/intent_router.py`
- **D-1: Voice-to-Mission — the crown jewel. VERIFIED:…** (1 connections) — `agent-verse-backend/app/voice/intent_router.py`
- **D-4: Voice-driven approval — uses real OrgService.record_decision().** (1 connections) — `agent-verse-backend/app/voice/intent_router.py`
- **Main entry: classify intent → dispatch to handler → return TTS text.** (1 connections) — `agent-verse-backend/app/voice/intent_router.py`
- **D-7: Use real OrgService.get_org_health() for summary.** (1 connections) — `agent-verse-backend/app/voice/intent_router.py`
- **Rule-based intent classifier. Fast, deterministic, no LLM needed.** (1 connections) — `agent-verse-backend/app/voice/intent_router.py`
- **Real-time bidirectional voice session over WebSocket. STT → Intent Router → TTS…** (1 connections) — `agent-verse-backend/app/voice/streaming.py`

## Relationships

- [Community 127](Community_127.md) (5 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (4 shared connections)
- [Org Department Memory](Org_Department_Memory.md) (4 shared connections)
- [Community 267](Community_267.md) (2 shared connections)
- [Community 128](Community_128.md) (2 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)
- [Community 122](Community_122.md) (1 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/voice/intent_router.py`
- `agent-verse-backend/app/voice/streaming.py`

## Audit Trail

- EXTRACTED: 53 (98%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 1 (2%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*