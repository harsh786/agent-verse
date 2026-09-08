# Community 127

> 40 nodes · cohesion 0.08

## Key Concepts

- **VoiceAlertManager (proactive TTS alerts via Redis pub/sub + SSE)** (15 connections) — `agent-verse-backend/app/voice/alerts.py`
- **VoiceStreamingSession** (13 connections) — `agent-verse-backend/app/voice/streaming.py`
- **transcribe (STT shim)** (10 connections) — `agent-verse-backend/app/voice/stt_engine.py`
- **synthesize_streaming** (9 connections) — `agent-verse-backend/app/voice/tts_engine.py`
- **._run_pipeline()** (8 connections) — `agent-verse-backend/app/voice/streaming.py`
- **._emit_interim()** (6 connections) — `agent-verse-backend/app/voice/streaming.py`
- **alerts.py** (5 connections) — `agent-verse-backend/app/voice/alerts.py`
- **build_alert_text()** (4 connections) — `agent-verse-backend/app/voice/alerts.py`
- **publish_voice_alert** (4 connections) — `agent-verse-backend/app/voice/alerts.py`
- **._handle_alert_message()** (4 connections) — `agent-verse-backend/app/voice/alerts.py`
- **._listen_loop()** (4 connections) — `agent-verse-backend/app/voice/alerts.py`
- **_run_pipeline (STT -> IntentRouter -> TTS)** (4 connections) — `agent-verse-backend/app/voice/streaming.py`
- **._loop()** (4 connections) — `agent-verse-backend/app/voice/streaming.py`
- **._send()** (4 connections) — `agent-verse-backend/app/voice/streaming.py`
- **._to_wav()** (4 connections) — `agent-verse-backend/app/voice/streaming.py`
- **Any** (3 connections)
- **.start()** (3 connections) — `agent-verse-backend/app/voice/alerts.py`
- **.subscribe()** (3 connections) — `agent-verse-backend/app/voice/alerts.py`
- **.__init__()** (3 connections) — `agent-verse-backend/app/voice/streaming.py`
- **.run()** (3 connections) — `agent-verse-backend/app/voice/streaming.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/voice/alerts.py`
- **.unsubscribe()** (2 connections) — `agent-verse-backend/app/voice/alerts.py`
- **Queue** (2 connections)
- **D-6: Proactive Voice Alerts — push TTS audio when important events happen.…** (1 connections) — `agent-verse-backend/app/voice/alerts.py`
- **Publish a voice alert to the tenant's pub/sub channel. Call this from anywhere…** (1 connections) — `agent-verse-backend/app/voice/alerts.py`
- *... and 15 more nodes in this community*

## Relationships

- [Community 122](Community_122.md) (5 shared connections)
- [Community 333](Community_333.md) (5 shared connections)
- [Community 128](Community_128.md) (5 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (2 shared connections)
- [Community 347](Community_347.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/voice/alerts.py`
- `agent-verse-backend/app/voice/router.py`
- `agent-verse-backend/app/voice/streaming.py`
- `agent-verse-backend/app/voice/stt_engine.py`
- `agent-verse-backend/app/voice/tts_engine.py`

## Audit Trail

- EXTRACTED: 76 (99%)
- INFERRED: 1 (1%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*