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
import base64
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.providers.base import LLMProvider


@dataclass
class VisionParseResult:
    source_name: str
    description: str = ""
    error: str | None = None
    model_used: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_chunks(self) -> list[dict[str, Any]]:
        content = self.description or f"[Image: {self.source_name}]"
        return [{
            "content": content,
            "chunk_index": 0,
            "source_name": self.source_name,
            "content_type": "image",
            "model_used": self.model_used,
        }]


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
        if self._provider is not None:
            try:
                description = await self._describe_with_provider(
                    self._provider, b64_image, mime_type, prompt
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
        providers = (
            ["openai", "anthropic"]
            if self._prefer == "openai"
            else ["anthropic", "openai"]
        )
        last_error = ""
        for provider_name in providers:
            describe_fn = self._sdk_dispatch.get(provider_name)
            if describe_fn is None:
                continue
            try:
                description = await describe_fn(b64_image, mime_type, prompt)
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

        request = CompletionRequest(
            messages=[
                Message(
                    role="user",
                    content=prompt,
                    image_data=b64_image,
                )
            ],
            model="",  # provider picks the vision-capable model
            system="You are an expert image analyst. Describe the image accurately.",
            max_tokens=500,
        )
        response = await provider.complete(request)
        return response.content or ""

    async def _describe_with_openai(self, b64_image: str, mime_type: str, prompt: str) -> str:
        import openai  # type: ignore[import]
        client = openai.AsyncOpenAI()
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:{mime_type};base64,{b64_image}", "detail": "auto",
                }},
            ]}],
            max_tokens=500,
        )
        return response.choices[0].message.content or ""

    async def _describe_with_anthropic(self, b64_image: str, mime_type: str, prompt: str) -> str:
        import anthropic  # type: ignore[import]
        client = anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=500,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": mime_type, "data": b64_image,
                }},
                {"type": "text", "text": prompt},
            ]}],
        )
        return response.content[0].text if response.content else ""
