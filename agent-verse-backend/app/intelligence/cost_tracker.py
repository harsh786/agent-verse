"""Cost tracking, budget enforcement, anomaly detection, and cost prediction.

This module is the single source of truth for real-money cost accounting in
AgentVerse.  All cost writes go through ``CostTracker.record_llm_usage()``
and all budget *reads* go through ``CostTracker.get_budget_status()`` — which
is a **pure read** and never calls Redis INCRBYFLOAT (Amendment 6.4).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Pricing table (USD per 1 M tokens)
# Updated to current 2026-06 pricing. DB-sourced pricing takes precedence
# when CostTracker is wired with a db_factory.
# ---------------------------------------------------------------------------

MODEL_PRICING: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-opus-4":           {"input": 15.0,   "output": 75.0},
    "claude-opus-4-8":         {"input": 15.0,   "output": 75.0},
    "claude-sonnet-4-5":       {"input":  3.0,   "output": 15.0},
    "claude-haiku-3-5":        {"input":  0.80,  "output":  4.0},
    "claude-3-haiku-20240307": {"input":  0.25,  "output":  1.25},
    # OpenAI
    "gpt-4o":                  {"input":  2.5,   "output": 10.0},
    "gpt-4o-mini":             {"input":  0.15,  "output":  0.60},
    "o1-preview":              {"input": 15.0,   "output": 60.0},
    "o1-mini":                 {"input":  3.0,   "output": 12.0},
    "gpt-4-turbo":             {"input": 10.0,   "output": 30.0},
    # Gemini
    "gemini-2.0-flash":        {"input":  0.075, "output":  0.30},
    "gemini-2.0-pro":          {"input":  3.5,   "output": 10.50},
    "gemini-1.5-pro":          {"input":  1.25,  "output":  5.0},
    "gemini-1.5-flash":        {"input":  0.075, "output":  0.30},
}

# Default fallback pricing when model is unknown
_FALLBACK_PRICING: dict[str, float] = {"input": 3.0, "output": 15.0}


def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Return cost in USD for a given model + token counts.

    Uses the in-memory MODEL_PRICING table; falls back to ``_FALLBACK_PRICING``
    when the model is not recognised.  Callers may pass a full model name like
    ``"claude-sonnet-4-5"`` or a partially-qualified name — the lookup tries an
    exact match first, then a prefix scan.
    """
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        # Try prefix match (e.g. "claude-sonnet-4-5-20241022" → "claude-sonnet-4-5")
        for key, val in MODEL_PRICING.items():
            if model.startswith(key) or key.startswith(model):
                pricing = val
                break
    if pricing is None:
        logger.warning("model_pricing_miss", model=model)
        pricing = _FALLBACK_PRICING

    return (
        prompt_tokens * pricing["input"] + completion_tokens * pricing["output"]
    ) / 1_000_000


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CostAnomaly:
    tenant_id: str
    agent_id: str | None
    anomaly_type: str               # 'spike' | 'sustained_high' | 'budget_exceed'
    cost_actual_usd: float
    cost_baseline_usd: float
    sigma_deviation: float
    detected_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class BudgetLimits:
    per_goal_usd: float = 10.0
    per_tenant_daily_usd: float = 500.0


# ---------------------------------------------------------------------------
# CostTracker
# ---------------------------------------------------------------------------


