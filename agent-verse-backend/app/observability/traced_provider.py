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
from typing import Any, cast

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
        # The configured provider type wins (set by the registry / tenant
        # provider / on-prem dispatcher); a MultiEndpointLLMProvider used to
        # report its class name.
        for holder in (type(inner), inner):
            ptype = getattr(holder, "_agentverse_provider_type", None)
            if isinstance(ptype, str) and ptype:
                return ptype
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


def _cost_usd(resp: Any) -> float | None:
    """The call's cost from the single pricing source (None when not computable)."""
    try:
        from app.intelligence.cost_tracker import calculate_cost

        return float(
            calculate_cost(
                str(getattr(resp, "model", "") or ""),
                int(getattr(resp, "input_tokens", 0) or 0),
                int(getattr(resp, "output_tokens", 0) or 0),
            )
        )
    except Exception:
        return None


def record_response(rec: Any, resp: Any) -> None:
    """Attach the response, its cost and whether it was served from a cache."""
    rec.set_response(
        resp, cost_usd=_cost_usd(resp), cache_hit=bool(getattr(resp, "cache_hit", False))
    )


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
            record_response(rec, resp)
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
            record_response(rec, resp)
            return resp

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        # A light span (no content): embeddings were invisible in traces.
        from app.observability.genai import record_embedding

        with record_embedding(
            request, provider_system=self._system, default_model=self._embed_model_name()
        ):
            return await self._inner.embed(request)

    def _embed_model_name(self) -> str:
        from app.providers.embedder_factory import embedder_model_name

        try:
            name = embedder_model_name(self._inner)
        except Exception:
            return ""
        return "" if name == type(self._inner).__name__ else name

    def _traced_embed_batch(self, inner_batch: Callable[..., Any]) -> Callable[..., Any]:
        async def embed_batch(texts: list[str]) -> list[list[float]]:
            from app.observability.genai import record_embedding

            with record_embedding(
                EmbedRequest(texts=list(texts)),
                provider_system=self._system,
                default_model=self._embed_model_name(),
            ):
                return cast("list[list[float]]", await inner_batch(texts))

        return embed_batch

    # -- transparent delegation for everything else ----------------------------

    def __getattr__(self, name: str) -> Any:
        # Only reached for attributes TracedProvider does not define itself
        # (supports_vision, supports_tool_use, stream_complete, …). embed_batch is
        # traced but stays absent when the inner provider has none, so
        # ``hasattr(provider, "embed_batch")`` feature checks keep working.
        attr = getattr(self._inner, name)
        if name == "embed_batch" and callable(attr):
            return self._traced_embed_batch(attr)
        return attr


def unwrap_provider(provider: Any) -> Any:
    """The provider inside any TracedProvider wrapping (``provider`` itself otherwise)."""
    seen = 0
    while seen < 8:
        if isinstance(provider, TracedProvider):
            provider = provider._inner
        elif getattr(type(provider), "_agentverse_embedder_proxy", False):
            # RegistryReloadingEmbedder: the embedder it currently serves.
            provider = provider.current
        else:
            break
        seen += 1
    return provider


def traced_embedder(embedder: Any, *, provider_system: str = "") -> Any:
    """Wrap the deployment embedder so every embed emits a ``gen_ai.embeddings`` span.

    a01-F024-01: only the agent role providers and decision calls were traced, so
    ingestion, retrieval and re-embedding vectors never appeared in traces.
    ``None`` stays ``None`` and an already-traced embedder is returned as is.
    """
    if embedder is None or isinstance(embedder, TracedProvider):
        return embedder
    system = provider_system or provider_system_of(embedder)
    return TracedProvider(embedder, provider_system=system)


def traced_role_providers(provider: Any) -> dict[str, TracedProvider]:
    """Wrap a resolved provider as three role-tagged traced providers for the
    planner/executor/verifier slots of AgentGraph, so every agent LLM call emits a
    role-attributed GenAI span."""
    system = provider_system_of(provider)
    return {
        role: TracedProvider(provider, provider_system=system, default_role=role)
        for role in ("planner", "executor", "verifier")
    }
