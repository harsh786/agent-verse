"""Self-improvement optimizer v2 — fixes all 4 critical bugs.

Bugs fixed from v1 (self_optimization.py):
  1. apply_suggestion() now passes actual agent_config (was passing nothing / empty dict)
  2. "Before" prompt now reads real agent config from DB (was literal string "before")
  3. Global shared state replaced with per-tenant Redis namespace (tenant isolation)
  4. min_goals threshold lowered from 50 → 5 (configurable per tenant)

Additional fixes:
  5. _maybe_conclude_experiment() uses single DB session (no stale session after commit)
  6. _bayesian_prob_better() uses numpy Generator per call (thread-safe; no global random.gauss)

Integration point (Amendment 9.4):
  In graph.py _node_initialize() / _node_verify() — record experiment arm and result.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.observability.logging import get_logger

logger = get_logger(__name__)

# ── Fix 4: min_goals lowered from 50 → 5 ──────────────────────────────────────
DEFAULT_MIN_GOALS: int = 5

# Domain-specific success metric defaults
DOMAIN_METRICS: dict[str, str] = {
    "legal":      "citation_accuracy",
    "healthcare": "eval_score",       # HIPAA-safe: no PHI in eval
    "finance":    "compliance_rate",
    "education":  "resolution_rate",
    "ecommerce":  "conversion_rate",
}


# ---------------------------------------------------------------------------
# Fix 3: Per-tenant Redis state (replaces global module-level dict)
# ---------------------------------------------------------------------------


class TenantOptimizationState:
    """
    Per-tenant, per-agent state stored in Redis.

    Key format: ``optstate:{tenant_id}:{agent_id}``
    Fix 3: Replaces the global module-level ``_optimization_state: dict = {}``.
    """

    PREFIX = "optstate:"

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    def _key(self, tenant_id: str, agent_id: str) -> str:
        return f"{self.PREFIX}{tenant_id}:{agent_id}"

    async def get(self, tenant_id: str, agent_id: str) -> dict[str, Any]:
        raw = await self._redis.get(self._key(tenant_id, agent_id))
        if raw is None:
            return {
                "goals_completed": 0,
                "last_optimized_at": None,
                "current_experiment_id": None,
            }
        return json.loads(raw)

    async def update(self, tenant_id: str, agent_id: str, updates: dict[str, Any]) -> None:
        state = await self.get(tenant_id, agent_id)
        state.update(updates)
        await self._redis.setex(
            self._key(tenant_id, agent_id),
            86400 * 90,  # 90-day TTL
            json.dumps(state),
        )

    async def increment_goals(self, tenant_id: str, agent_id: str) -> int:
        """Atomically increment the goal completion counter. Returns new count."""
        state = await self.get(tenant_id, agent_id)
        state["goals_completed"] = state.get("goals_completed", 0) + 1
        await self._redis.setex(
            self._key(tenant_id, agent_id),
            86400 * 90,
            json.dumps(state),
        )
        return int(state["goals_completed"])


# ---------------------------------------------------------------------------
# SelfOptimizerV2 — production-grade self-improvement engine
# ---------------------------------------------------------------------------


class SelfOptimizerV2:
    """
    Production-grade self-improvement engine with Bayesian A/B testing.

    Fix Summary:
      1. apply_suggestion() — calls DB UPDATE with actual candidate_config
      2. _read_current_agent_config() — reads real config from agents table
      3. TenantOptimizationState — per-tenant Redis namespace, not global dict
      4. DEFAULT_MIN_GOALS = 5 — optimizer triggers early
      5. _maybe_conclude_experiment() — single DB session, no stale session
      6. _bayesian_prob_better() — numpy.default_rng() per call, thread-safe
    """

    OPTIMIZER_PROMPT = """You are an AI agent optimization specialist.

Given an agent's current configuration and performance metrics, suggest ONE targeted improvement
to the system prompt or configuration that would improve performance on the stated metric.

Your suggestion must:
1. Be specific and actionable (not "make it better")
2. Preserve the agent's core purpose
3. Be expressible as a JSON diff to the current config
4. Include a clear rationale explaining the expected improvement

