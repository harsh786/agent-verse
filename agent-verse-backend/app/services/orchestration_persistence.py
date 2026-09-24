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
    ) -> None:
        """Store reflexion lesson in memory + Postgres."""
        from app.agent.reflexion_wirer import ReflexionWirer

        wirer = ReflexionWirer(store=self._reflexion_store)
        stored = wirer.maybe_store(state)
        if not stored:
            return
        effective_db = db or self._db
        if effective_db is None:
            return
        try:
            from sqlalchemy import text

            lessons = self._reflexion_store.recall(tenant_id=state.tenant_ctx.tenant_id, limit=1)
            if lessons:
                latest = lessons[-1]
                async with effective_db() as session, session.begin():
                    await session.execute(
                        text("""
                            INSERT INTO reflexion_lessons
                                (id, tenant_id, lesson, source_goal_id,
                                 failure_class, created_at)
                            VALUES
                                (:id, :tenant_id, :lesson, :source_goal_id,
                                 :failure_class, NOW())
                            ON CONFLICT DO NOTHING
                        """),
                        {
                            "id": uuid.uuid4().hex,
                            "tenant_id": state.tenant_ctx.tenant_id,
                            "lesson": latest["lesson"],
                            "source_goal_id": state.goal_id,
                            "failure_class": latest.get("failure_class", "unknown"),
                        },
                    )
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("reflexion_lesson_persist_failed", error=str(exc))

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
        self._tool_trust_store.record_outcome(tool_name, success=success, latency_ms=latency_ms)
        effective_db = db or self._db
        if effective_db is None:
            return
        try:
            from sqlalchemy import text

            async with effective_db() as session, session.begin():
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

    async def load_tool_trust_from_db(self, tenant_id: str, db: Any = None) -> None:
        """Load tool trust history from Postgres into in-memory store on startup."""
        effective_db = db or self._db
        if effective_db is None:
            return
        try:
            from sqlalchemy import text

            async with effective_db() as session:
                if tenant_id == "*":
                    # Load ALL tenants' tool trust history
                    rows = (
                        await session.execute(
                            text("""
                                SELECT tool_name, success, latency_ms
                                FROM tool_trust_records
                                ORDER BY created_at DESC
                                LIMIT 5000
                            """)
                        )
                    ).fetchall()
                else:
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
                for row in rows:
                    self._tool_trust_store.record_outcome(row[0], success=row[1], latency_ms=row[2])
        except Exception:
            pass
