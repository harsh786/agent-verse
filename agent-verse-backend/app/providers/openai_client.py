"""Shared AsyncOpenAI client factory that honours the configured endpoint.

Any component that builds a raw ``openai.AsyncOpenAI()`` with no arguments talks
to the *official* OpenAI API — which, on a self-hosted deployment configured with
``OPENAI_BASE_URL`` (vLLM/Qwen, etc.), means a bogus request to api.openai.com
with the local ``sk-noauth`` key and a 401. Route every such client through this
factory so it defaults to the configured base_url + api_key. Explicit overrides
(e.g. a per-call api_key) still win.
"""

from __future__ import annotations

import os
from typing import Any


def async_openai_client(**overrides: Any) -> Any:
    """Return an ``openai.AsyncOpenAI`` defaulting to the configured endpoint.

    Reads ``OPENAI_BASE_URL`` and ``OPENAI_API_KEY`` from the environment so no
    component silently falls back to the official OpenAI API. Any keyword passed
    in ``overrides`` takes precedence over the env defaults.
    """
    import openai

    kwargs: dict[str, Any] = {}
    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        kwargs["api_key"] = api_key
    kwargs.update(overrides)
    return openai.AsyncOpenAI(**kwargs)
