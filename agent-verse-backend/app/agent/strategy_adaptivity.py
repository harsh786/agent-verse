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

import random
from dataclasses import replace
from typing import Any

from app.agent.execution_strategy import (
    ExecutionStrategy,
    JsonReliability,
    ModelCapabilityProfile,
    PlanMode,
    ToolMode,
    is_seeded,
)

# Observation thresholds with a hysteresis band so a model near the boundary
# does not flap between strategies.
_UPGRADE_THRESHOLD = 0.8  # >= this observed success -> promote to the richer mode
_DOWNGRADE_THRESHOLD = 0.6  # < this -> demote to the safe mode
# Require this many observations before trusting a rate.
_MIN_OBSERVATIONS = 4

# Cold-start exploration: how often to *probe* the richer strategy before enough
# data exists, so capabilities are discovered rather than assumed. Probing is
# safe — a malformed structured plan falls back to sequential parsing, and
# parallel only affects a turn that emits multiple tool calls.
_EXPLORE_RATE_UNKNOWN = 1.0  # unknown model: always probe until we have data
_EXPLORE_RATE_SEEDED_INCAPABLE = 0.1  # seed says "no" — occasional cheap re-check


def refine_strategy(
    base: ExecutionStrategy,
    *,
    structured_ok_rate: float | None = None,
    parallel_ok_rate: float | None = None,
) -> ExecutionStrategy:
    """Refine a strategy from observed reliability rates — learns BOTH directions.

    * observed success ``>= _UPGRADE_THRESHOLD`` promotes to the richer mode
      (SEQUENTIAL->STRUCTURED, SINGLE->PARALLEL) even if the static seed said no;
    * observed success ``< _DOWNGRADE_THRESHOLD`` demotes to the safe mode;
    * rates in the hysteresis band, or ``None`` (unknown / too few samples), leave
      the mode unchanged.

    Observation always wins over the static seed once enough data exists — the
    seed is only a cold-start prior.
    """
    plan_mode = base.plan_mode
    tool_mode = base.tool_mode
    reasons = [base.reason] if base.reason else []

    if structured_ok_rate is not None:
        if structured_ok_rate >= _UPGRADE_THRESHOLD and plan_mode != PlanMode.STRUCTURED:
            plan_mode = PlanMode.STRUCTURED
            reasons.append(f"A:learned-up(structured_ok={structured_ok_rate:.2f})")
        elif structured_ok_rate < _DOWNGRADE_THRESHOLD and plan_mode != PlanMode.SEQUENTIAL:
            plan_mode = PlanMode.SEQUENTIAL
            reasons.append(f"A:learned-down(structured_ok={structured_ok_rate:.2f})")

    if parallel_ok_rate is not None:
        if parallel_ok_rate >= _UPGRADE_THRESHOLD and tool_mode != ToolMode.PARALLEL:
            tool_mode = ToolMode.PARALLEL
            reasons.append(f"B:learned-up(parallel_ok={parallel_ok_rate:.2f})")
        elif parallel_ok_rate < _DOWNGRADE_THRESHOLD and tool_mode != ToolMode.SINGLE:
            tool_mode = ToolMode.SINGLE
            reasons.append(f"B:learned-down(parallel_ok={parallel_ok_rate:.2f})")

    return replace(base, plan_mode=plan_mode, tool_mode=tool_mode, reason="; ".join(reasons))


def _explore_probability(profile: ModelCapabilityProfile, *, already_capable: bool) -> float:
    """How often to probe the richer strategy for a model with no data yet."""
    if already_capable:
        return 0.0  # base is already the richer mode — nothing to discover
    if not is_seeded(profile.model_id):
        return _EXPLORE_RATE_UNKNOWN  # unknown model — learn it
    if profile.json_reliability == JsonReliability.LOW.value:
        return 0.0  # seed is confident it's incapable — don't waste probes
    return _EXPLORE_RATE_SEEDED_INCAPABLE  # seed says no but plausible — re-check rarely


def apply_exploration(
    strategy: ExecutionStrategy,
    *,
    planner_profile: ModelCapabilityProfile,
    executor_profile: ModelCapabilityProfile,
    structured_rate_known: bool,
    parallel_rate_known: bool,
    rng: random.Random | None = None,
) -> ExecutionStrategy:
    """Cold-start exploration: when a mode has no observed data yet, occasionally
    probe the richer strategy so its reliability can be measured. Once data exists
    (``*_rate_known``), the learned rate governs and exploration stops.
    """
    _rng = rng or random
    plan_mode = strategy.plan_mode
    tool_mode = strategy.tool_mode
    reasons = [strategy.reason] if strategy.reason else []

    if plan_mode == PlanMode.SEQUENTIAL and not structured_rate_known:
        p = _explore_probability(planner_profile, already_capable=False)
        if p > 0 and _rng.random() < p:
            plan_mode = PlanMode.STRUCTURED
            reasons.append("A:explore")

    if tool_mode == ToolMode.SINGLE and not parallel_rate_known:
        p = _explore_probability(executor_profile, already_capable=False)
        if p > 0 and _rng.random() < p:
            tool_mode = ToolMode.PARALLEL
            reasons.append("B:explore")

    return replace(strategy, plan_mode=plan_mode, tool_mode=tool_mode, reason="; ".join(reasons))


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
