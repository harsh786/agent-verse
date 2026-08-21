"""SemanticCacheBridge — wires CachePolicyEngine to actual SemanticCache.

Rules:
  1. Never cache errors
  2. Never cache non-deterministic
  3. Always tenant-scoped (cache for T1 never serves T2)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CacheBridgeResult:
    content: str
    tenant_id: str
    similarity: float
    is_stale: bool = False


class SemanticCacheBridge:
    def __init__(self, semantic_cache: Any = None) -> None:
        self._cache = semantic_cache
        self._fallback: dict[str, list[dict]] = {}

    async def maybe_store(
        self,
        step_text: str,
        step_output: str,
        tenant_id: str,
        is_error: bool,
        is_nondeterministic: bool,
    ) -> bool:
        if is_error:
            return False
        if is_nondeterministic:
            return False
        if not step_output or not step_output.strip():
            return False
        try:
            if self._cache is not None and hasattr(self._cache, "store_async"):
                await self._cache.store_async(
                    query=step_text, response=step_output, tenant_id=tenant_id
                )
            else:
                self._fallback.setdefault(tenant_id, []).append(
                    {"text": step_text, "output": step_output}
                )
            return True
        except Exception:
            return False

    async def lookup(
        self, step_text: str, tenant_id: str, min_similarity: float = 0.85
    ) -> CacheBridgeResult | None:
        try:
            if self._cache is not None and hasattr(self._cache, "get_similar"):
                hits = await self._cache.get_similar(
                    query=step_text, tenant_id=tenant_id, threshold=min_similarity
                )
                if hits:
                    h = hits[0]
                    return CacheBridgeResult(
                        content=h.get("response", h.get("content", "")),
                        tenant_id=tenant_id,
                        similarity=h.get("similarity", 1.0),
                    )
            else:
                for entry in self._fallback.get(tenant_id, []):
                    if entry["text"] == step_text:
                        return CacheBridgeResult(
                            content=entry["output"], tenant_id=tenant_id, similarity=1.0
                        )
        except Exception:
            pass
        return None
