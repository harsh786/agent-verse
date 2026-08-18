"""Voice module — Speech-to-Text / Text-to-Speech for mission creation.

Endpoints:
  POST /v1/voice/transcribe  - convert audio blob -> text transcript
  POST /v1/voice/goal        - refine a raw transcript into a mission goal

The module is intentionally thin: it delegates to the LLM provider for
goal refinement and to an STT backend (Whisper-compatible) for transcription.
When no STT key is configured it returns a no-op stub so the UI degrades
gracefully (the browser's Web Speech API handles transcription client-side).
"""

from .router import router

__all__ = ["router"]
