"""TracedProvider — a transparent LLMProvider wrapper that emits a GenAI span per call.

Wrap any provider once, at the point it is constructed/resolved (create_app /
model router), and every ``complete`` / ``stream_tokens`` call it makes is
recorded as a ``gen_ai.*`` span (see ``app.observability.genai``) — no change to
providers or call sites. The role (planner/executor/verifier) is read from
``request.metadata["agentverse.role"]`` when the caller sets it.

The wrapper is deliberately thin and fail-safe: it never alters the response and
never introduces an error the inner provider didn't raise. Unknown attributes and
capability methods (``supports_vision`` …) delegate straight to the inner object.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.observability.genai import record_generation
from app.providers.base import CompletionRequest, CompletionResponse, EmbedRequest, EmbedResponse


def _role(request: CompletionRequest, default: str | None) -> str | None:
    meta = getattr(request, "metadata", None) or {}
    role = meta.get("agentverse.role") or meta.get("role")
    return str(role) if role else default


def provider_system_of(inner: Any) -> str:
    """Best-effort GenAI ``gen_ai.system`` name for a provider instance.

    OpenAI-compatible covers OpenAI, NVIDIA cloud and on-prem vLLM, so refine by
    base_url when possible. Never raises.
    """
    try:
        cls = type(inner).__name__.lower()
        if "anthropic" in cls:
            return "anthropic"
        if "voyage" in cls:
            return "voyage"
        if "gemini" in cls or "google" in cls:
            return "gemini"
        if "fake" in cls:
            return "fake"
        base = str(
            getattr(inner, "_base_url", "") or getattr(inner, "base_url", "") or ""
        ).lower()
        if "nvidia" in base:
            return "nvidia"
        if "openai.com" in base:
            return "openai"
        if base:
            return "vllm"
        return "openai" if ("openai" in cls or "compatible" in cls) else (cls or "unknown")
    except Exception:
        return "unknown"


class TracedProvider:
    """Transparent tracing wrapper around an ``LLMProvider``."""

    def __init__(
        self, inner: Any, *, provider_system: str, default_role: str | None = None
    ) -> None:
        self._inner = inner
        self._system = provider_system
        self._default_role = default_role

    # -- traced hot paths ------------------------------------------------------

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        async with record_generation(
            request, provider_system=self._system, role=_role(request, self._default_role)
        ) as rec:
            resp = await self._inner.complete(request)
            rec.set_response(resp)
            return resp

    async def stream_tokens(
        self,
        request: CompletionRequest,
        on_token: Callable[[str], Awaitable[None]],
    ) -> CompletionResponse:
        async with record_generation(
            request, provider_system=self._system, role=_role(request, self._default_role)
        ) as rec:
            resp = await self._inner.stream_tokens(request, on_token)
            rec.set_response(resp)
            return resp

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        # Embeddings are high-volume and low-signal for the trace tree; delegate
        # without a per-call span (covered by aggregate metrics instead).
        return await self._inner.embed(request)

    # -- transparent delegation for everything else ----------------------------

    def __getattr__(self, name: str) -> Any:
        # Only reached for attributes TracedProvider does not define itself
        # (supports_vision, supports_tool_use, embed_batch, stream_complete, …).
        return getattr(self._inner, name)


def traced_role_providers(provider: Any) -> dict[str, TracedProvider]:
    """Wrap a resolved provider as three role-tagged traced providers for the
    planner/executor/verifier slots of AgentGraph, so every agent LLM call emits a
    role-attributed GenAI span."""
    system = provider_system_of(provider)
    return {
        role: TracedProvider(provider, provider_system=system, default_role=role)
        for role in ("planner", "executor", "verifier")
    }
