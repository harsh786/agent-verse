"""Adaptivity for the execution-strategy engine (P5).

Refines a statically-resolved :class:`ExecutionStrategy` using *observed*
behavior per (tenant, model): if a model's structured-plan JSON keeps failing
to parse, downgrade it to SEQUENTIAL; when it recovers, allow STRUCTURED again.
Same idea for parallel tool calls.

``refine_strategy`` is pure (observed rates are passed in) so it is trivially
testable. The async :class:`RedisCapabilityTracker` records outcomes and reports
rolling rates; when no rates are known the base strategy is returned unchanged,
so the static resolver behavior is preserved.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.agent.execution_strategy import ExecutionStrategy, PlanMode, ToolMode

# Below this observed success rate, force the safe mode even if the static
# profile allowed the richer strategy.
_DOWNGRADE_THRESHOLD = 0.6
# Require this many observations before trusting a rate.
_MIN_OBSERVATIONS = 4


def refine_strategy(
    base: ExecutionStrategy,
    *,
    structured_ok_rate: float | None = None,
    parallel_ok_rate: float | None = None,
) -> ExecutionStrategy:
    """Return a possibly-downgraded strategy from observed reliability rates.

    Only ever *downgrades* (STRUCTURED->SEQUENTIAL, PARALLEL->SINGLE) — it never
    upgrades beyond what the static resolver allowed. ``None`` rates (unknown /
    too few samples) leave the corresponding mode unchanged.
    """
    updated = base
    if (
        updated.plan_mode == PlanMode.STRUCTURED
        and structured_ok_rate is not None
        and structured_ok_rate < _DOWNGRADE_THRESHOLD
    ):
        updated = replace(
            updated,
            plan_mode=PlanMode.SEQUENTIAL,
            reason=f"{updated.reason}; A:downgraded(structured_ok={structured_ok_rate:.2f})",
        )
    if (
        updated.tool_mode == ToolMode.PARALLEL
        and parallel_ok_rate is not None
        and parallel_ok_rate < _DOWNGRADE_THRESHOLD
    ):
        updated = replace(
            updated,
            tool_mode=ToolMode.SINGLE,
            reason=f"{updated.reason}; B:downgraded(parallel_ok={parallel_ok_rate:.2f})",
        )
    return updated


class RedisCapabilityTracker:
    """Redis-backed rolling success-rate tracker per (tenant, model, kind).

    Stores two hash counters per key — total and ok — and reports ok/total once
    at least ``_MIN_OBSERVATIONS`` samples exist. Fully guarded: any Redis error
    yields "unknown" (None) so the caller keeps the static strategy.
    """

    def __init__(self, redis: Any, *, key_prefix: str = "model:caps") -> None:
        self._redis = redis
        self._prefix = key_prefix

    def _key(self, model_id: str, tenant_id: str | None, kind: str) -> str:
        tid = tenant_id or "global"
        safe_model = (model_id or "unknown").replace(" ", "_")
        return f"{self._prefix}:{tid}:{safe_model}:{kind}"

    async def record(
        self,
        model_id: str,
        *,
        ok: bool,
        tenant_id: str | None = None,
        kind: str = "structured",
    ) -> None:
        key = self._key(model_id, tenant_id, kind)
        try:
            await self._redis.hincrby(key, "total", 1)
            if ok:
                await self._redis.hincrby(key, "ok", 1)
            await self._redis.expire(key, 7 * 24 * 3600)
        except Exception:  # pragma: no cover - defensive
            return

    async def rate(
        self, model_id: str, *, tenant_id: str | None = None, kind: str = "structured"
    ) -> float | None:
        key = self._key(model_id, tenant_id, kind)
        try:
            data = await self._redis.hgetall(key)
        except Exception:  # pragma: no cover - defensive
            return None
        if not data:
            return None

        def _num(v: Any) -> int:
            try:
                return int(v)
            except (TypeError, ValueError):
                return 0

        total = _num(data.get("total", data.get(b"total", 0)))
        ok = _num(data.get("ok", data.get(b"ok", 0)))
        if total < _MIN_OBSERVATIONS:
            return None
        return ok / total
