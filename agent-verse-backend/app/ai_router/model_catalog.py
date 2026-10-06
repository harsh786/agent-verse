"""Reference model catalog per provider, plus provider readiness.

The catalog lists models each supported provider serves, per capability
(reasoning, embeddings, vision, OCR, reranking), with reference list prices
(USD per 1k tokens; operators can edit any imported entry). Importing it
(``POST /models/catalog/import``) copies entries into the configured registry
store, where they take part in selection and failover.

A model whose provider has no credentials configured is *not ready*: it stays in
the registry (so the operator sees it and can add the key later) but selection
skips it — see :func:`provider_ready`.

On-prem (Qwen/Gemma vLLM cluster) and Ollama entries come from the deployment's
own settings, so the catalog names the models this deployment actually serves.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

# Capability bundles.
_REASON = ("text_generation", "tool_use", "structured_output")
_REASON_VISION = ("text_generation", "tool_use", "structured_output", "vision", "ocr")
_VISION = ("text_generation", "vision", "ocr")
_EMBED = ("embedding",)
_RERANK = ("rerank",)

# Output width of embedding models whose dimension is known (model id → dims).
# Vectors of a model whose width differs from the vector index (``EMBEDDING_DIM``)
# cannot be stored, so registry-driven embedder selection refuses such a model.
# A model missing here is unknown (``None``) and is not checked.
# NVIDIA widths verified live against integrate.api.nvidia.com (2048-d each).
KNOWN_EMBEDDING_DIMENSIONS: dict[str, int] = {
    "nvidia/nemotron-3-embed-1b": 2048,
    "nvidia/llama-nemotron-embed-vl-1b-v2": 2048,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "gemini-embedding-001": 3072,
    "voyage-3.5": 1024,
    "voyage-3.5-lite": 1024,
    "Qwen/Qwen3-Embedding-0.6B": 1024,
    "nomic-embed-text": 768,
}


@dataclass(frozen=True)
class CatalogModel:
    model_id: str
    capabilities: tuple[str, ...]
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    quality_score: float = 0.7
    display_name: str = ""
    # Embedding output width when known (None = unknown, not checked).
    dimensions: int | None = None

    def endpoint(self, provider: str) -> dict[str, Any]:
        caps = list(self.capabilities)
        out: dict[str, Any] = {
            "provider": provider,
            "model_id": self.model_id,
            "display_name": self.display_name or self.model_id,
            "capabilities": caps,
            "cost_per_1k_input": self.cost_per_1k_input,
            "cost_per_1k_output": self.cost_per_1k_output,
            "supports_tools": "tool_use" in caps,
            "supports_vision": "vision" in caps,
            "supports_structured_output": "structured_output" in caps,
            "quality_score": self.quality_score,
            "is_available": True,
            # Imported from the catalog: ranks after the deployment's own models
            # until the operator puts it in the preference order.
            "origin": "catalog",
        }
        if "embedding" in caps:
            out["dimensions"] = self.dimensions
        return out


@dataclass(frozen=True)
class CatalogProvider:
    provider: str
    label: str
    env_hint: str
    models: tuple[CatalogModel, ...] = field(default_factory=tuple)


_M = CatalogModel

_STATIC: tuple[CatalogProvider, ...] = (
    # NVIDIA entries verified live against integrate.api.nvidia.com on
    # 2026-10-06 (chat with native tool calls, vision, embedding dimensions).
    # Retired there and removed: llama-3.3-70b-instruct, nemotron-super-49b,
    # qwen3-235b, gpt-oss-120b, llama-4-maverick (410/404); rerank models need
    # the account to be entitled (404 "not found for account" otherwise).
    CatalogProvider(
        "nvidia",
        "NVIDIA (build.nvidia.com)",
        "NVIDIA_API_KEY",
        (
            _M("nvidia/nemotron-3-super-120b-a12b", _REASON, 0.0002, 0.0006, 0.86),
            _M("nvidia/nemotron-3-ultra-550b-a55b", _REASON, 0.0006, 0.0018, 0.91),
            _M("openai/gpt-oss-20b", _REASON, 0.00007, 0.0003, 0.74),
            _M("meta/llama-3.2-11b-vision-instruct", _VISION, 0.00005, 0.00015, 0.72),
            _M("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", _VISION, 0.0001, 0.0004, 0.80),
            # Answers correctly but took ~200s on the hosted API: vision only, not
            # in the OCR order by default (raise AGENTVERSE_VISION_CALL_TIMEOUT_SECONDS
            # and add OCR to it in the registry to use it for OCR).
            _M("meta/llama-3.2-90b-vision-instruct", ("text_generation", "vision"), 0.0002,
               0.0006, 0.80),
            _M("nvidia/nemotron-3-embed-1b", _EMBED, 0.00002, 0.0, 0.80, dimensions=2048),
            _M("nvidia/llama-nemotron-embed-vl-1b-v2", _EMBED, 0.00002, 0.0, 0.79,
               dimensions=2048),
            _M("nvidia/nv-rerankqa-mistral-4b-v3", _RERANK, 0.00003, 0.0, 0.78),
        ),
    ),
    CatalogProvider(
        "groq",
        "Groq",
        "GROQ_API_KEY",
        (
            _M("llama-3.1-8b-instant", _REASON, 0.00005, 0.00008, 0.62),
            _M("llama-3.3-70b-versatile", _REASON, 0.00059, 0.00079, 0.80),
            _M("openai/gpt-oss-20b", _REASON, 0.0001, 0.0005, 0.74),
            _M("openai/gpt-oss-120b", _REASON, 0.00015, 0.00075, 0.83),
            _M("qwen/qwen3-32b", _REASON, 0.00029, 0.00059, 0.79),
            _M("moonshotai/kimi-k2-instruct", _REASON, 0.001, 0.003, 0.84),
            _M("meta-llama/llama-4-scout-17b-16e-instruct", _REASON_VISION, 0.00011, 0.00034, 0.76),
            _M("meta-llama/llama-4-maverick-17b-128e-instruct", _REASON_VISION, 0.0002, 0.0006,
               0.80),
        ),
    ),
    CatalogProvider(
        "xai",
        "xAI (Grok)",
        "XAI_API_KEY",
        (
            _M("grok-3-mini", _REASON, 0.0003, 0.0005, 0.80),
            _M("grok-4", _REASON_VISION, 0.003, 0.015, 0.92),
            _M("grok-2-vision-1212", _VISION, 0.002, 0.01, 0.80),
        ),
    ),
    CatalogProvider(
        "anthropic",
        "Anthropic Claude",
        "ANTHROPIC_API_KEY",
        (
            _M("claude-haiku-4-5-20251001", _REASON_VISION, 0.001, 0.005, 0.85),
            _M("claude-sonnet-5-5", _REASON_VISION, 0.003, 0.015, 0.94),
            _M("claude-opus-5-5", _REASON_VISION, 0.005, 0.025, 0.97),
        ),
    ),
    CatalogProvider(
        "openai",
        "OpenAI",
        "OPENAI_API_KEY",
        (
            _M("gpt-5-nano", _REASON_VISION, 0.00005, 0.0004, 0.72),
            _M("gpt-4o-mini", _REASON_VISION, 0.00015, 0.0006, 0.75),
            _M("gpt-5-mini", _REASON_VISION, 0.00025, 0.002, 0.86),
            _M("gpt-5", _REASON_VISION, 0.00125, 0.01, 0.94),
            _M("gpt-4.1", _REASON_VISION, 0.002, 0.008, 0.88),
            _M("text-embedding-3-small", _EMBED, 0.00002, 0.0, 0.78, dimensions=1536),
            _M("text-embedding-3-large", _EMBED, 0.00013, 0.0, 0.85, dimensions=3072),
        ),
    ),
    CatalogProvider(
        "gemini",
        "Google Gemini",
        "GOOGLE_API_KEY",
        (
            _M("gemini-2.5-flash-lite", _REASON_VISION, 0.0001, 0.0004, 0.76),
            _M("gemini-2.5-flash", _REASON_VISION, 0.0003, 0.0025, 0.86),
            _M("gemini-2.5-pro", _REASON_VISION, 0.00125, 0.01, 0.93),
            _M("gemini-embedding-001", _EMBED, 0.00015, 0.0, 0.84, dimensions=3072),
        ),
    ),
    CatalogProvider(
        "voyage",
        "Voyage AI",
        "VOYAGE_API_KEY",
        (
            _M("voyage-3.5-lite", _EMBED, 0.00002, 0.0, 0.80, dimensions=1024),
            _M("voyage-3.5", _EMBED, 0.00006, 0.0, 0.86, dimensions=1024),
            _M("rerank-2.5-lite", _RERANK, 0.00002, 0.0, 0.80),
            _M("rerank-2.5", _RERANK, 0.00005, 0.0, 0.86),
        ),
    ),
)


def _settings() -> Any:
    try:
        from app.core.config import get_settings

        return get_settings()
    except Exception:  # pragma: no cover - defensive
        return None


def _onprem_embed_dim(s: Any, model_id: str) -> int | None:
    known = KNOWN_EMBEDDING_DIMENSIONS.get(model_id)
    if known:
        return known
    declared = getattr(s, "onprem_embedding_dim", None)
    return int(declared) if isinstance(declared, int) and declared > 0 else None


def _onprem_provider() -> CatalogProvider:
    s = _settings()
    qwen = str(getattr(s, "onprem_qwen_model", "") or "Qwen/Qwen3.5-4B")
    gemma = str(getattr(s, "onprem_gemma_model", "") or "google/gemma-4-E2B")
    embed = str(getattr(s, "onprem_embedding_model", "") or "Qwen/Qwen3-Embedding-0.6B")
    rerank = str(getattr(s, "onprem_reranker_model", "") or "Qwen/Qwen3-Reranker-0.6B")
    return CatalogProvider(
        "onprem",
        "Qwen on-prem (vLLM)",
        "ONPREM_ENABLED + ONPREM_QWEN_BASE_URL",
        (
            _M(qwen, _REASON, 0.0, 0.0, 0.74),
            _M(gemma, _REASON, 0.0, 0.0, 0.62),
            # The deployment declares its on-prem model's width (ONPREM_EMBEDDING_DIM)
            # when the model is not one of the known ones.
            _M(embed, _EMBED, 0.0, 0.0, 0.76, dimensions=_onprem_embed_dim(s, embed)),
            _M(rerank, _RERANK, 0.0, 0.0, 0.76),
        ),
    )


def _ollama_provider() -> CatalogProvider:
    s = _settings()
    chat = str(getattr(s, "ollama_default_model", "") or "qwen3:8b")
    embed = str(getattr(s, "ollama_embed_model", "") or "nomic-embed-text")
    ocr = str(getattr(s, "ollama_ocr_model", "") or "glm-ocr:latest")
    models = [
        _M(chat, _REASON, 0.0, 0.0, 0.66),
        _M("llama3.1:8b", _REASON, 0.0, 0.0, 0.62),
        _M("qwen3:8b", _REASON, 0.0, 0.0, 0.66),
        _M(embed, _EMBED, 0.0, 0.0, 0.72, dimensions=KNOWN_EMBEDDING_DIMENSIONS.get(embed)),
        _M("nomic-embed-text", _EMBED, 0.0, 0.0, 0.68, dimensions=768),
        _M(ocr, _VISION, 0.0, 0.0, 0.70),
        _M("qwen2.5vl:7b", _VISION, 0.0, 0.0, 0.72),
        _M("llama3.2-vision:11b", _VISION, 0.0, 0.0, 0.70),
    ]
    unique = tuple({m.model_id: m for m in reversed(models)}.values())[::-1]
    return CatalogProvider("ollama", "Ollama (local)", "OLLAMA_BASE_URL", unique)


def catalog_providers() -> list[CatalogProvider]:
    """Every catalog provider, the deployment-specific ones built from settings."""
    providers = list(_STATIC)
    providers.insert(1, _onprem_provider())
    providers.append(_ollama_provider())
    return providers


def catalog_endpoints(
    providers: list[str] | None = None, model_ids: list[str] | None = None
) -> list[dict[str, Any]]:
    """Catalog entries as registry endpoint dicts, optionally filtered."""
    wanted_p = {p.strip() for p in providers or [] if p and p.strip()}
    wanted_m = {m.strip() for m in model_ids or [] if m and m.strip()}
    out: list[dict[str, Any]] = []
    for cp in catalog_providers():
        if wanted_p and cp.provider not in wanted_p:
            continue
        for m in cp.models:
            keys = {m.model_id, f"{cp.provider}/{m.model_id}"}
            if wanted_m and not keys & wanted_m:
                continue
            out.append(m.endpoint(cp.provider))
    return out


def catalog_embedding_dimension(model_id: str) -> int | None:
    """Output width of embedding model *model_id*, or ``None`` when unknown.

    Keyed by model id alone: the same model id served by another provider or
    endpoint produces vectors of the same width (and in the same space).
    """
    mid = (model_id or "").strip()
    if not mid:
        return None
    if mid in KNOWN_EMBEDDING_DIMENSIONS:
        return KNOWN_EMBEDDING_DIMENSIONS[mid]
    for cp in catalog_providers():
        for m in cp.models:
            if m.model_id == mid and m.dimensions:
                return m.dimensions
    return None


# ── Provider readiness ───────────────────────────────────────────────────────

_PROVIDER_ENV: dict[str, tuple[str, ...]] = {
    "nvidia": ("NVIDIA_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "xai": ("XAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GOOGLE_API_KEY",),
    "google": ("GOOGLE_API_KEY",),
    "voyage": ("VOYAGE_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
    "cohere": ("COHERE_API_KEY",),
}


def _env(name: str) -> str:
    try:
        from app.core.config import get_provider_env

        return get_provider_env(name)
    except Exception:  # pragma: no cover - defensive
        return os.getenv(name, "")


def provider_ready(provider: str) -> bool:
    """True when this deployment has what *provider* needs to serve a call.

    Unknown providers ("custom", "openai_compatible", …) count as ready: their
    endpoint is configured outside this table, so they keep today's behaviour.
    """
    p = (provider or "").strip().lower()
    if p in _PROVIDER_ENV:
        return any(_env(name) for name in _PROVIDER_ENV[p])
    if p == "onprem":
        s = _settings()
        return bool(
            getattr(s, "onprem_enabled", False)
            and str(getattr(s, "onprem_qwen_base_url", "") or "").strip()
        )
    if p == "ollama":
        s = _settings()
        return bool(_env("OLLAMA_BASE_URL") or str(getattr(s, "ollama_base_url", "") or "").strip())
    return True


def provider_for_endpoint_url(url: str) -> str | None:
    """The provider behind an embedding / reranker endpoint URL, or ``None``.

    Embedding and reranking calls go to ONE configured endpoint, which serves
    only its own provider's models — so the registry may only pick among that
    provider's models. ``None`` (unrecognised endpoint) means the caller keeps
    its configured model.
    """
    from urllib.parse import urlparse

    raw = (url or "").strip()
    if not raw:
        return None
    s = _settings()
    for attr in (
        "onprem_qwen_base_url",
        "onprem_gemma_base_url",
        "onprem_embedding_base_url",
        "onprem_reranker_url",
    ):
        base = str(getattr(s, attr, "") or "").strip().rstrip("/")
        if base and raw.rstrip("/").startswith(base):
            return "onprem"
    ollama = (_env("OLLAMA_BASE_URL") or str(getattr(s, "ollama_base_url", "") or "")).strip()
    if ollama and raw.rstrip("/").startswith(ollama.rstrip("/")):
        return "ollama"
    host = (urlparse(raw).hostname or "").lower()
    for needle, provider in (
        ("nvidia.com", "nvidia"),
        ("voyageai.com", "voyage"),
        ("cohere", "cohere"),
        ("api.openai.com", "openai"),
        ("googleapis.com", "gemini"),
        ("groq.com", "groq"),
    ):
        if needle in host:
            return provider
    return None
