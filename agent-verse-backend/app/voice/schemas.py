"""Pydantic schemas for the Voice OS API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class VoiceStatusResponse(BaseModel):
    stt_status: str  # "ready" | "idle" | "error"
    tts_status: str  # "ready" | "idle" | "error"
    # The models that actually run (resolved from the Model Registry → env pins →
    # local engines) and where each choice came from: registry_preference |
    # registry_cheapest | env_pin | local_default | degraded | not_configured.
    stt_model: str = ""
    tts_model: str = ""
    stt_source: str = ""
    tts_source: str = ""
    device: str = "cpu"
    stt_provider: str = ""
    tts_provider: str = ""
    # Why a capability has no model (the resolver's configuration hint).
    stt_error: str | None = None
    tts_error: str | None = None


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
    # s3:// URL of the archived reference audio; None when it was not archived
    # (the persona itself is stored in Redis either way).
    ref_audio_url: str | None = None
    ref_text: str
    language: str = "en"
    created_at: str
