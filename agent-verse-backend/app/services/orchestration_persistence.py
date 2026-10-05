"""OrchestrationPersistence — persists all orchestration state to Postgres.

Called from graph.py _node_complete after every goal execution.
Writes to: eval_scorecards, reflexion_lessons, tool_trust_records, ab_test_results.
Degrades gracefully to in-memory when DB is not available.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent.state import AgentState
    from app.evals.runtime_scorecard import ScorecardResult
    from app.orchestration.runtime_profile import GoalRuntimeProfile


class OrchestrationPersistence:
    def __init__(
        self,
        db: Any = None,
        reflexion_store: Any = None,
        tool_trust_store: Any = None,
    ) -> None:
        self._db = db
        if reflexion_store is None:
            from app.state_runtime.reflexion_store import ReflexionStore

            reflexion_store = ReflexionStore()
        self._reflexion_store = reflexion_store
        if tool_trust_store is None:
            from app.tool_runtime.tool_trust_store import ToolTrustStore

            tool_trust_store = ToolTrustStore()
        self._tool_trust_store = tool_trust_store

    async def persist_scorecard(
        self,
        scorecard: ScorecardResult,
        *,
        profile: GoalRuntimeProfile,
        db: Any = None,
    ) -> None:
        """Persist RuntimeScorecard to eval_scorecards table."""
        effective_db = db or self._db
        if effective_db is None:
            return
        try:
            import json

            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            identity = ":".join(
                (
                    profile.tenant_id,
                    scorecard.goal_id,
                    scorecard.strategy_execution_id,
                    scorecard.evaluator_version,
                )
            )
            # Two bugs made this insert a 100%-failure no-op that was silently
            # swallowed by the except-warning below, so eval scorecards were NEVER
            # actually persisted despite the caller seeing no error:
            #   1. eval_scorecards has FORCE ROW LEVEL SECURITY (migration 0099) —
            #      without setting app.tenant_id here every insert violates the RLS
            #      policy.
            #   2. `:param::jsonb` is not valid inside a SQLAlchemy text() query:
            #      its bind-parameter regex refuses to treat `:name` as a parameter
            #      when immediately followed by another `:` (so it doesn't swallow
            #      Postgres's `::` cast operator), which left `:scores::jsonb` etc.
            #      as unsubstituted literal text and asyncpg failed to parse it.
            #      `app/intelligence/eval_runner.py::score_and_persist` already
            #      uses the correct `CAST(:param AS jsonb)` form for the same
            #      reason — mirrored here.
            async with (
                effective_db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, profile.tenant_id),
            ):
                await session.execute(
                    text("""
                        INSERT INTO eval_scorecards
                            (id, goal_id, tenant_id, overall_score, scores,
                             improvement_suggestions, profile_id, profile_version,
                             primary_strategy_id, primary_strategy_version,
                             auxiliary_strategy_versions, strategy_execution_id,
                             evaluator_version, dimension_status, evidence_references,
                             coverage, correlation_id, created_at)
                        VALUES
                            (:id, :goal_id, :tenant_id, :overall_score,
                             CAST(:scores AS jsonb), CAST(:suggestions AS jsonb), :profile_id,
                             :profile_version, :primary_strategy_id,
                             :primary_strategy_version,
                             CAST(:auxiliary_strategy_versions AS jsonb),
                             :strategy_execution_id, :evaluator_version,
                             CAST(:dimension_status AS jsonb),
                             CAST(:evidence_references AS jsonb),
                             :coverage, :correlation_id, NOW())
                        ON CONFLICT
                            (tenant_id, goal_id, strategy_execution_id, evaluator_version)
                        DO NOTHING
                    """),
                    {
                        "id": uuid.uuid5(uuid.NAMESPACE_URL, identity).hex,
                        "goal_id": scorecard.goal_id,
                        "tenant_id": profile.tenant_id,
                        "overall_score": scorecard.overall_score,
                        "scores": json.dumps(scorecard.scores),
                        "suggestions": json.dumps(
                            getattr(scorecard, "improvement_suggestions", [])
                        ),
                        "profile_id": getattr(profile, "profile_id", None),
                        "profile_version": scorecard.profile_version,
                        "primary_strategy_id": scorecard.primary_strategy_id,
                        "primary_strategy_version": scorecard.primary_strategy_version,
                        "auxiliary_strategy_versions": json.dumps(
                            scorecard.auxiliary_strategy_versions
                        ),
                        "strategy_execution_id": scorecard.strategy_execution_id,
                        "evaluator_version": scorecard.evaluator_version,
                        "dimension_status": json.dumps(scorecard.dimension_status),
                        "evidence_references": json.dumps(scorecard.evidence_references),
                        "coverage": scorecard.coverage,
                        "correlation_id": scorecard.correlation_id,
                    },
                )
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("scorecard_persist_failed", error=str(exc))

    async def persist_reflexion_lesson(
        self,
        state: AgentState,
        *,
        db: Any = None,
    ) -> bool:
        """Store a failed goal's reflexion lesson in memory + Postgres.

        Goes through ``ReflexionStore.record_async`` so the lesson passes the
        shared memory-write gate before it is stored anywhere (MEM-68); it used
        to cache the raw lesson and INSERT it unvetted here.
        """
        from app.agent.reflexion_wirer import ReflexionWirer

        wirer = ReflexionWirer(store=self._reflexion_store, db_factory=db or self._db)
        try:
            return await wirer.maybe_store_async(state)
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("reflexion_lesson_persist_failed", error=str(exc))
            return False

    async def persist_regression_case(
        self,
        regression_candidate: dict[str, Any],
        *,
        db: Any = None,
    ) -> None:
        """Persist a regression case candidate for the eval dataset."""
        effective_db = db or self._db
        if effective_db is None:
            return
        try:
            import json

            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            strategy_id = str(regression_candidate.get("strategy_id", "unknown"))
            strategy_version = str(regression_candidate.get("strategy_version", "unknown"))
            profile_version = int(regression_candidate.get("profile_version", 0))
            evaluator_version = str(regression_candidate.get("evaluator_version", "unknown"))
            tenant_id = str(regression_candidate.get("tenant_id", "unknown"))
            identity = ":".join(
                (
                    tenant_id,
                    str(regression_candidate.get("goal_id", "")),
                    strategy_id,
                    strategy_version,
                    str(profile_version),
                    evaluator_version,
                )
            )
            # Same two bugs as persist_scorecard above (see its comment): missing
            # RLS context against regression_cases' FORCE ROW LEVEL SECURITY policy,
            # plus an invalid `:evidence::jsonb` bind-parameter/cast collision that
            # asyncpg could never parse. Both made this insert a silent no-op.
            async with (
                effective_db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    text("""
                        INSERT INTO regression_cases
                            (id, tenant_id, goal_id, strategy_id, strategy_version,
                             profile_version, evaluator_version, dataset_version,
                             evidence, created_at)
                        VALUES (:id, :tenant_id, :goal_id, :strategy_id,
                                :strategy_version, :profile_version, :evaluator_version,
                                :dataset_version, CAST(:evidence AS jsonb), NOW())
                        ON CONFLICT
                            (tenant_id, goal_id, strategy_id, strategy_version,
                             profile_version, evaluator_version)
                        DO NOTHING
                    """),
                    {
                        "id": uuid.uuid5(uuid.NAMESPACE_URL, identity).hex,
                        "goal_id": regression_candidate.get("goal_id", ""),
                        "tenant_id": regression_candidate.get("tenant_id", "unknown"),
                        "strategy_id": strategy_id,
                        "strategy_version": strategy_version,
                        "profile_version": profile_version,
                        "evaluator_version": evaluator_version,
                        "dataset_version": regression_candidate.get(
                            "dataset_version", "default-v1"
                        ),
                        "evidence": json.dumps(regression_candidate),
                    },
                )
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("regression_case_persist_failed", error=str(exc))

    async def persist_tool_outcome(
        self,
        tool_name: str,
        *,
        success: bool,
        latency_ms: float,
        tenant_id: str,
        db: Any = None,
    ) -> None:
        """Persist tool trust outcome to in-memory store + Postgres."""
        self._tool_trust_store.record_outcome(
            tool_name, success=success, latency_ms=latency_ms, tenant_id=tenant_id
        )
        effective_db = db or self._db
        if effective_db is None:
            return
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            # tool_trust_records is FORCE RLS; the outcome belongs to the goal's
            # tenant, so write under that tenant's GUC.
            async with (
                effective_db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    text("""
                        INSERT INTO tool_trust_records
                            (id, tool_name, tenant_id, success, latency_ms,
                             call_count, created_at)
                        VALUES
                            (:id, :tool_name, :tenant_id, :success,
                             :latency_ms, 1, NOW())
                    """),
                    {
                        "id": uuid.uuid4().hex,
                        "tool_name": tool_name[:200],
                        "tenant_id": tenant_id,
                        "success": success,
                        "latency_ms": latency_ms,
                    },
                )
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("tool_trust_persist_failed", error=str(exc))

    async def load_tool_trust_from_db(self, tenant_id: str, db: Any = None) -> int:
        """Seed the in-memory trust store from ONE tenant's persisted history.

        Returns the number of rows loaded (0 when there is no DB, nothing
        persisted, or the load failed).

        This used to accept ``"*"`` and was fired at startup to pull every
        tenant's ``tool_trust_records`` in one unscoped SELECT. That scan is
        gone: under the least-privilege (NOBYPASSRLS) application role it
        matched zero rows (no tenant GUC), and using the maintenance role for it
        would have been a privilege escalation for a cache warm-up whose
        in-memory store has no readers that depend on it being warm. A tenant's
        history is now loaded only on request, scoped by RLS and by an explicit
        ``tenant_id`` predicate. A wildcard/empty tenant id is refused.
        """
        effective_db = db or self._db
        if effective_db is None:
            return 0
        if not tenant_id or tenant_id == "*":
            from app.observability.logging import get_logger

            get_logger(__name__).warning(
                "tool_trust_cross_tenant_load_refused",
                tenant_id=tenant_id,
                reason="tool trust history is loaded per tenant only",
            )
            return 0
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with (
                effective_db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                rows = (
                    await session.execute(
                        text("""
                            SELECT tool_name, success, latency_ms
                            FROM tool_trust_records
                            WHERE tenant_id = :tenant_id
                            ORDER BY created_at DESC
                            LIMIT 1000
                        """),
                        {"tenant_id": tenant_id},
                    )
                ).fetchall()
            # Newest-first from the query; replay oldest-first so the history
            # (and its trailing consecutive-failure count) is chronological.
            for row in reversed(rows):
                self._tool_trust_store.record_outcome(
                    row[0], success=row[1], latency_ms=row[2], tenant_id=tenant_id
                )
            return len(rows)
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning(
                "tool_trust_load_failed", tenant_id=tenant_id, error=str(exc)
            )
            return 0
