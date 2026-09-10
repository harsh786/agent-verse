"""VisionParser — describes images using GPT-4o or Claude Vision.

Supports two modes:
1. Protocol mode: inject an ``LLMProvider`` on construction; uses
   ``provider.complete()`` with image_data — works with any provider that
   implements ``supports_vision()``.
2. Legacy SDK mode (default): falls back to direct OpenAI / Anthropic SDK
   calls when no provider is injected.  The ``prefer_provider`` string
   controls the attempt order.
"""

from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.providers.base import LLMProvider

# A dedicated OCR/vision model (esp. a large reasoning VLM like the NVIDIA omni
# model) can be slow on its first, cold request — long enough that a one-shot
# attempt times out and the parser falls back to a placeholder. Give the vision
# call a generous timeout and a bounded retry so the cold attempt warms the model
# and the retry succeeds. Both are env-tunable.
_VISION_TIMEOUT_S = float(os.getenv("VISION_CALL_TIMEOUT_SECONDS", "") or 180.0)
_VISION_MAX_ATTEMPTS = max(1, int(os.getenv("VISION_MAX_ATTEMPTS", "") or 3))
_VISION_RETRY_DELAY_S = float(os.getenv("VISION_RETRY_DELAY_SECONDS", "") or 2.0)

# Process-level guard so the proactive warm-up ping runs at most once.
_warmed_up = False


async def _retry_async(factory: Callable[[], Awaitable[str]]) -> str:
    """Run an awaitable factory with bounded retries (cold-start resilience).

    Retries on any exception up to ``_VISION_MAX_ATTEMPTS``; the first (cold)
    attempt typically loads the model so a later attempt returns text instead of
    the parser falling back. Re-raises the last error only if every attempt fails.
    """
    last_exc: Exception | None = None
    for attempt in range(_VISION_MAX_ATTEMPTS):
        try:
            return await factory()
        except Exception as exc:
            last_exc = exc
            if attempt < _VISION_MAX_ATTEMPTS - 1:
                await asyncio.sleep(_VISION_RETRY_DELAY_S * (attempt + 1))
    raise last_exc if last_exc else RuntimeError("vision call failed")


async def warm_up_vision_model() -> bool:
    """Proactively spin up the configured OCR/vision model (once per process).

    Sends a tiny text request so the model is loaded before the first real image
    arrives, so a cold start never turns into a fallback. Best-effort and safe to
    call from app startup; a failure here is swallowed (the retry path still
    covers cold starts at request time).
    """
    global _warmed_up
    if _warmed_up:
        return True
    _warmed_up = True  # set first: never warm more than once, even on failure
    try:
        from app.providers.model_defaults import configured_vision_model
        from app.providers.openai_client import async_openai_client

        model = configured_vision_model("")
        if not model:
            return False
        client = async_openai_client()
        await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ok"}],
            max_tokens=1,
            timeout=_VISION_TIMEOUT_S,
        )
        return True
    except Exception:
        return False


@dataclass
class VisionParseResult:
    source_name: str
    description: str = ""
    error: str | None = None
    model_used: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_chunks(self) -> list[dict[str, Any]]:
        content = self.description or f"[Image: {self.source_name}]"
        return [
            {
                "content": content,
                "chunk_index": 0,
                "source_name": self.source_name,
                "content_type": "image",
                "model_used": self.model_used,
            }
        ]


