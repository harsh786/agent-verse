# Community 347

> 19 nodes · cohesion 0.13

## Key Concepts

- **voice_stream()** (9 connections) — `agent-verse-backend/app/voice/router.py`
- **synthesize (TTS shim)** (9 connections) — `agent-verse-backend/app/voice/tts_engine.py`
- **synthesize_greeting** (8 connections) — `agent-verse-backend/app/voice/greeting.py`
- **greeting.py** (6 connections) — `agent-verse-backend/app/voice/greeting.py`
- **jurisdiction_to_language (D-5 multi-language)** (6 connections) — `agent-verse-backend/app/voice/greeting.py`
- **_ws_auth()** (5 connections) — `agent-verse-backend/app/voice/router.py`
- **build_greeting_script** (4 connections) — `agent-verse-backend/app/voice/greeting.py`
- **get_tts (provider singleton, fallback chain)** (3 connections) — `agent-verse-backend/app/voice/providers/__init__.py`
- **Any** (2 connections)
- **websocket** (2 connections)
- **Voice OS FastAPI router** (2 connections) — `agent-verse-backend/app/voice/router.py`
- **Voice greeting builder — synthesised on every org page load. Data sources…** (1 connections) — `agent-verse-backend/app/voice/greeting.py`
- **D-5: Auto-detect TTS language from org jurisdiction field.** (1 connections) — `agent-verse-backend/app/voice/greeting.py`
- **Return WAV bytes for the login greeting using real org health data.** (1 connections) — `agent-verse-backend/app/voice/greeting.py`
- **Render a natural-language greeting from OrgService.get_org_health() data. Args:…** (1 connections) — `agent-verse-backend/app/voice/greeting.py`
- **D-1/D-3/D-4: Real-time voice session — speak goals, approve missions.** (1 connections) — `agent-verse-backend/app/voice/router.py`
- **Authenticate WebSocket using the same key resolver as TenantMiddleware.** (1 connections) — `agent-verse-backend/app/voice/router.py`
- **Synthesise text to WAV bytes.** (1 connections) — `agent-verse-backend/app/voice/tts_engine.py`
- **TTS_REGISTRY (kokoro/omnivoice/elevenlabs/openai_tts/azure_tts/macos_say/browser)** (1 connections) — `agent-verse-backend/app/voice/providers/__init__.py`

## Relationships

- [Community 122](Community_122.md) (10 shared connections)
- [Community 128](Community_128.md) (3 shared connections)
- [Org Department Memory](Org_Department_Memory.md) (2 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (1 shared connections)
- [Community 127](Community_127.md) (1 shared connections)
- [Community 133](Community_133.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/voice/greeting.py`
- `agent-verse-backend/app/voice/providers/__init__.py`
- `agent-verse-backend/app/voice/router.py`
- `agent-verse-backend/app/voice/tts_engine.py`

## Audit Trail

- EXTRACTED: 38 (93%)
- INFERRED: 3 (7%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*