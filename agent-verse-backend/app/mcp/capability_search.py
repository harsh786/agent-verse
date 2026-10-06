"""Semantic and keyword-based tool capability search.

Falls back to keyword matching when no embedder is configured.
When an embedder is available, uses cosine similarity over cached embeddings:
every tool descriptor used to be re-embedded on every query (a02-F032-04). The
vectors now live in a bounded, expiring process cache keyed by the embedding
model and a digest of the text, so only the query and descriptors not seen
before are embedded. With a tenant those embeddings are charged against the
tenant's budget and metered (:func:`app.embedding.metering.embed_metered`).
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any

from app.mcp.bounded_cache import BoundedTTLCache
from app.tenancy.context import TenantContext

EMBED_CACHE_MAX = 20_000
EMBED_CACHE_TTL_S = 24 * 3600.0
_EMBED_CACHE: BoundedTTLCache[str, list[float]] = BoundedTTLCache(
    maxsize=EMBED_CACHE_MAX, ttl_s=EMBED_CACHE_TTL_S
)


def reset_embedding_cache() -> None:
    """Test seam: forget every cached descriptor / query vector."""
    _EMBED_CACHE.clear()


def _embedder_cache_scope(embedder: Any) -> str:
    """The model identity vectors are cached under.

    A real model name is shared by every embedder object of that model (the same
    text gives the same vector); without one, the vectors belong to this object.
    """
    from app.providers.embedder_factory import embedder_model_name

    model = embedder_model_name(embedder)
    if not model or model == type(embedder).__name__:
        return f"{type(embedder).__qualname__}@{id(embedder)}"
    return model


@dataclass
class ToolMatch:
    """A tool that matched a capability query."""

    server_id: str
    tool_name: str
    description: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_id": self.server_id,
            "tool_name": self.tool_name,
            "description": self.description,
            "score": round(self.score, 4),
        }


class CapabilitySearch:
    """Find tools matching a natural-language capability query.

    Usage::

        search = CapabilitySearch()                          # keyword mode
        search = CapabilitySearch(embedder=my_provider)      # semantic mode
        search = CapabilitySearch(tools=my_tools, embedder=my_provider)  # pre-loaded

    Both modes return a ranked list of :class:`ToolMatch` objects.
    """

    def __init__(
        self,
        *,
        tools: Any = None,
        embedder: Any = None,
        cost_controller: Any = None,
        meter: bool = False,
    ) -> None:
        self._embedder = embedder
        self._tools: list[dict[str, Any]] | None = (
            self._normalize_tools(tools) if tools is not None else None
        )
        # meter=True: embeddings made for a tenant (search(tenant_ctx=...)) are
        # reserved against its budget (cost_controller, or the process one) and
        # recorded as usage.
        self._cost_controller = cost_controller
        self._meter = meter

    # ── normalisation ─────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_tools(tools: Any) -> list[dict[str, Any]]:
        """Coerce a mixed list of dicts or ToolDefinition objects to dicts."""
        result: list[dict[str, Any]] = []
        for t in tools:
            if isinstance(t, dict):
                result.append(t)
            else:
                result.append(
                    {
                        "name": getattr(t, "name", ""),
                        "description": getattr(t, "description", ""),
                        "server_id": getattr(t, "server_id", ""),
                        "input_schema": getattr(t, "input_schema", {}),
                    }
                )
        return result

    # ── cosine similarity ─────────────────────────────────────────────────────

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        """Return the cosine similarity between two vectors."""
        if len(a) != len(b) or not a:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))
        if mag_a == 0.0 or mag_b == 0.0:
            return 0.0
        return dot / (mag_a * mag_b)

    # ── keyword fallback ──────────────────────────────────────────────────────

    @staticmethod
    def _keyword_score(query: str, tool: dict[str, Any]) -> float:
        """Jaccard-style overlap between query and tool name + description."""

        def tokens(text: str) -> set[str]:
            return {w.lower() for w in re.findall(r"[a-z0-9]+", text.lower())}

        q_tokens = tokens(query)
        t_tokens = tokens(f"{tool.get('name', '')} {tool.get('description', '')}")
        if not q_tokens or not t_tokens:
            return 0.0
        overlap = q_tokens & t_tokens
        return len(overlap) / max(len(q_tokens), len(t_tokens))

    # ── public API ────────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        tools: list[Any] | None = None,
        *,
        tenant_ctx: TenantContext | None = None,
        top_k: int = 5,
        threshold: float = 0.0,
    ) -> list[ToolMatch]:
        """Return the top-*k* tools matching *query*.

        Parameters
        ----------
        query:
            Natural-language description of the desired capability.
        tools:
            Flat list of tool dicts or ToolDefinition objects (each with at
            least ``name`` and ``description`` keys / attributes, optionally
            ``server_id``).  When omitted, the tools passed to ``__init__``
            are used.
        tenant_ctx:
            The calling tenant — results are scoped to the supplied tools
            so tenant isolation is the caller's responsibility.
        top_k:
            Maximum number of results to return.
        threshold:
            Minimum score for a tool to appear in results.
        """
        # Resolve tool list: explicit argument > stored in __init__ > empty.
        if tools is not None:
            resolved: list[dict[str, Any]] = self._normalize_tools(tools)
        elif self._tools is not None:
            resolved = self._tools
        else:
            resolved = []

        if not resolved:
            return []

        if self._embedder is not None:
            return await self._search_semantic(
                query, resolved, top_k=top_k, threshold=threshold, tenant_ctx=tenant_ctx
            )
        return self._search_keyword(query, resolved, top_k=top_k, threshold=threshold)

    def _search_keyword(
        self,
        query: str,
        tools: list[dict[str, Any]],
        *,
        top_k: int,
        threshold: float,
    ) -> list[ToolMatch]:
        matches: list[ToolMatch] = []
        for tool in tools:
            score = self._keyword_score(query, tool)
            if score > threshold:
                matches.append(
                    ToolMatch(
                        server_id=str(tool.get("server_id", "")),
                        tool_name=str(tool.get("name", "")),
                        description=str(tool.get("description", "")),
                        score=score,
                    )
                )
        matches.sort(key=lambda m: m.score, reverse=True)
        return matches[:top_k]

    async def _embed_cached(
        self, texts: list[str], *, tenant_ctx: TenantContext | None
    ) -> list[list[float]]:
        """One vector per text: cached ones from the process cache, the rest embedded
        in one metered pass (and cached when the provider returned a vector)."""
        from app.providers.base import EmbedRequest

        scope = _embedder_cache_scope(self._embedder)
        keys = [f"{scope}:{hashlib.sha256(t.encode()).hexdigest()}" for t in texts]
        vectors: list[list[float] | None] = [_EMBED_CACHE.get(k) for k in keys]
        missing = list(dict.fromkeys(t for t, v in zip(texts, vectors, strict=True) if v is None))
        if missing:

            async def _batch(batch: list[str]) -> list[list[float]]:
                resp = await self._embedder.embed(EmbedRequest(texts=batch))
                return list(resp.embeddings or [])

            if self._meter and tenant_ctx is not None:
                from app.embedding.metering import embed_metered
                from app.providers.embedder_factory import embedder_model_name

                fresh = await embed_metered(
                    missing,
                    _batch,
                    tenant_ctx=tenant_ctx,
                    model=embedder_model_name(self._embedder),
                    controller=self._cost_controller,
                    resolve_controller=self._cost_controller is None,
                    label="capability-search",
                )
            else:
                fresh = await _batch(missing)
            by_text = dict(zip(missing, fresh, strict=False))
            for i, (text, key) in enumerate(zip(texts, keys, strict=True)):
                if vectors[i] is None:
                    vec = list(by_text.get(text) or [])
                    vectors[i] = vec
                    if vec:
                        _EMBED_CACHE[key] = vec
        return [v or [] for v in vectors]

    async def _search_semantic(
        self,
        query: str,
        tools: list[dict[str, Any]],
        *,
        top_k: int,
        threshold: float,
        tenant_ctx: TenantContext | None = None,
    ) -> list[ToolMatch]:
        tool_texts = [f"{t.get('name', '')} {t.get('description', '')}" for t in tools]
        (q_vec,) = await self._embed_cached([query], tenant_ctx=tenant_ctx)
        if not q_vec:
            # Embedder returned nothing useful — fall back to keywords
            return self._search_keyword(query, tools, top_k=top_k, threshold=threshold)
        t_vecs = await self._embed_cached(tool_texts, tenant_ctx=tenant_ctx)

        matches: list[ToolMatch] = []
        for tool, t_vec in zip(tools, t_vecs, strict=False):
            score = self._cosine(q_vec, t_vec)
            if score > threshold:
                matches.append(
                    ToolMatch(
                        server_id=str(tool.get("server_id", "")),
                        tool_name=str(tool.get("name", "")),
                        description=str(tool.get("description", "")),
                        score=score,
                    )
                )

        matches.sort(key=lambda m: m.score, reverse=True)
        return matches[:top_k]