class CostTracker:
    """Central cost-tracking service wired with Redis + DB.

    Redis keys
    ----------
    cost:daily:{tenant_id}:{YYYY-MM-DD}  — daily spend counter (INCRBYFLOAT)
    cost:goal:{goal_id}                  — per-goal spend counter (INCRBYFLOAT)
    cost_ewma:{tenant_id}:{agent_id|tenant}  — EWMA anomaly state (JSON)

    All *reads* use GET (never INCRBYFLOAT) so budget status checks are safe
    to call from prediction endpoints without corrupting counters.
    """

    # EWMA constants for anomaly detection
    _EWMA_ALPHA: float = 0.3
    _SIGMA_THRESHOLD: float = 3.0

    def __init__(
        self,
        redis: Any = None,
        db_factory: Any = None,
    ) -> None:
        self._redis = redis
        self._db = db_factory

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _today() -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d")

    def _daily_key(self, tenant_id: str) -> str:
        return f"cost:daily:{tenant_id}:{self._today()}"

    @staticmethod
    def _goal_key(goal_id: str) -> str:
        return f"cost:goal:{goal_id}"

    @staticmethod
    def _ewma_key(tenant_id: str, agent_id: str | None) -> str:
        return f"cost_ewma:{tenant_id}:{agent_id or 'tenant'}"

    async def _load_budgets(self, tenant_id: str) -> BudgetLimits:
        """Load budget limits from DB; return defaults if unavailable."""
        if self._db is None:
            return BudgetLimits()
        try:
            from sqlalchemy import text as _t
            async with self._db() as session:
                row = (
                    await session.execute(
                        _t(
                            "SELECT per_goal_usd, per_tenant_daily_usd "
                            "FROM budget_configs WHERE tenant_id = :tid"
                        ),
                        {"tid": tenant_id},
                    )
                ).fetchone()
            if row:
                return BudgetLimits(
                    per_goal_usd=float(row[0]),
                    per_tenant_daily_usd=float(row[1]),
                )
        except Exception as exc:
            logger.warning("budget_load_failed", tenant_id=tenant_id, error=str(exc))
        return BudgetLimits()

    # ------------------------------------------------------------------
    # Budget status — READ ONLY (Amendment 6.4)
    # ------------------------------------------------------------------

    async def get_budget_status(
        self, tenant_id: str, goal_id: str | None = None
    ) -> dict[str, Any]:
        """Pure READ operation — never modifies Redis counters.

        This is the safe method to call from prediction endpoints and dashboards.
        It uses GET (not INCRBYFLOAT) so it cannot corrupt TTLs or counters.
        """
        daily_key = self._daily_key(tenant_id)
        goal_key = self._goal_key(goal_id) if goal_id else None

        if self._redis is not None:
            daily_spent = float(await self._redis.get(daily_key) or 0)
            goal_spent = (
                float(await self._redis.get(goal_key) or 0) if goal_key else 0.0
            )
        else:
            daily_spent = 0.0
            goal_spent = 0.0

        budget = await self._load_budgets(tenant_id)
        remaining = max(0.0, budget.per_tenant_daily_usd - daily_spent)

        return {
            "daily_spent": daily_spent,
            "daily_limit": budget.per_tenant_daily_usd,
            "daily_remaining": remaining,
            "budget_pct_remaining": remaining / max(budget.per_tenant_daily_usd, 0.01),
            "goal_spent": goal_spent,
        }

    # ------------------------------------------------------------------
    # Record LLM usage
    # ------------------------------------------------------------------

    async def record_llm_usage(
        self,
        *,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        tenant_ctx: Any,
        goal_id: str,
        agent_id: str | None = None,
        role: str = "executor",
        iteration: int | None = None,
    ) -> float:
        """Record a real LLM call's token usage and compute/persist the cost.

        Returns the cost in USD so the caller can accumulate it on state.
        """
        cost_usd = calculate_cost(model, prompt_tokens, completion_tokens)
        tenant_id: str = (
            tenant_ctx.tenant_id
            if hasattr(tenant_ctx, "tenant_id")
            else str(tenant_ctx)
        )

        # 1. Increment Redis counters (atomic INCRBYFLOAT)
        if self._redis is not None:
            try:
                daily_key = self._daily_key(tenant_id)
                goal_key = self._goal_key(goal_id)
                await self._redis.incrbyfloat(daily_key, cost_usd)
                await self._redis.expire(daily_key, 90_000)  # ~25 h buffer past midnight
                await self._redis.incrbyfloat(goal_key, cost_usd)
                await self._redis.expire(goal_key, 86_400)
            except Exception as exc:
                logger.warning("redis_cost_incr_failed", error=str(exc))

        # 2. Persist to cost_ledger
        if self._db is not None:
            try:
                from sqlalchemy import text as _t
                async with self._db() as session:
                    await session.execute(
                        _t(
                            "INSERT INTO cost_ledger "
                            "(tenant_id, goal_id, agent_id, model, prompt_tokens, "
                            " completion_tokens, cost_usd, cost_type, tags) "
                            "VALUES (:tid, :gid, :aid, :model, :pt, :ct, :cost, 'llm', "
                            "        :tags::jsonb)"
                        ),
                        {
                            "tid": tenant_id,
                            "gid": goal_id,
                            "aid": agent_id,
                            "model": model,
                            "pt": prompt_tokens,
                            "ct": completion_tokens,
                            "cost": cost_usd,
                            "tags": json.dumps(
                                {"role": role, "iteration": iteration}
                            ),
                        },
                    )
                    await session.commit()
            except Exception as exc:
                logger.warning("cost_ledger_write_failed", error=str(exc))

        # 3. Run anomaly detection (fire-and-forget; never blocks the agent)
        try:
            anomaly = await self._check_ewma_anomaly(
                tenant_id=tenant_id,
                agent_id=agent_id,
                cost_usd=cost_usd,
            )
            if anomaly and self._redis is not None:
                await self._redis.publish(
                    f"cost:anomaly:{tenant_id}",
                    json.dumps(
                        {
                            "tenant_id": anomaly.tenant_id,
                            "agent_id": anomaly.agent_id,
                            "anomaly_type": anomaly.anomaly_type,
                            "cost_actual_usd": anomaly.cost_actual_usd,
                            "cost_baseline_usd": anomaly.cost_baseline_usd,
                            "sigma_deviation": anomaly.sigma_deviation,
                            "detected_at": anomaly.detected_at,
                        }
                    ),
                )
        except Exception as exc:
            logger.warning("anomaly_check_failed", error=str(exc))

        logger.info(
            "llm_cost_recorded",
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            tenant_id=tenant_id,
            goal_id=goal_id,
        )
        return cost_usd

    # ------------------------------------------------------------------
    # Anomaly detection — EWMA z-score
    # ------------------------------------------------------------------

    async def _check_ewma_anomaly(
        self,
        tenant_id: str,
        agent_id: str | None,
        cost_usd: float,
    ) -> CostAnomaly | None:
        """Update EWMA state and return a CostAnomaly if a spike is detected."""
        if self._redis is None:
            return None

        key = self._ewma_key(tenant_id, agent_id)
        raw = await self._redis.get(key)

        if raw is None:
            # Initialise EWMA with the first observation; no anomaly yet
            await self._redis.setex(
                key,
                86_400 * 30,
                json.dumps({"mean": cost_usd, "var": 0.0}),
            )
            return None

        state = json.loads(raw)
        mean: float = state["mean"]
        var: float = state["var"]

        delta = cost_usd - mean

        # Compute sigma using the PRIOR variance — before absorbing the new observation.
        # Using the updated variance would attenuate the sigma value for large spikes,
        # causing under-detection.  The prior std represents the established baseline.
        prior_std = math.sqrt(max(var, 1e-10))
        sigma = delta / prior_std if prior_std > 0 else 0.0

        # Update EWMA state
        new_mean = mean + self._EWMA_ALPHA * delta
        new_var = (1 - self._EWMA_ALPHA) * (var + self._EWMA_ALPHA * delta**2)

        # Persist updated state
        await self._redis.setex(
            key,
            86_400 * 30,
            json.dumps({"mean": new_mean, "var": new_var}),
        )

        if sigma > self._SIGMA_THRESHOLD and cost_usd > 0.01:
            return CostAnomaly(
                tenant_id=tenant_id,
                agent_id=agent_id,
                anomaly_type="spike",
                cost_actual_usd=cost_usd,
                cost_baseline_usd=mean,
                sigma_deviation=sigma,
            )
        return None

    async def detect_anomaly(self, tenant_id: str) -> list[CostAnomaly]:
        """Return recent anomalies for a tenant (reads from Redis EWMA state).

        This is a snapshot read — it does not write to any counter.
        Returns an empty list if Redis is unavailable.
        """
        if self._redis is None:
            return []

        anomalies: list[CostAnomaly] = []
        # Scan all EWMA keys for this tenant
        try:
            pattern = f"cost_ewma:{tenant_id}:*"
            cursor = 0
            while True:
                cursor, keys = await self._redis.scan(cursor, match=pattern, count=100)
                for key in keys:
                    raw = await self._redis.get(key)
                    if raw is None:
                        continue
                    state = json.loads(raw)
                    mean: float = state.get("mean", 0.0)
                    var: float = state.get("var", 0.0)
                    std = math.sqrt(max(var, 1e-10))
                    # Flag keys where the baseline itself indicates sustained high cost
                    if mean > 1.0 and std / max(mean, 0.01) > 0.5:
                        parts = key.split(":")
                        agent_id = parts[-1] if parts[-1] != "tenant" else None
                        anomalies.append(
                            CostAnomaly(
                                tenant_id=tenant_id,
                                agent_id=agent_id,
                                anomaly_type="sustained_high",
                                cost_actual_usd=mean,
                                cost_baseline_usd=mean * 0.5,
                                sigma_deviation=std / max(mean, 0.01),
                            )
                        )
                if cursor == 0:
                    break
        except Exception as exc:
            logger.warning("detect_anomaly_scan_failed", error=str(exc))

        return anomalies

    # ------------------------------------------------------------------
    # Cost prediction
    # ------------------------------------------------------------------

    async def predict_cost(
        self,
        *,
        tenant_id: str,
        agent_id: str | None,
        goal_description: str,
        max_iterations: int = 10,
    ) -> dict[str, Any]:
        """Estimate cost before a goal runs.  Pure read — no Redis writes.

        Preference order:
          1. Historical P50/P95 from cost_ledger (if DB available)
          2. Word-count heuristic
        """
        p50_cost: float | None = None
        p95_cost: float | None = None
        basis = "heuristic_estimate"

        if self._db is not None and agent_id:
            try:
                from sqlalchemy import text as _t
                async with self._db() as session:
                    row = (
                        await session.execute(
                            _t("""
                                SELECT
                                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY total_cost) AS p50,
                                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY total_cost) AS p95
                                FROM (
                                    SELECT goal_id, SUM(cost_usd) AS total_cost
                                    FROM cost_ledger
                                    WHERE tenant_id = :tid
                                      AND agent_id  = :aid
                                      AND cost_type = 'llm'
                                      AND created_at >= NOW() - INTERVAL '30 days'
                                    GROUP BY goal_id
                                    LIMIT 100
                                ) sub
                            """),
                            {"tid": tenant_id, "aid": agent_id},
                        )
                    ).fetchone()
                if row and row[0] is not None:
                    p50_cost = float(row[0])
                    p95_cost = float(row[1]) if row[1] is not None else p50_cost * 3.5
                    basis = "agent_historical_average"
            except Exception as exc:
                logger.warning("predict_cost_db_failed", error=str(exc))

        if p50_cost is None:
            # Heuristic: word_count * 1.3 tokens/word * 5 LLM calls per iteration
            word_count = len(goal_description.split())
            estimated_tokens = word_count * 1.3 * 5 * max_iterations
            # Use gpt-4o-mini pricing as conservative estimate
            p50_cost = estimated_tokens * 0.15 / 1_000_000
            p95_cost = p50_cost * 3.5

        # Budget check — READ ONLY (get_budget_status never calls INCRBYFLOAT)
        budget_status = await self.get_budget_status(tenant_id)
        budget_remaining = budget_status["daily_remaining"]

        return {
            "predicted_cost_usd": round(p50_cost, 6),
            "p95_cost_usd": round(p95_cost or p50_cost * 3.5, 6),
            "confidence": "high" if basis == "agent_historical_average" else "low",
            "basis": basis,
            "breakdown": {
                "planning_usd": round(p50_cost * 0.10, 6),
                "execution_usd": round(p50_cost * 0.80, 6),
                "verification_usd": round(p50_cost * 0.10, 6),
            },
            "budget_remaining_usd": round(budget_remaining, 4),
        }

    # ------------------------------------------------------------------
    # Per-agent cost summary
    # ------------------------------------------------------------------

    async def get_per_agent_summary(
        self,
        tenant_id: str,
        days: int = 30,
    ) -> list[dict[str, Any]]:
        """Return per-agent cost breakdown for the given period."""
        if self._db is None:
            return []
        try:
            from sqlalchemy import text as _t
            async with self._db() as session:
                rows = (
                    await session.execute(
                        _t("""
                            SELECT
                                agent_id,
                                SUM(cost_usd)           AS total_cost,
                                SUM(prompt_tokens)      AS total_prompt_tokens,
                                SUM(completion_tokens)  AS total_completion_tokens,
                                COUNT(DISTINCT goal_id) AS goal_count
                            FROM cost_ledger
                            WHERE tenant_id = :tid
                              AND cost_type  = 'llm'
                              AND created_at >= NOW() - INTERVAL ':days days'
                            GROUP BY agent_id
                            ORDER BY total_cost DESC
                        """),
                        {"tid": tenant_id, "days": days},
                    )
                ).fetchall()
            return [
                {
                    "agent_id": str(r[0]) if r[0] else None,
                    "total_cost_usd": float(r[1] or 0),
                    "total_prompt_tokens": int(r[2] or 0),
                    "total_completion_tokens": int(r[3] or 0),
                    "goal_count": int(r[4] or 0),
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("per_agent_summary_failed", error=str(exc))
            return []
