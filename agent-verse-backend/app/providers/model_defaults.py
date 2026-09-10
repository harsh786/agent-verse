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

# Priority order for the chat/reasoning/tooling model. Most specific first.
_LLM_ENV_VARS = ("NVIDIA_MODEL", "DEFAULT_MODEL", "OPENAI_MODEL")
# Priority order for the embedding model.
_EMBED_ENV_VARS = ("NVIDIA_EMBED_MODEL", "EMBEDDING_MODEL")


def configured_default_model(fallback: str = "") -> str:
    """Return the system-configured default LLM model, else ``fallback``.

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
