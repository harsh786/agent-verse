"""Model Registry driven reranking with ordered failover.

The hosted reranker used to call ONE env-configured endpoint. Rerank models the
operator registered in the Model Registry (catalog imports for NVIDIA / Voyage /
Cohere, the on-prem reranker, a custom Cohere-compatible endpoint) were listed
but never called. :func:`reranker_chain_from_settings` now builds an ordered
chain of rerank targets:

1. every eligible configured rerank model, in the registry's execution order
   (operator preference order, then cheapest) — each one sent to its OWN
   provider's rerank API (:class:`NvidiaReranker`, :class:`VoyageReranker`,
   :class:`CohereReranker`), or, for on-prem / custom / env-seeded models, to the
   Cohere-compatible :class:`HostedReranker` shape against the model's OWN
   registry endpoint (``base_url``, e.g. ``http://host:30083/v1`` → ``/v1/rerank``)
   or else the configured on-prem / ``RAG_HOSTED_RERANKER_URL`` endpoint;
2. the env/settings endpoints — ``RAG_HOSTED_RERANKER_URL``/``MODEL``, then
   ``ONPREM_RERANKER_URL``/``MODEL`` (unless step 1 already covers the same
   endpoint + model);

and :class:`FailoverReranker` tries them in turn. Any failure (HTTP error,
timeout, malformed or incomplete payload) logs ``rerank_failover from=… to=…
error=…`` and moves on; when every target failed :class:`HostedRerankerError` is
raised so the caller (RerankPolicy) degrades to its local reranker. One target's
full answer is returned — scores of different models are never mixed.

Native adapters use the SSRF-pinned ``public_async_client`` with the operator's
private-network allowance, and only providers with credentials are tried.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.net.ssrf_guard import private_access_networks, public_async_client
from app.rag_platform.hosted_reranker import HostedReranker, HostedRerankerError

# Plain stdlib logger (like ``llm_model_failover`` in circuit_breaker): %-style
# messages, greppable as ``rerank_failover from=… to=… error=…``.
logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 10.0

NVIDIA_RERANK_BASE_URL = "https://ai.api.nvidia.com/v1/retrieval"
VOYAGE_RERANK_URL = "https://api.voyageai.com/v1/rerank"
COHERE_RERANK_URL = "https://api.cohere.com/v2/rerank"


class Reranker(Protocol):
    """Anything that reranks: ``(original_index, score)`` pairs, best first."""

    async def rerank(
        self, query: str, documents: list[str], top_k: int | None = None
    ) -> list[tuple[int, float]]: ...


def _env(name: str) -> str:
    from app.core.config import get_provider_env

    return get_provider_env(name)


def _parse_pairs(
    items: Any, *, n_documents: int, score_keys: Sequence[str], label: str
) -> list[tuple[int, float]]:
    """Validate ``[{"index": i, <score_key>: s}, ...]`` into sorted pairs."""
    if not isinstance(items, list):
        raise HostedRerankerError(f"{label} rerank response is not a list")
    pairs: list[tuple[int, float]] = []
    for item in items:
        if not isinstance(item, dict):
            raise HostedRerankerError(f"{label} rerank result is not an object")
        idx = item.get("index")
        score: Any = None
        for key in score_keys:
            if key in item:
                score = item[key]
                break
        if not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < n_documents:
            raise HostedRerankerError(f"{label} rerank result index is invalid")
        if isinstance(score, bool) or not isinstance(score, int | float):
            raise HostedRerankerError(f"{label} rerank result score is invalid")
        pairs.append((idx, float(score)))
    pairs.sort(key=lambda p: p[1], reverse=True)
    return pairs


class _NativeReranker:
    """Base for a provider's own rerank API (fixed public URL, Bearer auth)."""

    provider = ""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        timeout_seconds: float = _DEFAULT_TIMEOUT_S,
        client: Any = None,
    ) -> None:
        if not model:
            raise ValueError("rerank model is required")
        self.model = model
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._client = client  # injectable httpx-compatible async client (tests)

    # -- provider specifics ------------------------------------------------
    def url(self) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    def payload(self, query: str, documents: list[str], top_k: int | None) -> dict[str, Any]:
        raise NotImplementedError  # pragma: no cover - abstract

    def parse(self, data: Any, n_documents: int) -> list[tuple[int, float]]:
        raise NotImplementedError  # pragma: no cover - abstract

    # -- shared transport --------------------------------------------------
    async def rerank(
        self, query: str, documents: list[str], top_k: int | None = None
    ) -> list[tuple[int, float]]:
        if not documents:
            return []
        if not self._api_key:
            raise HostedRerankerError(f"{self.provider} reranker has no API key")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        body = self.payload(query, documents, top_k)
        try:
            data = await self._post(self.url(), body, headers)
        except HostedRerankerError:
            raise
        except Exception as exc:  # transport / timeout / HTTP status
            raise HostedRerankerError(
                f"{self.provider} rerank request failed: {type(exc).__name__}: {exc}"
            ) from exc
        return self.parse(data, len(documents))

    async def _post(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> Any:
        if self._client is not None:
            resp = await self._client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            return resp.json()
        async with public_async_client(
            allowed_networks=private_access_networks(), timeout=self._timeout
        ) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            return resp.json()


class NvidiaReranker(_NativeReranker):
    """NVIDIA API Catalog (build.nvidia.com) retrieval reranking.

    ``POST {base}/{model path}/reranking`` where the model path is the model id
    with ``/`` kept and ``.`` written as ``_`` (``nvidia/llama-3.2-nv-rerankqa-1b-v2``
    → ``nvidia/llama-3_2-nv-rerankqa-1b-v2``), as NVIDIA's published endpoints do.
    Body ``{"model", "query": {"text"}, "passages": [{"text"}], "truncate"}``;
    answer ``{"rankings": [{"index", "logit"}]}``.
    """

    provider = "nvidia"

    def __init__(self, *, base_url: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._base = (base_url or NVIDIA_RERANK_BASE_URL).rstrip("/")

    def url(self) -> str:
        path = self.model.strip("/").replace(".", "_")
        return f"{self._base}/{path}/reranking"

    def payload(self, query: str, documents: list[str], top_k: int | None) -> dict[str, Any]:
        return {
            "model": self.model,
            "query": {"text": query},
            "passages": [{"text": d} for d in documents],
            "truncate": "END",
        }

    def parse(self, data: Any, n_documents: int) -> list[tuple[int, float]]:
        if not isinstance(data, dict) or "rankings" not in data:
            raise HostedRerankerError("nvidia rerank response missing 'rankings'")
        return _parse_pairs(
            data["rankings"], n_documents=n_documents, score_keys=("logit", "score"),
            label="nvidia",
        )


class VoyageReranker(_NativeReranker):
    """Voyage AI ``POST /v1/rerank``: ``{"query", "documents", "model", "top_k"}``
    → ``{"data": [{"index", "relevance_score"}]}``."""

    provider = "voyage"

    def url(self) -> str:
        return VOYAGE_RERANK_URL

    def payload(self, query: str, documents: list[str], top_k: int | None) -> dict[str, Any]:
        body: dict[str, Any] = {"query": query, "documents": documents, "model": self.model}
        if top_k is not None:
            body["top_k"] = int(top_k)
        return body

    def parse(self, data: Any, n_documents: int) -> list[tuple[int, float]]:
        if not isinstance(data, dict) or "data" not in data:
            raise HostedRerankerError("voyage rerank response missing 'data'")
        return _parse_pairs(
            data["data"], n_documents=n_documents, score_keys=("relevance_score",),
            label="voyage",
        )


class CohereReranker(_NativeReranker):
    """Cohere ``POST /v2/rerank``: ``{"model", "query", "documents", "top_n"}`` →
    ``{"results": [{"index", "relevance_score"}]}``."""

    provider = "cohere"

    def url(self) -> str:
        return COHERE_RERANK_URL

    def payload(self, query: str, documents: list[str], top_k: int | None) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self.model, "query": query, "documents": documents}
        if top_k is not None:
            body["top_n"] = int(top_k)
        return body

    def parse(self, data: Any, n_documents: int) -> list[tuple[int, float]]:
        if not isinstance(data, dict) or "results" not in data:
            raise HostedRerankerError("cohere rerank response missing 'results'")
        return _parse_pairs(
            data["results"], n_documents=n_documents, score_keys=("relevance_score",),
            label="cohere",
        )


_NATIVE: dict[str, tuple[type[_NativeReranker], str]] = {
    "nvidia": (NvidiaReranker, "NVIDIA_API_KEY"),
    "voyage": (VoyageReranker, "VOYAGE_API_KEY"),
    "cohere": (CohereReranker, "COHERE_API_KEY"),
}


@dataclass(frozen=True)
class RerankTarget:
    """One step of the chain: a label for logs, its dedupe key and the client."""

    label: str
    key: tuple[str, str]
    reranker: Reranker


class FailoverReranker:
    """Try each target in order; the first complete, valid answer wins."""

    def __init__(self, targets: Sequence[RerankTarget]) -> None:
        if not targets:
            raise ValueError("at least one rerank target is required")
        self.targets = list(targets)
        self.last_model = ""  # label of the target that answered last

    async def rerank(
        self, query: str, documents: list[str], top_k: int | None = None
    ) -> list[tuple[int, float]]:
        if not documents:
            return []
        last_exc: Exception | None = None
        for i, target in enumerate(self.targets):
            try:
                pairs = await target.reranker.rerank(query, documents, top_k)
                if top_k is not None:
                    pairs = pairs[: max(int(top_k), 0)]
                _check_complete(pairs, n_documents=len(documents), top_k=top_k)
            except Exception as exc:
                last_exc = exc
                nxt = self.targets[i + 1].label if i + 1 < len(self.targets) else "local"
                logger.warning(
                    "rerank_failover from=%s to=%s error=%s",
                    target.label, nxt, str(exc)[:200],
                )
                continue
            self.last_model = target.label
            return pairs
        raise HostedRerankerError(f"every reranker failed; last error: {last_exc}")


def _check_complete(pairs: list[tuple[int, float]], *, n_documents: int, top_k: int | None) -> None:
    """A partial or duplicated ranking is a bad payload: fail over instead of
    mixing it with another model's scores."""
    indices = [idx for idx, _ in pairs]
    if len(set(indices)) != len(indices):
        raise HostedRerankerError("reranker returned duplicate indices")
    expected = n_documents if top_k is None else min(int(top_k), n_documents)
    if len(indices) != expected:
        raise HostedRerankerError(
            f"reranker ranked {len(indices)} of {expected} documents"
        )


# ── chain construction ───────────────────────────────────────────────────────


def _norm(provider: str) -> str:
    p = (provider or "").strip().lower()
    return {"google": "gemini", "openai_compatible": "openai"}.get(p, p)


def _timeout(settings: Any) -> float:
    try:
        return float(getattr(settings, "rag_hosted_reranker_timeout_seconds", 0) or 0) or (
            _DEFAULT_TIMEOUT_S
        )
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_S


def _env_endpoint(settings: Any, model: str) -> RerankTarget | None:
    """The env-configured Cohere-compatible endpoint serving *model*."""
    url = str(getattr(settings, "rag_hosted_reranker_url", "") or "").strip()
    if not url or not model:
        return None
    return RerankTarget(
        label=f"endpoint/{model}",
        key=(url, model),
        reranker=HostedReranker(
            url=url,
            api_key=str(getattr(settings, "rag_hosted_reranker_api_key", "") or ""),
            model=model,
            timeout_seconds=_timeout(settings),
            allow_internal=bool(getattr(settings, "rag_hosted_reranker_allow_internal", False)),
        ),
    )


def _onprem_endpoint(settings: Any, model: str) -> RerankTarget | None:
    """The on-prem (vLLM) reranker — an operator-configured, trusted LAN URL."""
    url = str(getattr(settings, "onprem_reranker_url", "") or "").strip()
    if not url or not model:
        return None
    return RerankTarget(
        label=f"onprem/{model}",
        key=(url, model),
        reranker=HostedReranker(
            url=url,
            api_key=str(getattr(settings, "onprem_api_key", "") or ""),
            model=model,
            timeout_seconds=_timeout(settings),
            allow_internal=True,
        ),
    )


def _own_endpoint(m: Any, model: str, provider: str, settings: Any) -> RerankTarget | None:
    """A registry model that names its OWN endpoint (Model Registry ``base_url``,
    e.g. a vLLM Qwen3-Reranker at ``http://host:30083/v1``) is served there, with
    its own saved credential — never at an unrelated env endpoint."""
    from app.ai_router.model_endpoints import check_model_endpoint, endpoint_api_key

    base = str(getattr(m, "base_url", "") or "").strip()
    if not base:
        return None
    try:
        base = check_model_endpoint(base)
        api_key = endpoint_api_key(provider, m)
    except Exception as exc:
        logger.warning(
            "rerank_model_endpoint_unusable model=%s error=%s", model, str(exc)[:160]
        )
        return None
    url = base if base.rstrip("/").endswith("/rerank") else f"{base.rstrip('/')}/rerank"
    return RerankTarget(
        label=f"{provider or 'endpoint'}/{model}",
        key=(url, model),
        reranker=HostedReranker(
            url=url,
            api_key="" if api_key == "EMPTY" else api_key,
            model=model,
            timeout_seconds=_timeout(settings),
            # check_model_endpoint above applied the model-endpoint egress policy.
            allow_internal=True,
        ),
    )


def _target_for_model(m: Any, settings: Any) -> RerankTarget | None:
    """Where a registry rerank model is served, or None when nothing can serve it."""
    from app.ai_router.model_catalog import provider_for_endpoint_url, provider_ready

    model = str(getattr(m, "model_id", "") or "").strip()
    if not model:
        return None
    provider = _norm(str(getattr(m, "provider", "") or ""))
    source = (getattr(m, "extra", None) or {}).get("source")
    env_url = str(getattr(settings, "rag_hosted_reranker_url", "") or "").strip()

    # A self-hosted / custom model with its own endpoint is called there. Native
    # providers keep their own rerank API (their base_url is the chat endpoint).
    if provider not in _NATIVE:
        own = _own_endpoint(m, model, provider, settings)
        if own is not None:
            return own
    # Env-seeded: the deployment's own reranker, i.e. the env endpoint's model.
    if source == "env":
        if provider == "onprem":
            return _onprem_endpoint(settings, model) or _env_endpoint(settings, model)
        return _env_endpoint(settings, model)
    endpoint_provider = provider_for_endpoint_url(env_url) if env_url else None
    if provider in _NATIVE:
        cls, key_name = _NATIVE[provider]
        if provider_ready(provider):
            return RerankTarget(
                label=f"{provider}/{model}",
                key=(provider, model),
                reranker=cls(
                    model=model, api_key=_env(key_name), timeout_seconds=_timeout(settings)
                ),
            )
        # No key of its own: only an env endpoint of that same provider serves it.
        return _env_endpoint(settings, model) if endpoint_provider == provider else None
    if provider == "onprem":
        return _onprem_endpoint(settings, model) or (
            _env_endpoint(settings, model) if endpoint_provider == "onprem" else None
        )
    # Custom / unknown providers: the env endpoint when it serves this provider or
    # is unrecognised, else the on-prem endpoint.
    if env_url:
        return _env_endpoint(settings, model) if endpoint_provider in (None, provider) else None
    return _onprem_endpoint(settings, model)


def _registry_models() -> list[Any]:
    try:
        from app.ai_router.selection import ordered_configured_models

        return list(ordered_configured_models("rerank"))
    except Exception as exc:  # pragma: no cover - never block retrieval
        logger.debug("rerank_registry_unavailable error=%s", str(exc)[:120])
        return []


def registry_rerank_targets(
    settings: Any, *, models: Sequence[Any] | None = None
) -> list[tuple[Any, RerankTarget]]:
    """``(registry model, target)`` for every configured rerank model that can be
    served, in the registry's execution order (preference, then cheapest)."""
    out: list[tuple[Any, RerankTarget]] = []
    seen: set[tuple[str, str]] = set()
    for m in models if models is not None else _registry_models():
        target = _target_for_model(m, settings)
        if target is None:
            logger.debug(
                "rerank_model_skipped model=%s provider=%s",
                getattr(m, "model_id", ""), getattr(m, "provider", ""),
            )
            continue
        if target.key not in seen:
            seen.add(target.key)
            out.append((m, target))
    return out


def env_rerank_targets(
    settings: Any, *, exclude: set[tuple[str, str]] | None = None
) -> list[RerankTarget]:
    """The deployment's env/settings endpoints: ``RAG_HOSTED_RERANKER_URL``/``MODEL``,
    then the on-prem reranker (``ONPREM_RERANKER_URL``/``MODEL``). Endpoints in
    *exclude* (already served by a registry model) are not repeated."""
    from app.rag_platform import hosted_reranker as _hosted

    seen = set(exclude or ())
    targets: list[RerankTarget] = []
    env = _hosted.hosted_reranker_from_settings(settings)
    if env is not None:
        url = str(getattr(env, "_url", "") or "")
        model = str(getattr(env, "_model", "") or "")
        if (url, model) not in seen:
            seen.add((url, model))
            targets.append(RerankTarget(label=f"endpoint/{model}", key=(url, model), reranker=env))
    onprem_model = str(getattr(settings, "onprem_reranker_model", "") or "").strip()
    onprem = _onprem_endpoint(settings, onprem_model)
    if onprem is not None and onprem.key not in seen:
        targets.append(onprem)
    return targets


def reranker_chain_from_settings(
    settings: Any, *, models: Sequence[Any] | None = None
) -> FailoverReranker | None:
    """The ordered rerank chain, or None when nothing is configured.

    Registry rerank models first (execution order), then the env/settings
    endpoints. *models* overrides the registry lookup (tests); by default the
    registry's configured rerank models in execution order are used.
    """
    registry = [target for _m, target in registry_rerank_targets(settings, models=models)]
    targets = registry + env_rerank_targets(settings, exclude={t.key for t in registry})
    return FailoverReranker(targets) if targets else None
