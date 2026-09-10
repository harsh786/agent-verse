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
# Priority order for the OCR / image-understanding (vision) model.
_VISION_ENV_VARS = ("VISION_MODEL", "OCR_MODEL", "NVIDIA_VISION_MODEL")
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
    """Return the system-configured OCR / image-understanding model.

    A dedicated vision model is independent of the reasoning model. Priority:
    ``VISION_MODEL`` → ``OCR_MODEL`` → ``NVIDIA_VISION_MODEL``; when none is set
    it falls back to the reasoning model (``configured_default_model``) so a
    single multimodal model still serves both, and finally to ``fallback``.
    """
    for var in _VISION_ENV_VARS:
        value = (os.getenv(var) or "").strip()
        if value:
            return value
    return configured_default_model(fallback)


def configured_audio_model(fallback: str = "whisper-1") -> str:
    """Return the system-configured audio transcription model, else ``fallback``.

    Priority: ``AUDIO_MODEL`` → ``TRANSCRIPTION_MODEL`` → ``NVIDIA_AUDIO_MODEL``.
    """
    for var in _AUDIO_ENV_VARS:
        value = (os.getenv(var) or "").strip()
        if value:
            return value
    return fallback
