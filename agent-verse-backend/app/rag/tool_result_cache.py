"""Tool-result cache — reuse read-only tool outputs within a TTL (Phase 5, T5.2).

Distinct from the semantic LLM cache: this keys on the *exact* (tenant, tool,
arguments) tuple and caches only READ-ONLY tool results, so an agent that lists
the same Jira board twice in a run doesn't pay for two identical calls.

Hard safety rules (so the cache can never manufacture a fake success):
* only ``read_only=True`` calls are cached;
* error / "requires approval" / empty / INSUFFICIENT-DATA results are never cached
  (mirrors SemanticCache's uncacheable-output guard);
* entries expire after ``ttl_seconds``; a write to a tool can ``invalidate`` it.
Best-effort: any backend error degrades to a miss, never raises.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from collections import OrderedDict
from typing import Any

_NS = "toolcache"


def _key(tenant_id: str, tool_name: str, arguments: dict[str, Any]) -> str:
    try:
        args = json.dumps(arguments, sort_keys=True, default=str)
    except Exception:
        args = str(sorted(arguments.items())) if arguments else ""
    digest = hashlib.sha256(f"{tenant_id}\x00{tool_name}\x00{args}".encode()).hexdigest()
    return f"{_NS}:{tenant_id}:{tool_name}:{digest}"


def is_uncacheable_result(result: Any) -> bool:
    """True when a tool result must NOT be cached (error/empty/approval/insufficient)."""
    if result is None:
        return True
    if isinstance(result, str):
        low = result.strip().lower()
        if not low:
            return True
        markers = ("error", "exception", "traceback", "requires approval", "insufficient data")
        return any(m in low for m in markers)
    if isinstance(result, dict):
        if not result:
            return True
        if result.get("error") or result.get("success") is False:
            return True
        # "no rows" style empties
        if result.get("total") == 0 or result.get("count") == 0:
            return True
        # Only treat an explicitly-present, empty ``content`` as uncacheable — a
        # dict WITHOUT a content key (e.g. {"total": 3}) is a real result.
        return "content" in result and result["content"] in ([], "", None)
    if isinstance(result, (list, tuple, set)):
        return len(result) == 0
    return False


class ToolResultCache:
    def __init__(
        self,
        redis: Any | None = None,
        *,
        ttl_seconds: float = 300.0,
        max_size: int = 1024,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        self._redis = redis
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._l1: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def _now(self) -> float:
        return time.monotonic()

    def _l1_get(self, key: str) -> Any | None:
        entry = self._l1.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= self._now():
            self._l1.pop(key, None)  # expired
            return None
        self._l1.move_to_end(key)
        return value

    def _l1_put(self, key: str, value: Any) -> None:
        self._l1[key] = (self._now() + self._ttl, value)
        self._l1.move_to_end(key)
        while len(self._l1) > self._max_size:
            self._l1.popitem(last=False)

    async def get(
        self, tenant_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> Any | None:
        key = _key(tenant_id, tool_name, arguments)
        hit = self._l1_get(key)
        if hit is not None:
            return hit
        if self._redis is not None:
            try:
                blob = await self._redis.get(key)
            except Exception:
                return None
            if blob:
                with contextlib.suppress(Exception):
                    value = json.loads(blob)
                    self._l1_put(key, value)
                    return value
        return None

    async def set(
        self,
        tenant_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: Any,
        *,
        read_only: bool,
    ) -> bool:
        """Cache a tool result. Returns True if stored, False if refused.

        Refuses non-read-only calls and uncacheable results.
        """
        if not read_only or is_uncacheable_result(result):
            return False
        key = _key(tenant_id, tool_name, arguments)
        self._l1_put(key, result)
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.set(key, json.dumps(result, default=str), ex=int(self._ttl))
        return True

    def invalidate(self, tenant_id: str, tool_name: str) -> int:
        """Drop all L1 entries for a (tenant, tool) — call after a write to it."""
        prefix = f"{_NS}:{tenant_id}:{tool_name}:"
        stale = [k for k in self._l1 if k.startswith(prefix)]
        for k in stale:
            self._l1.pop(k, None)
        return len(stale)

    def __len__(self) -> int:
        return len(self._l1)