def _detect_image_mime(image_bytes: bytes) -> str:
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_bytes[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


class VisionParser:
    def __init__(
        self,
        prefer_provider: str = "openai",
        provider: LLMProvider | None = None,
    ) -> None:
        self._prefer = prefer_provider
        self._provider = provider  # injected LLMProvider Protocol (optional)

        # Dispatch table eliminates if/elif chains over provider names.
        # Keys match the strings in the preference list; values are bound methods.
        self._sdk_dispatch: dict[
            str,
            Callable[[str, str, str], Coroutine[Any, Any, str]],
        ] = {
            "openai": self._describe_with_openai,
            "anthropic": self._describe_with_anthropic,
        }

    async def parse_image_bytes(
        self,
        image_bytes: bytes,
        source_name: str,
        prompt: str = "Describe this image in detail.",
    ) -> VisionParseResult:
        if not image_bytes:
            return VisionParseResult(source_name=source_name, error="empty image")
        mime_type = _detect_image_mime(image_bytes)
        b64_image = base64.standard_b64encode(image_bytes).decode()

        # ── Protocol path: use the injected LLMProvider if available ──────────
        provider = self._provider
        if provider is not None:
            try:
                description = await _retry_async(
                    lambda: self._describe_with_provider(
                        provider, b64_image, mime_type, prompt
                    )
                )
                return VisionParseResult(
                    source_name=source_name,
                    description=description,
                    model_used="llm_provider",
                )
            except Exception as exc:
                return VisionParseResult(
                    source_name=source_name,
                    description=f"[Image: {source_name}]",
                    error=str(exc),
                    model_used="fallback",
                )

        # ── Legacy SDK path: try providers in preference order via dispatch ───
        providers = ["openai", "anthropic"] if self._prefer == "openai" else ["anthropic", "openai"]
        last_error = ""
        for provider_name in providers:
            describe_fn = self._sdk_dispatch.get(provider_name)
            if describe_fn is None:
                continue
            try:
                description = await _retry_async(
                    lambda fn=describe_fn: fn(b64_image, mime_type, prompt)
                )
                return VisionParseResult(
                    source_name=source_name,
                    description=description,
                    model_used=provider_name,
                )
            except Exception as exc:
                last_error = str(exc)
        return VisionParseResult(
            source_name=source_name,
            description=f"[Image: {source_name}]",
            error=last_error,
            model_used="fallback",
        )

    async def _describe_with_provider(
        self,
        provider: LLMProvider,
        b64_image: str,
        mime_type: str,
        prompt: str,
    ) -> str:
        """Use the injected LLMProvider Protocol to describe the image.

        Builds a CompletionRequest with the base64-encoded image in the
        message content, delegating all provider-specific details to the
        provider implementation.
        """
        from app.providers.base import CompletionRequest, Message
        from app.providers.model_defaults import configured_vision_model

        request = CompletionRequest(
            messages=[
                Message(
                    role="user",
                    content=prompt,
                    image_data=b64_image,
                )
            ],
            # Dedicated vision/OCR model; empty defers to the provider default.
            model=configured_vision_model(""),
            system="You are an expert image analyst. Describe the image accurately.",
            max_tokens=500,
        )
        response = await provider.complete(request)
        return response.content or ""

    async def _describe_with_openai(self, b64_image: str, mime_type: str, prompt: str) -> str:
        from app.providers.openai_client import async_openai_client

        client = async_openai_client()
        # Model comes from config, not a hardcoded slug: the dedicated vision/OCR
        # model (VISION_MODEL/OCR_MODEL), else the reasoning model, resolved on the
        # OpenAI-compatible endpoint configured via OPENAI_BASE_URL.
        from app.providers.model_defaults import configured_vision_model

        ocr_model = configured_vision_model("gpt-4o")
        response = await client.chat.completions.create(
            model=ocr_model,
            timeout=_VISION_TIMEOUT_S,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{b64_image}",
                                "detail": "auto",
                            },
                        },
                    ],
                }
            ],
            max_tokens=500,
        )
        return response.choices[0].message.content or ""

    async def _describe_with_anthropic(self, b64_image: str, mime_type: str, prompt: str) -> str:
        import anthropic  # type: ignore[import]

        client = anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model=os.getenv("ANTHROPIC_VISION_MODEL") or "claude-3-5-sonnet-20241022",
            max_tokens=500,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": b64_image,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        return response.content[0].text if response.content else ""