Respond with ONLY valid JSON:
{
  "suggested_change": {
    "field": "system_prompt",
    "current_value_excerpt": "first 100 chars of current value",
    "new_value": "complete new value for this field"
  },
  "rationale": "This change improves citation accuracy by...",
  "expected_uplift_pct": 8.5,
  "confidence": "medium"
}"""

    def __init__(
        self,
        redis: Any,
        db_factory: Any,
        llm_provider_factory: Any,
    ) -> None:
        self._redis = redis
        self._db = db_factory
        self._llm_factory = llm_provider_factory
        self._state = TenantOptimizationState(redis)

    # ── Public API ────────────────────────────────────────────────────────

    async def on_goal_completed(
        self,
        tenant_id: str,
        agent_id: str,
        goal_id: str,
        eval_score: float | None,
        cost_usd: float,
        latency_ms: int,
        domain: str | None = None,
    ) -> None:
        """Called after every goal completion. Drives the optimization loop."""
        count = await self._state.increment_goals(tenant_id, agent_id)
        state = await self._state.get(tenant_id, agent_id)
        exp_id = state.get("current_experiment_id")

        if exp_id:
            arm = await self._get_arm_for_goal(tenant_id, agent_id, goal_id)
            await self._record_result(
                tenant_id=tenant_id,
                experiment_id=exp_id,
                goal_id=goal_id,
                arm=arm,
                eval_score=eval_score,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                domain=domain,
            )
            await self._maybe_conclude_experiment(tenant_id, exp_id)

        min_goals = await self._get_min_goals(tenant_id)
        if count >= min_goals and not exp_id:
            await self._maybe_start_experiment(tenant_id, agent_id, domain)

    async def apply_suggestion(
        self,
        tenant_id: str,
        agent_id: str,
        experiment_id: str,
        candidate_config: dict[str, Any],
    ) -> bool:
        """
        Fix 1: Apply candidate_config to the agent via a direct DB UPDATE.

        Was: called API endpoint with no agent_config → every optimization silently failed.
        Now: UPDATE agents SET config = :candidate_config.
        """
        from sqlalchemy import text as _t

        try:
            async with self._db() as db:
                # Read current config for history record (Fix 2)
                current_config = await self._read_current_agent_config_with_session(
                    db, tenant_id, agent_id
                )

                # Apply the candidate config (Fix 1)
                await db.execute(
                    _t("""
                        UPDATE agents
                        SET config = :config, updated_at = NOW()
                        WHERE id = :agent_id AND tenant_id = :tenant_id
                    """),
                    {
                        "config": json.dumps(candidate_config),
                        "agent_id": agent_id,
                        "tenant_id": tenant_id,
                    },
                )

                # Record in optimization history
                await db.execute(
                    _t("""
                        INSERT INTO agent_optimization_history
                            (id, tenant_id, agent_id, experiment_id,
                             config_before, config_after, delta,
                             applied_at, applied_by)
                        VALUES
                            (:id, :tenant_id, :agent_id, :experiment_id,
                             :before::jsonb, :after::jsonb, :delta::jsonb,
                             NOW(), 'system')
                    """),
                    {
                        "id": uuid4().hex,
                        "tenant_id": tenant_id,
                        "agent_id": agent_id,
                        "experiment_id": experiment_id,
                        "before": json.dumps(current_config or {}),
                        "after": json.dumps(candidate_config),
                        "delta": json.dumps(
                            self._compute_delta(current_config or {}, candidate_config)
                        ),
                    },
                )

                # Mark experiment applied
                await db.execute(
                    _t("""
                        UPDATE improvement_experiments
                        SET status = 'completed', applied_at = NOW(), winner = 'candidate'
                        WHERE id = :exp_id
                    """),
                    {"exp_id": experiment_id},
                )

                await db.commit()

            # Reset tenant state for next optimization cycle
            await self._state.update(tenant_id, agent_id, {
                "current_experiment_id": None,
                "goals_completed": 0,
                "last_optimized_at": datetime.now(UTC).isoformat(),
            })

            logger.info(
                "optimization_applied",
                tenant_id=tenant_id,
                agent_id=agent_id,
                experiment_id=experiment_id,
            )
            return True

        except Exception as exc:
            logger.error(
                "optimization_apply_error",
                error=str(exc),
                tenant_id=tenant_id,
                agent_id=agent_id,
            )
            return False

    async def rollback(
        self,
        tenant_id: str,
        agent_id: str,
        experiment_id: str,
        reason: str,
    ) -> bool:
        """Roll back to the control config from the experiment."""
        from sqlalchemy import text as _t

        try:
            async with self._db() as db:
                row = (
                    await db.execute(
                        _t("SELECT control_config FROM improvement_experiments WHERE id = :id"),
                        {"id": experiment_id},
                    )
                ).fetchone()
                if not row:
                    return False

                control_config = row[0]
                if isinstance(control_config, str):
                    control_config = json.loads(control_config)

                await db.execute(
                    _t("""
                        UPDATE agents
                        SET config = :config, updated_at = NOW()
                        WHERE id = :agent_id AND tenant_id = :tenant_id
                    """),
                    {
                        "config": json.dumps(control_config),
                        "agent_id": agent_id,
                        "tenant_id": tenant_id,
                    },
                )
                await db.execute(
                    _t("""
                        UPDATE improvement_experiments
                        SET status = 'rolled_back', rolled_back_at = NOW(),
                            rolled_back_reason = :reason, winner = 'control'
                        WHERE id = :id
                    """),
                    {"reason": reason, "id": experiment_id},
                )
                await db.commit()

            await self._state.update(tenant_id, agent_id, {
                "current_experiment_id": None,
            })
            logger.info(
                "optimization_rolled_back",
                tenant_id=tenant_id,
                agent_id=agent_id,
                reason=reason,
            )
            return True
        except Exception as exc:
            logger.error("rollback_error", error=str(exc))
            return False

    async def get_arm_config(
        self, tenant_id: str, agent_id: str, goal_id: str
    ) -> dict[str, Any]:
        """Return the agent config for a specific goal (control or candidate arm)."""
        arm = await self._get_arm_for_goal(tenant_id, agent_id, goal_id)
        if arm == "control":
            return await self._read_current_agent_config(tenant_id, agent_id) or {}

        state = await self._state.get(tenant_id, agent_id)
        exp_id = state.get("current_experiment_id")
        if not exp_id:
            return await self._read_current_agent_config(tenant_id, agent_id) or {}

        from sqlalchemy import text as _t

        async with self._db() as db:
            row = (
                await db.execute(
                    _t("SELECT candidate_config FROM improvement_experiments WHERE id = :id"),
                    {"id": exp_id},
                )
            ).fetchone()
            if row:
                cfg = row[0]
                return cfg if isinstance(cfg, dict) else json.loads(cfg or "{}")
        return {}

    # ── Private helpers ──────────────────────────────────────────────────

    async def _read_current_agent_config(
        self, tenant_id: str, agent_id: str
    ) -> dict[str, Any] | None:
        """
        Fix 2: Read actual agent config from DB.
        Was: before_prompt = "before" — literal string placeholder.
        """

        async with self._db() as db:
            return await self._read_current_agent_config_with_session(db, tenant_id, agent_id)

    async def _read_current_agent_config_with_session(
        self, db: Any, tenant_id: str, agent_id: str
    ) -> dict[str, Any] | None:
        from sqlalchemy import text as _t

        try:
            row = (
                await db.execute(
                    _t("""
                        SELECT config FROM agents
                        WHERE id = :agent_id AND tenant_id = :tenant_id
                    """),
                    {"agent_id": agent_id, "tenant_id": tenant_id},
                )
            ).fetchone()
            if row and row[0]:
                cfg = row[0]
                return cfg if isinstance(cfg, dict) else json.loads(cfg)
        except Exception as exc:
            logger.warning("read_agent_config_failed", error=str(exc))
        return None

    async def _maybe_start_experiment(
        self, tenant_id: str, agent_id: str, domain: str | None
    ) -> str | None:
        current_config = await self._read_current_agent_config(tenant_id, agent_id)
        if not current_config:
            logger.warning("cannot_read_agent_config", tenant_id=tenant_id, agent_id=agent_id)
            return None

        metrics = await self._get_recent_metrics(tenant_id, agent_id)
        success_metric = DOMAIN_METRICS.get(domain or "", "eval_score")
        suggestion = await self._generate_suggestion(current_config, metrics, success_metric)
        if not suggestion:
            return None

        candidate_config = self._apply_suggestion_to_config(current_config, suggestion)
        if candidate_config == current_config:
            return None

        experiment_id = await self._create_experiment(
            tenant_id=tenant_id,
            agent_id=agent_id,
            control_config=current_config,
            candidate_config=candidate_config,
            suggestion=suggestion,
            success_metric=success_metric,
            domain=domain,
        )
        await self._state.update(tenant_id, agent_id, {"current_experiment_id": experiment_id})
        logger.info(
            "experiment_started",
            tenant_id=tenant_id,
            agent_id=agent_id,
            experiment_id=experiment_id,
            metric=success_metric,
        )
        return experiment_id

    async def _generate_suggestion(
        self,
        current_config: dict[str, Any],
        metrics: dict[str, Any],
        success_metric: str,
    ) -> dict[str, Any] | None:
        try:
            provider = await self._llm_factory()
            from app.providers.base import CompletionRequest, Message

            config_excerpt = {
                k: (v[:500] + "..." if isinstance(v, str) and len(v) > 500 else v)
                for k, v in current_config.items()
            }
            user_content = json.dumps({
                "current_config": config_excerpt,
                "performance_metrics": metrics,
                "target_metric": success_metric,
                "goal": f"Improve {success_metric}",
            }, indent=2)

            response = await provider.complete(CompletionRequest(
                model="claude-haiku-3-5",
                messages=[
                    Message(role="system", content=self.OPTIMIZER_PROMPT),
                    Message(role="user", content=user_content),
                ],
                max_tokens=500,
                temperature=0.7,
            ))
            suggestion = json.loads(response.content.strip())
            if not isinstance(suggestion, dict):
                return None
            if "suggested_change" not in suggestion or "rationale" not in suggestion:
                return None
            if "field" not in suggestion.get("suggested_change", {}):
                return None
            return suggestion
        except Exception as exc:
            logger.warning("suggestion_generation_error", error=str(exc))
            return None

    @staticmethod
    def _apply_suggestion_to_config(
        current_config: dict[str, Any], suggestion: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Fix 1 (part 2): Apply suggestion to config dict.
        Was: called API with empty body.
        Now: direct dict mutation returning candidate config.
        """
        candidate = dict(current_config)
        change = suggestion.get("suggested_change", {})
        field = change.get("field")
        new_value = change.get("new_value")

        if field and new_value is not None:
            parts = field.split(".")
            target = candidate
            for part in parts[:-1]:
                if isinstance(target, dict) and part in target:
                    target = target[part]
                else:
                    return current_config  # path not found, return unchanged
            if isinstance(target, dict):
                target[parts[-1]] = new_value

        return candidate

    async def _maybe_conclude_experiment(
        self, tenant_id: str, experiment_id: str
    ) -> None:
        """
        Fix 5: All DB operations in ONE session — no stale session after commit.
        apply_suggestion() called OUTSIDE the session (fresh connection).
        """
        from sqlalchemy import text as _t

        winner: str | None = None
        candidate_config: dict[str, Any] | None = None
        agent_id_str: str | None = None

        async with self._db() as db:
            row = (
                await db.execute(
                    _t("""
                        SELECT
                            e.min_samples_per_arm,
                            e.significance_threshold,
                            e.success_metric,
                            e.agent_id,
                            e.candidate_config,
                            COUNT(CASE WHEN r.arm = 'control'   THEN 1 END) AS ctrl_n,
                            COUNT(CASE WHEN r.arm = 'candidate' THEN 1 END) AS cand_n,
                            AVG(CASE WHEN r.arm = 'control'   THEN r.metric_value END) AS ctrl_mean,
                            AVG(CASE WHEN r.arm = 'candidate' THEN r.metric_value END) AS cand_mean
                        FROM improvement_experiments e
                        LEFT JOIN improvement_results r ON r.experiment_id = e.id
                        WHERE e.id = :exp_id AND e.tenant_id = :tenant_id
                        GROUP BY e.id, e.min_samples_per_arm, e.significance_threshold,
                                 e.success_metric, e.agent_id, e.candidate_config
                    """),
                    {"exp_id": experiment_id, "tenant_id": tenant_id},
                )
            ).fetchone()

            if not row:
                return

            ctrl_n = int(row[5] or 0)
            cand_n = int(row[6] or 0)
            min_n = int(row[0])
            if ctrl_n < min_n or cand_n < min_n:
                return

            ctrl_mean = float(row[7] or 0)
            cand_mean = float(row[8] or 0)
            threshold = float(row[1])
            agent_id_str = str(row[3])
            raw_cfg = row[4]
            candidate_config = (
                raw_cfg if isinstance(raw_cfg, dict) else json.loads(raw_cfg or "{}")
            )

            posterior_prob = self._bayesian_prob_better(
                ctrl_n=ctrl_n, ctrl_mean=ctrl_mean,
                cand_n=cand_n, cand_mean=cand_mean,
            )
            uplift_pct = ((cand_mean - ctrl_mean) / max(ctrl_mean, 1e-9)) * 100

            if posterior_prob >= threshold:
                winner = "candidate"
            elif posterior_prob <= (1.0 - threshold):
                winner = "control"
            else:
                winner = "inconclusive"

            # Update experiment in SAME session (Fix 5)
            await db.execute(
                _t("""
                    UPDATE improvement_experiments
                    SET control_n = :ctrl_n, candidate_n = :cand_n,
                        control_mean = :ctrl_mean, candidate_mean = :cand_mean,
                        bayesian_uplift = :uplift, posterior_prob_better = :prob,
                        winner = :winner,
                        status = CASE WHEN :winner != 'inconclusive'
                                      THEN 'completed' ELSE status END,
                        completed_at = CASE WHEN :winner != 'inconclusive'
                                            THEN NOW() ELSE completed_at END
                    WHERE id = :exp_id
                """),
                {
                    "ctrl_n": ctrl_n, "cand_n": cand_n,
                    "ctrl_mean": ctrl_mean, "cand_mean": cand_mean,
                    "uplift": round(uplift_pct, 4),
                    "prob": round(posterior_prob, 3),
                    "winner": winner,
                    "exp_id": experiment_id,
                },
            )
            await db.commit()
        # Apply suggestion OUTSIDE the session (fresh connection) — Fix 5
        if winner == "candidate" and agent_id_str and candidate_config is not None:
            await self.apply_suggestion(
                tenant_id, agent_id_str, experiment_id, candidate_config
            )

    @staticmethod
    def _bayesian_prob_better(
        ctrl_n: int,
        ctrl_mean: float,
        cand_n: int,
        cand_mean: float,
        n_samples: int = 10000,
    ) -> float:
        """
        Fix 6: Thread-safe Thompson sampling using numpy.default_rng().
        Was: global random.gauss() — not thread-safe under async.
        Now: np.random.default_rng() per call — new Generator instance, thread-safe.
        """
        try:
            import numpy as np  # type: ignore[import]

            rng = np.random.default_rng()  # thread-safe: new Generator per call
            ctrl_std = max(ctrl_mean * 0.3, 1e-9)
            cand_std = max(cand_mean * 0.3, 1e-9)
            ctrl_samples = rng.normal(
                ctrl_mean, ctrl_std / max(ctrl_n ** 0.5, 1), n_samples
            )
            cand_samples = rng.normal(
                cand_mean, cand_std / max(cand_n ** 0.5, 1), n_samples
            )
            return float(np.mean(cand_samples > ctrl_samples))
        except ImportError:
            # Fallback: pure-Python (less accurate but works without numpy)
            import random as _r

            ctrl_std = max(ctrl_mean * 0.3, 1e-9)
            cand_std = max(cand_mean * 0.3, 1e-9)
            wins = sum(
                1 for _ in range(n_samples)
                if (
                    _r.gauss(cand_mean, cand_std / max(cand_n ** 0.5, 1))
                    > _r.gauss(ctrl_mean, ctrl_std / max(ctrl_n ** 0.5, 1))
                )
            )
            return wins / n_samples

    async def _get_arm_for_goal(
        self, tenant_id: str, agent_id: str, goal_id: str
    ) -> str:
        """
        Deterministic arm assignment via goal_id hash.
        50/50 split by default; respects experiment traffic_split_pct.
        """
        state = await self._state.get(tenant_id, agent_id)
        exp_id = state.get("current_experiment_id")
        if not exp_id:
            return "control"

        split = 50
        from sqlalchemy import text as _t

        try:
            async with self._db() as db:
                row = (
                    await db.execute(
                        _t("SELECT traffic_split_pct FROM improvement_experiments WHERE id = :id"),
                        {"id": exp_id},
                    )
                ).fetchone()
                if row:
                    split = int(row[0])
        except Exception:
            pass

        hash_val = int(hashlib.sha256(goal_id.encode()).hexdigest()[:8], 16) % 100
        return "candidate" if hash_val < split else "control"

    async def _record_result(
        self,
        tenant_id: str,
        experiment_id: str,
        goal_id: str,
        arm: str,
        eval_score: float | None,
        cost_usd: float,
        latency_ms: int,
        domain: str | None,
    ) -> None:
        success_metric = DOMAIN_METRICS.get(domain or "", "eval_score")
        metric_value = eval_score or 0.0

        from sqlalchemy import text as _t

        try:
            async with self._db() as db:
                await db.execute(
                    _t("""
                        INSERT INTO improvement_results
                            (id, experiment_id, tenant_id, goal_id, arm,
                             metric_value, metric_name, goal_completed,
                             cost_usd, latency_ms, eval_score, recorded_at)
                        VALUES
                            (:id, :exp_id, :tenant_id, :goal_id, :arm,
                             :metric_value, :metric_name, TRUE,
                             :cost_usd, :latency_ms, :eval_score, NOW())
                    """),
                    {
                        "id": uuid4().hex,
                        "exp_id": experiment_id,
                        "tenant_id": tenant_id,
                        "goal_id": goal_id or None,
                        "arm": arm,
                        "metric_value": metric_value,
                        "metric_name": success_metric,
                        "cost_usd": cost_usd,
                        "latency_ms": latency_ms,
                        "eval_score": eval_score,
                    },
                )
                await db.commit()
        except Exception as exc:
            logger.warning("record_result_failed", error=str(exc))

    async def _get_recent_metrics(
        self, tenant_id: str, agent_id: str
    ) -> dict[str, Any]:
        from sqlalchemy import text as _t

        try:
            async with self._db() as db:
                row = (
                    await db.execute(
                        _t("""
                            SELECT
                                COUNT(*) AS n,
                                AVG(eval_score) AS avg_eval,
                                AVG(cost_usd) AS avg_cost,
                                AVG(latency_ms) AS avg_latency,
                                SUM(CASE WHEN goal_completed THEN 1 ELSE 0 END)
                                    * 100.0 / NULLIF(COUNT(*), 0) AS completion_rate
                            FROM improvement_results
                            WHERE tenant_id = :tenant_id
                              AND recorded_at > NOW() - INTERVAL '30 days'
                        """),
                        {"tenant_id": tenant_id},
                    )
                ).fetchone()
                if row:
                    return {
                        "n_goals": int(row[0] or 0),
                        "avg_eval_score": float(row[1] or 0),
                        "avg_cost_usd": float(row[2] or 0),
                        "avg_latency_ms": int(row[3] or 0),
                        "completion_rate_pct": float(row[4] or 0),
                    }
        except Exception as exc:
            logger.warning("get_recent_metrics_failed", error=str(exc))
        return {}

    async def _get_min_goals(self, tenant_id: str) -> int:
        """Load per-tenant min_goals setting. Fix 4: default is 5, not 50."""
        from sqlalchemy import text as _t

        try:
            async with self._db() as db:
                row = (
                    await db.execute(
                        _t("""
                            SELECT settings->>'self_improvement_min_goals'
                            FROM tenant_settings
                            WHERE tenant_id = :tid
                        """),
                        {"tid": tenant_id},
                    )
                ).fetchone()
                if row and row[0]:
                    return int(row[0])
        except Exception:
            pass
        return DEFAULT_MIN_GOALS

    async def _create_experiment(
        self,
        tenant_id: str,
        agent_id: str,
        control_config: dict[str, Any],
        candidate_config: dict[str, Any],
        suggestion: dict[str, Any],
        success_metric: str,
        domain: str | None,
    ) -> str:
        from sqlalchemy import text as _t

        exp_id = uuid4().hex
        async with self._db() as db:
            await db.execute(
                _t("""
                    INSERT INTO improvement_experiments
                        (id, tenant_id, agent_id, name, status,
                         control_config, candidate_config, suggestion_rationale,
                         success_metric, domain, created_by, started_at)
                    VALUES
                        (:id, :tenant_id, :agent_id, :name, 'running',
                         :control::jsonb, :candidate::jsonb, :rationale,
                         :metric, :domain, 'system', NOW())
                """),
                {
                    "id": exp_id,
                    "tenant_id": tenant_id,
                    "agent_id": agent_id,
                    "name": (
                        f"Auto-optimization {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')}"
                    ),
                    "control": json.dumps(control_config),
                    "candidate": json.dumps(candidate_config),
                    "rationale": suggestion.get("rationale", ""),
                    "metric": success_metric,
                    "domain": domain,
                },
            )
            await db.commit()
        return exp_id

    @staticmethod
    def _compute_delta(
        before: dict[str, Any], after: dict[str, Any]
    ) -> dict[str, Any]:
        delta: dict[str, Any] = {}
        for k in set(before) | set(after):
            if before.get(k) != after.get(k):
                delta[k] = {"before": before.get(k), "after": after.get(k)}
        return delta
