# Community 190

> 31 nodes · cohesion 0.08

## Key Concepts

- **TranscriptResult** (10 connections) — `agent-verse-backend/app/voice/providers/base.py`
- **FasterWhisperSTT** (6 connections) — `agent-verse-backend/app/voice/providers/stt/faster_whisper.py`
- **AssemblyAISTT** (5 connections) — `agent-verse-backend/app/voice/providers/stt/assemblyai.py`
- **faster_whisper.py** (5 connections) — `agent-verse-backend/app/voice/providers/stt/faster_whisper.py`
- **WhisperAPISTT** (5 connections) — `agent-verse-backend/app/voice/providers/stt/whisper_api.py`
- **assemblyai.py** (4 connections) — `agent-verse-backend/app/voice/providers/stt/assemblyai.py`
- **_decode_audio()** (4 connections) — `agent-verse-backend/app/voice/providers/stt/faster_whisper.py`
- **._get_model()** (4 connections) — `agent-verse-backend/app/voice/providers/stt/faster_whisper.py`
- **.transcribe()** (4 connections) — `agent-verse-backend/app/voice/providers/stt/faster_whisper.py`
- **whisper_api.py** (4 connections) — `agent-verse-backend/app/voice/providers/stt/whisper_api.py`
- **.transcribe()** (3 connections) — `agent-verse-backend/app/voice/providers/stt/assemblyai.py`
- **.transcribe()** (2 connections) — `agent-verse-backend/app/voice/providers/base.py`
- **.to_dict()** (2 connections) — `agent-verse-backend/app/voice/providers/base.py`
- **.warmup()** (2 connections) — `agent-verse-backend/app/voice/providers/stt/faster_whisper.py`
- **.transcribe()** (2 connections) — `agent-verse-backend/app/voice/providers/stt/whisper_api.py`
- **Any** (1 connections)
- **Normalised STT output — same shape regardless of provider.** (1 connections) — `agent-verse-backend/app/voice/providers/base.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/voice/providers/stt/assemblyai.py`
- **.is_ready()** (1 connections) — `agent-verse-backend/app/voice/providers/stt/assemblyai.py`
- **.warmup()** (1 connections) — `agent-verse-backend/app/voice/providers/stt/assemblyai.py`
- **AssemblyAI STT — requires ASSEMBLY_AI_KEY.** (1 connections) — `agent-verse-backend/app/voice/providers/stt/assemblyai.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/voice/providers/stt/faster_whisper.py`
- **.is_ready()** (1 connections) — `agent-verse-backend/app/voice/providers/stt/faster_whisper.py`
- **Any** (1 connections)
- **ndarray** (1 connections)
- *... and 6 more nodes in this community*

## Relationships

- [Community 128](Community_128.md) (5 shared connections)
- [Community 155](Community_155.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/voice/providers/base.py`
- `agent-verse-backend/app/voice/providers/stt/assemblyai.py`
- `agent-verse-backend/app/voice/providers/stt/faster_whisper.py`
- `agent-verse-backend/app/voice/providers/stt/whisper_api.py`

## Audit Trail

- EXTRACTED: 41 (98%)
- INFERRED: 1 (2%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*