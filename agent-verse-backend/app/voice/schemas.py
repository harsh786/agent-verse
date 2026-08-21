"""Pydantic schemas for the Voice OS API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class VoiceStatusResponse(BaseModel):
    stt_status: str  # "ready" | "idle" | "error"
    tts_status: str  # "ready" | "idle" | "error"
    stt_model: str = "large-v3-turbo"
    tts_model: str = "k2-fsa/OmniVoice"
    device: str = "cpu"
    stt_provider: str = "faster_whisper"
    tts_provider: str = "kokoro"


class TranscribeResponse(BaseModel):
    transcript: str
    language: str = "en"
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    segments: list[dict] = Field(default_factory=list)
    duration_s: float = 0.0


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4096)
    language: str = "en"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    org_id: str | None = None
    use_org_persona: bool = True


class PersonaResponse(BaseModel):
    org_id: str
    tenant_id: str
    ref_audio_url: str
    ref_text: str
    language: str = "en"
    created_at: str
