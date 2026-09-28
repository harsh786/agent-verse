"""A/B experiment arm assignment and per-tenant result tracking.

Results are kept in memory per ``(tenant_id, experiment_type)`` and persisted to
``ab_test_results`` (FORCE ROW LEVEL SECURITY, TEXT ``tenant_id``). Every DB
statement runs inside a transaction carrying the tenant GUC
(``sqlalchemy_rls_context``) plus an explicit ``tenant_id`` predicate.

There is deliberately **no** cross-tenant startup hydration: a tenant's history is
loaded lazily, once per process, the first time that tenant records a result
(:meth:`ABTestingEngine.record_result_async`). The previous startup scan read
every tenant's rows into one shared pool — which mixed tenants' statistics, and
under the NOBYPASSRLS production role could only ever see zero rows anyway.
"""

from __future__ import annotations

import enum
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from app.db.rls import sqlalchemy_rls_context

_log = logging.getLogger(__name__)


class ExperimentType(enum.StrEnum):
    PLANNER_PROMPT = "planner_prompt"
    EXECUTOR_PROMPT = "executor_prompt"
    VERIFIER_PROMPT = "verifier_prompt"
    MODEL_ROUTING = "model_routing"
    RAG_STRATEGY = "rag_strategy"


_ARMS = ["control", "variant_a", "variant_b"]


@dataclass
class ExperimentArm:
    arm_id: str
    config: dict = field(default_factory=dict)


class ABTestingEngine:
    def __init__(self, db_factory: Any = None) -> None:
        # {(tenant_id, experiment_type): [{"goal_id", "arm_id", "score"}, ...]}
        self._results: dict[tuple[str, str], list[dict]] = {}
        self._db_factory = db_factory
        # Tenants whose DB history has been loaded into this process.
        self._hydrated_tenants: set[str] = set()

    def get_experiment_arm(self, goal_id: str, experiment_type: ExperimentType) -> ExperimentArm:
        h = int(
            hashlib.md5(f"{goal_id}:{experiment_type.value}".encode()).hexdigest(),
            16,
        )
        arm_id = _ARMS[h % len(_ARMS)]
        return ExperimentArm(arm_id=arm_id, config={"arm": arm_id})

    def record_result(
        self,
        goal_id: str,
        experiment_type: ExperimentType,
        arm_id: str,
        score: float,
        tenant_id: str = "",
    ) -> None:
        key = (tenant_id, experiment_type.value)
        self._results.setdefault(key, []).append(
            {"goal_id": goal_id, "arm_id": arm_id, "score": score}
        )

    def get_arm_stats(
        self, experiment_type: ExperimentType, arm_id: str, tenant_id: str = ""
    ) -> dict:
        key = (tenant_id, experiment_type.value)
        results = [r for r in self._results.get(key, []) if r["arm_id"] == arm_id]
        if not results:
            return {"call_count": 0, "avg_score": 0.0}
        avg = sum(r["score"] for r in results) / len(results)
        return {"call_count": len(results), "avg_score": round(avg, 3)}

    def can_promote_variant(
        self,
        experiment_type: ExperimentType,
        arm_id: str,
        min_score_threshold: float,
        current_score: float,
        tenant_id: str = "",
    ) -> bool:
        stats = self.get_arm_stats(experiment_type, arm_id, tenant_id=tenant_id)
        if stats["call_count"] < 5:
            return current_score >= min_score_threshold
        return stats["avg_score"] >= min_score_threshold

    async def record_result_async(
        self,
        goal_id: str,
        experiment_type: ExperimentType,
        arm_id: str,
        score: float,
        tenant_id: str = "",
    ) -> None:
        """Record a result in memory AND persist it to ``ab_test_results``.

        Runs on the goal path for one tenant, so the INSERT is tenant-scoped (RLS
        GUC + explicit ``tenant_id``). Without a tenant nothing is persisted: a
        tenant-less row can be neither isolated nor attributed (the old
        ``"unknown"`` default also violated the ``tenants`` foreign key).
        """
        if self._db_factory is not None and tenant_id:
            # Lazy, per-tenant hydration BEFORE the new row is written so the
            # just-inserted result is not counted twice.
            await self._ensure_tenant_loaded(tenant_id)
        self.record_result(goal_id, experiment_type, arm_id, score, tenant_id=tenant_id)
        if self._db_factory is None or not tenant_id:
            return
        try:
            import uuid

            from sqlalchemy import text

            async with (
                self._db_factory() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    text("""
                        INSERT INTO ab_test_results
                            (id, goal_id, tenant_id, experiment_type, arm_id, score, created_at)
                        VALUES
                            (:id, :goal_id, :tenant_id, :experiment_type, :arm_id, :score, NOW())
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "id": uuid.uuid4().hex,
                        "goal_id": goal_id,
                        "tenant_id": tenant_id,
                        "experiment_type": experiment_type.value,
                        "arm_id": arm_id,
                        "score": score,
                    },
                )
        except Exception as exc:
            _log.warning("ab_test_result_persist_failed: %s", exc)

    async def _ensure_tenant_loaded(self, tenant_id: str) -> None:
        if tenant_id in self._hydrated_tenants:
            return
        # Mark first so a failing/slow DB does not trigger a reload per result.
        self._hydrated_tenants.add(tenant_id)
        await self.load_from_db(tenant_id=tenant_id)

    async def load_from_db(
        self,
        *,
        tenant_id: str,
        db_factory: Any | None = None,
        limit: int = 1000,
    ) -> int:
        """Load ONE tenant's recent results from ``ab_test_results`` into memory.

        Tenant-scoped (RLS GUC + ``WHERE tenant_id``); there is no all-tenant
        variant by design. Returns the number of results loaded.
        """
        factory = db_factory or self._db_factory
        if factory is None or not tenant_id:
            return 0
        self._hydrated_tenants.add(tenant_id)
        try:
            from sqlalchemy import text

            async with (
                factory() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                result = await session.execute(
                    text("""
                        SELECT goal_id, experiment_type, arm_id, score
                        FROM ab_test_results
                        WHERE tenant_id = :tid
                        ORDER BY created_at DESC
                        LIMIT :limit
                    """),
                    {"tid": tenant_id, "limit": limit},
                )
                rows = list(result.fetchall())
        except Exception as exc:
            _log.warning("ab_test_results_load_failed: %s", exc)
            return 0
        count = 0
        # Oldest first, so in-memory order matches recording order.
        for row in reversed(rows):
            goal_id, exp_type_str, arm_id, score = row[0], row[1], row[2], row[3]
            try:
                exp_type = ExperimentType(exp_type_str)
            except ValueError:
                continue
            self.record_result(goal_id, exp_type, arm_id, float(score), tenant_id=tenant_id)
            count += 1
        return count


ab_testing_engine = ABTestingEngine()
