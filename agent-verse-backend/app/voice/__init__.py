"""Voice OS module — Native STT/TTS/Streaming for AgentVerse.

Providers:
  STT: faster-whisper (default) | whisper_api | assemblyai
  TTS: kokoro (default) | omnivoice | elevenlabs | openai_tts | azure_tts

Endpoints:
  GET  /v1/voice/status
  POST /v1/voice/transcribe
  POST /v1/voice/speak
  GET  /v1/voice/greeting/{org_id}
  POST /v1/voice/persona/{org_id}
  WS   /v1/voice/stream/{org_id}
"""

from .router import router

__all__ = ["router"]
