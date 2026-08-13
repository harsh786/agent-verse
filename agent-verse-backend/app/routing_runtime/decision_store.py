"""Idempotent tenant-scoped routing decision and outcome persistence."""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.routing import RoutingDecisionRow, RoutingOutcomeRow
from app.db.rls import sqlalchemy_rls_context
from app.routing_runtime.contracts import OptimizationOutcome, RoutingDecision


class InMemoryDecisionStore:
    def __init__(self) -> None:
        self._decisions: dict[tuple[str, str], RoutingDecision] = {}
        self._outcomes: dict[tuple[str, str, int, str], OptimizationOutcome] = {}
        self._lock = asyncio.Lock()

    async def save_decision(self, decision: RoutingDecision) -> RoutingDecision:
        key = (decision.tenant_id, decision.decision_id)
        async with self._lock:
            prior = self._decisions.get(key)
            if prior is not None and prior != decision:
                raise ValueError("routing decision ID collision")
            self._decisions[key] = decision
            return prior or decision

    async def get_decision(self, tenant_id: str, decision_id: str) -> RoutingDecision | None:
        return self._decisions.get((tenant_id, decision_id))

    async def save_outcome(self, outcome: OptimizationOutcome) -> OptimizationOutcome:
        if (outcome.tenant_id, outcome.decision_id) not in self._decisions:
            raise KeyError("routing decision not found")
        key = (outcome.tenant_id, outcome.decision_id, outcome.attempt, outcome.evaluator_version)
        async with self._lock:
            prior = self._outcomes.get(key)
            if prior is not None and prior != outcome:
                raise ValueError("routing outcome key collision")
            self._outcomes[key] = outcome
            return prior or outcome


class PostgresDecisionStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def save_decision(self, decision: RoutingDecision) -> RoutingDecision:
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, decision.tenant_id),
        ):
            prior = await db.get(RoutingDecisionRow, decision.decision_id)
            if prior is not None:
                return _decision(prior.payload)
            await db.execute(
                insert(RoutingDecisionRow).values(
                    id=decision.decision_id,
                    tenant_id=decision.tenant_id,
                    goal_id=decision.goal_id,
                    execution_id=decision.execution_id,
                    category=decision.category,
                    profile_version=decision.signals.profile_version,
                    selected_candidate_id=decision.selected_candidate_id,
                    selected_candidate_version=next(
                        (
                            item.candidate_version
                            for item in decision.candidates
                            if item.candidate_id == decision.selected_candidate_id
                        ),
                        None,
                    ),
                    safe_rationale=decision.safe_rationale,
                    payload=decision.model_dump(mode="json"),
                    created_at=decision.created_at,
                )
            )
            return decision

    async def get_decision(self, tenant_id: str, decision_id: str) -> RoutingDecision | None:
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            row = (
                await db.execute(
                    select(RoutingDecisionRow).where(RoutingDecisionRow.id == decision_id)
                )
            ).scalar_one_or_none()
            return _decision(row.payload) if row is not None else None

    async def save_outcome(self, outcome: OptimizationOutcome) -> OptimizationOutcome:
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, outcome.tenant_id),
        ):
            prior = await db.get(RoutingOutcomeRow, outcome.outcome_id)
            if prior is not None:
                return OptimizationOutcome.model_validate(prior.payload)
            await db.execute(
                insert(RoutingOutcomeRow).values(
                    id=outcome.outcome_id,
                    tenant_id=outcome.tenant_id,
                    decision_id=outcome.decision_id,
                    attempt=outcome.attempt,
                    evaluator_version=outcome.evaluator_version,
                    success=outcome.success,
                    quality_score=outcome.quality_score,
                    actual_cost_usd=outcome.actual_cost_usd,
                    actual_latency_ms=outcome.actual_latency_ms,
                    prompt_tokens=outcome.prompt_tokens,
                    completion_tokens=outcome.completion_tokens,
                    fallback_used=outcome.fallback_used,
                    error_class=outcome.error_class,
                    payload=outcome.model_dump(mode="json"),
                    recorded_at=outcome.recorded_at,
                )
            )
            return outcome


def _decision(payload: dict[str, Any]) -> RoutingDecision:
    return RoutingDecision.model_validate(payload)


__all__ = ["InMemoryDecisionStore", "PostgresDecisionStore"]
