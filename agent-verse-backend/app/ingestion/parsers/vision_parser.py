"""VisionParser — describes images with the Model Registry's vision model.

The model comes from :func:`app.ai_router.resolve.resolve_vision` (registry
vision order → ``VISION_MODEL`` / ``NVIDIA_VISION_MODEL`` → an honest
"not configured" error) and the call goes through
:class:`~app.providers.model_dispatch.ModelDispatchProvider`: the injected
provider (a tenant's BYOK one) or else the platform provider, so each model —
the head and every failover model — runs at its own registry endpoint with its
own key. No raw vendor SDK client, no vendor model literal.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from collections.abc import Awaitable, Callable
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

_log = logging.getLogger(__name__)


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
    """Proactively spin up the configured vision model (once per process).

    Sends a tiny text request to the resolved vision model — through the
    dispatch provider, so it reaches the model's own endpoint — so the model is
    loaded before the first real image arrives. Best-effort and safe to call
    from app startup; a failure here is swallowed (the retry path still covers
    cold starts at request time). No-op when no vision model is configured.
    """
    global _warmed_up
    if _warmed_up:
        return True
    _warmed_up = True  # set first: never warm more than once, even on failure
    try:
        from app.ai_router.resolve import (
            ModelNotConfiguredError,
            dispatch_provider,
            resolve_vision,
        )
        from app.providers.base import CompletionRequest, Message
        from app.providers.guarded_completion import complete_decision, uncharged_platform_call

        try:
            res = resolve_vision()
        except ModelNotConfiguredError:
            return False
        # A model probe: an uncharged platform call, never billed to a tenant.
        with uncharged_platform_call("model_probe"):
            await complete_decision(
                dispatch_provider(None),
                CompletionRequest(
                    messages=[Message(role="user", content="ok")],
                    model=res.model,
                    max_tokens=1,
                ),
                role="vision_warmup",
                timeout_seconds=_VISION_TIMEOUT_S,
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
        prefer_provider: str = "",
        provider: LLMProvider | None = None,
    ) -> None:
        # ``prefer_provider`` is accepted for backwards compatibility and
        # ignored: the Model Registry's vision order decides the model.
        self._prefer = prefer_provider
        self._provider = provider  # injected LLMProvider (e.g. a tenant's BYOK one)

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

        from app.ai_router.resolve import ModelNotConfiguredError, dispatch_provider

        try:
            resolution = _resolve_vision()
            provider = dispatch_provider(self._provider)
        except ModelNotConfiguredError as exc:
            return VisionParseResult(
                source_name=source_name,
                description=f"[Image: {source_name}]",
                error=str(exc),
                model_used="fallback",
            )
        except Exception as exc:
            return VisionParseResult(
                source_name=source_name,
                description=f"[Image: {source_name}]",
                error=f"no provider for the vision model: {exc}",
                model_used="fallback",
            )
        answered: list[str] = []
        try:
            description = await _retry_async(
                lambda: self._describe_with_provider(
                    provider, b64_image, mime_type, prompt,
                    model=resolution.model,
                    fallbacks=list(resolution.fallbacks),
                    answered=answered,
                )
            )
        except Exception as exc:
            return VisionParseResult(
                source_name=source_name,
                description=f"[Image: {source_name}]",
                error=str(exc),
                model_used="fallback",
            )
        return VisionParseResult(
            source_name=source_name,
            description=description,
            model_used=answered[-1] if answered else resolution.model,
            metadata={"vision_model_source": resolution.source},
        )

    async def _describe_with_provider(
        self,
        provider: Any,
        b64_image: str,
        mime_type: str,
        prompt: str,
        *,
        model: str,
        fallbacks: list[str],
        answered: list[str] | None = None,
    ) -> str:
        """Describe the image with *model*, failing over across *fallbacks*.

        *provider* is a dispatch provider: every model is sent to its own
        registry endpoint / key (the model's ``base_url``).
        """
        from app.providers.base import CompletionRequest, Message
        from app.providers.guarded_completion import (
            complete_decision,
            vision_timeout_seconds,
        )

        request = CompletionRequest(
            messages=[Message(role="user", content=prompt, image_data=b64_image)],
            model=model,
            system="You are an expert image analyst. Describe the image accurately.",
            max_tokens=500,
        )
        response = await complete_decision(
            provider,
            request,
            role="vision_parse",
            timeout_seconds=vision_timeout_seconds(),
            # A failing / timing-out vision model fails over to the next one in
            # the Model Registry vision order.
            fallback_models=fallbacks,
        )
        if answered is not None:
            answered.append(str(getattr(response, "model", "") or model))
        return response.content or ""


def _resolve_vision() -> Any:
    from app.ai_router.resolve import resolve_vision

    return resolve_vision()
