"""Single source of truth for "the model the system is configured to use".

Peripheral LLM call sites (guardrail LLM-judge, chat, OCR, ad-hoc embedders)
historically baked in a cloud slug like ``gpt-4o`` / ``gpt-4o-mini`` /
``text-embedding-3-small``. On a system configured for a different provider
(NVIDIA, self-hosted vLLM, …) those slugs are routed to an endpoint that cannot
serve them and 404. Resolve the configured model here instead, so whichever
model is configured in the system takes priority and the literal only survives
as a last-resort fallback.
"""

from __future__ import annotations

import os

# Three independent roles, each with its own model. Reasoning/tooling/chat,
# embedding, and OCR/vision are configured separately so a deployment can run a
# reasoning LLM, a dedicated embedder, and a dedicated vision/OCR model at once.
# Priority order for the chat/reasoning/tooling model. Most specific first.
_LLM_ENV_VARS = ("NVIDIA_MODEL", "DEFAULT_MODEL", "OPENAI_MODEL")
# Priority order for the embedding model.
_EMBED_ENV_VARS = ("NVIDIA_EMBED_MODEL", "EMBEDDING_MODEL")
# Priority order for the image-understanding (vision) model pin.
_VISION_ENV_VARS = ("VISION_MODEL", "NVIDIA_VISION_MODEL")
# The dedicated OCR model pin.
_OCR_ENV_VARS = ("OCR_MODEL",)
# Priority order for the audio transcription (speech-to-text) model.
_AUDIO_ENV_VARS = ("AUDIO_MODEL", "TRANSCRIPTION_MODEL", "NVIDIA_AUDIO_MODEL")


def configured_default_model(fallback: str = "") -> str:
    """Return the system-configured reasoning/tooling LLM model, else ``fallback``.

    Priority: ``NVIDIA_MODEL`` → ``DEFAULT_MODEL`` → ``OPENAI_MODEL`` → fallback.
    """
    for var in _LLM_ENV_VARS:
        value = (os.getenv(var) or "").strip()
        if value:
            return value
    return fallback


def configured_embed_model(fallback: str = "") -> str:
    """Return the system-configured embedding model, else ``fallback``.

    Priority: ``NVIDIA_EMBED_MODEL`` → ``EMBEDDING_MODEL`` → fallback.
    """
    for var in _EMBED_ENV_VARS:
        value = (os.getenv(var) or "").strip()
        if value:
            return value
    return fallback


def configured_vision_model(fallback: str = "") -> str:
    """Return the explicitly pinned image-understanding (vision) model.

    Priority: ``VISION_MODEL`` → ``NVIDIA_VISION_MODEL`` → ``fallback``. It never
    falls back to the reasoning model: a text model handed an image fails (or
    hallucinates). The Model Registry decides first — see
    :func:`app.ai_router.resolve.resolve_vision`; this is only the env pin.
    """
    for var in _VISION_ENV_VARS:
        value = (os.getenv(var) or "").strip()
        if value:
            return value
    return fallback


def configured_ocr_model(fallback: str = "") -> str:
    """Return the explicitly pinned OCR model (``OCR_MODEL``), else ``fallback``.

    The env pin below the Model Registry's OCR / vision order — see
    :func:`app.ai_router.resolve.resolve_ocr`.
    """
    for var in _OCR_ENV_VARS:
        value = (os.getenv(var) or "").strip()
        if value:
            return value
    return fallback


def configured_audio_model(fallback: str = "") -> str:
    """Return the system-configured audio transcription model, else ``fallback``.

    Priority: ``AUDIO_MODEL`` → ``TRANSCRIPTION_MODEL`` → ``NVIDIA_AUDIO_MODEL``.
    This is only the env-pin tier: transcription resolves through the Model
    Registry first (``app.ai_router.resolve.resolve_stt``), never a vendor default.
    """
    for var in _AUDIO_ENV_VARS:
        value = (os.getenv(var) or "").strip()
        if value:
            return value
    return fallback
