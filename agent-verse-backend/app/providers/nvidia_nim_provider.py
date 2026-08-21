"""NVIDIA NIM provider.

NVIDIA NIM exposes an OpenAI-compatible API either via NVIDIA's hosted cloud
(integrate.api.nvidia.com) or a self-hosted NIM container.

Environment variables:
    NGC_API_KEY          — API key from build.nvidia.com / NGC catalog
    NVIDIA_NIM_BASE_URL  — Override for self-hosted NIM (default: NVIDIA cloud)
"""

from __future__ import annotations

import os

from app.providers.openai_compatible import OpenAICompatibleProvider


class NvidiaNIMProvider(OpenAICompatibleProvider):
    """NVIDIA NIM provider.

    Supports NVIDIA's hosted models as well as self-hosted NIM containers.
    The API is fully OpenAI-compatible; no method overrides are required.
    """

    provider_name = "nvidia_nim"

    CLOUD_MODELS: list[str] = [
        "nvidia/llama-3.1-nemotron-70b-instruct",
        "nvidia/mistral-nemo-minitron-8b-8k-instruct",
        "meta/llama-3.1-405b-instruct",
        "mistralai/mixtral-8x22b-instruct-v0.1",
        "google/gemma-2-27b-it",
    ]

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str = "nvidia/llama-3.1-nemotron-70b-instruct",
    ) -> None:
        super().__init__(
            api_key=api_key or os.getenv("NGC_API_KEY", ""),
            base_url=(
                base_url or os.getenv("NVIDIA_NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
            ),
            default_model=default_model,
            supports_vision_flag=False,
        )
