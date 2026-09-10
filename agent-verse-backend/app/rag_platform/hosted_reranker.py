"""Hosted reranker — a first-class managed cross-encoder reranking provider.

Calls a Cohere-compatible ``/v1/rerank`` HTTP endpoint (Cohere, Voyage, Jina, or
a self-hosted equivalent) over HTTPS. This is the vendor-agnostic managed
alternative to the local cross-encoder/LLM rerankers: no model download, no GPU,
just an API call. Selected via the ``hosted`` rerank strategy when a URL is
configured.

Contract:
  * **SSRF-guarded** — the endpoint URL is validated (public, https) before any
    request, blocking metadata/loopback/RFC-1918 targets.
  * **Honest failure** — network/parse/auth errors raise ``HostedRerankerError``;
    callers (RerankPolicy) degrade to the local path rather than dropping results.
  * **Injectable client** — an httpx-compatible async client can be passed in for
    tests; otherwise one is created per call and closed.

Request shape:  ``{"model", "query", "documents": [...], "top_n"}`` + Bearer auth.
Response shape: ``{"results": [{"index": int, "relevance_score": float}, ...]}``.
"""

from __future__ import annotations

from typing import Any

from app.net.ssrf_guard import SSRFError, assert_public_url
from app.observability.logging import get_logger

logger = get_logger(__name__)


class HostedRerankerError(RuntimeError):
    """Raised when the hosted rerank endpoint is unreachable or returns garbage."""


class HostedReranker:
    """Client for a Cohere-compatible ``/v1/rerank`` endpoint."""

    def __init__(
        self,
        *,
        url: str,
        api_key: str = "",
        model: str = "rerank-english-v3.0",
        timeout_seconds: float = 10.0,
        client: Any = None,
    ) -> None:
        if not url or not url.strip():
            raise ValueError("hosted reranker url is required")
        self._url = url.strip()
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._client = client  # injectable httpx.AsyncClient for tests

    async def rerank(
        self, query: str, documents: list[str], top_k: int | None = None
    ) -> list[tuple[int, float]]:
        """Return ``(original_index, relevance_score)`` pairs, best score first.

        Raises ``HostedRerankerError`` on any transport/HTTP/parse failure so the
        caller can fall back to a local reranker.
        """
        if not documents:
            return []

        # SSRF egress guard — fail closed before any network call.
        try:
            assert_public_url(self._url, context="hosted_reranker")
        except (SSRFError, ValueError) as exc:
            raise HostedRerankerError(f"hosted reranker url blocked: {exc}") from exc

        payload: dict[str, Any] = {
            "model": self._model,
            "query": query,
            "documents": documents,
        }
        if top_k is not None:
            payload["top_n"] = int(top_k)
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            data = await self._post(payload, headers)
        except HostedRerankerError:
            raise
        except Exception as exc:  # transport/timeout
            raise HostedRerankerError(f"hosted reranker request failed: {exc}") from exc

        return self._parse(data, n_documents=len(documents))

    async def _post(self, payload: dict[str, Any], headers: dict[str, str]) -> Any:
        import httpx

        if self._client is not None:
            resp = await self._client.post(self._url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    def _parse(data: Any, *, n_documents: int) -> list[tuple[int, float]]:
        if not isinstance(data, dict) or "results" not in data:
            raise HostedRerankerError("hosted reranker response missing 'results'")
        results = data["results"]
        if not isinstance(results, list):
            raise HostedRerankerError("hosted reranker 'results' is not a list")
        pairs: list[tuple[int, float]] = []
        for item in results:
            if not isinstance(item, dict):
                raise HostedRerankerError("hosted reranker result is not an object")
            idx = item.get("index")
            score = item.get("relevance_score", item.get("score"))
            if not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < n_documents:
                raise HostedRerankerError("hosted reranker result index is invalid")
            if isinstance(score, bool) or not isinstance(score, int | float):
                raise HostedRerankerError("hosted reranker result score is invalid")
            pairs.append((idx, float(score)))
        pairs.sort(key=lambda p: p[1], reverse=True)
        return pairs


def hosted_reranker_from_settings(settings: Any) -> HostedReranker | None:
    """Build a HostedReranker from Settings, or None when no URL is configured."""
    url = str(getattr(settings, "rag_hosted_reranker_url", "") or "").strip()
    if not url:
        return None
    return HostedReranker(
        url=url,
        api_key=str(getattr(settings, "rag_hosted_reranker_api_key", "") or ""),
        model=str(getattr(settings, "rag_hosted_reranker_model", "rerank-english-v3.0")),
        timeout_seconds=float(getattr(settings, "rag_hosted_reranker_timeout_seconds", 10.0)),
    )


def is_hosted_reranker_configured(settings: Any) -> bool:
    """True when a hosted reranker URL is set (the strategy can be used)."""
    return bool(str(getattr(settings, "rag_hosted_reranker_url", "") or "").strip())
