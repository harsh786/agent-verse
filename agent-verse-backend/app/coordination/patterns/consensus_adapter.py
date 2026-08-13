"""Durable, bounded consensus verification with explicit lineage and escalation."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.coordination.patterns.common import invoke
from app.orchestration.strategy_adapters import ExecutionTier


class ConsensusVote(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    verifier_id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    success: bool
    safe_reason: str = Field(max_length=2_000)
    confidence: float = Field(ge=0, le=1)
    evidence_references: tuple[str, ...] = ()
    cost_usd: float = Field(default=0, ge=0)


class DurableConsensusState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    execution_id: str
    phase: Literal["created", "verifying", "judging", "completed", "escalated"]
    rubric_id: str
    rubric_version: str
    verifier_ids: tuple[str, ...]
    votes: tuple[ConsensusVote, ...] = ()
    judge_decision: bool | None = None
    judge_reference: str | None = None
    disagreement: bool = False
    quorum_met: bool = False
    requires_hitl: bool = False
    total_cost_usd: float = Field(default=0, ge=0)
    terminal_reason: str | None = None
    checkpoint_version: int = 1


@dataclass(frozen=True, slots=True)
class DurableConsensusAdapter:
    strategy_id: str = "consensus"
    execution_tier: ExecutionTier = ExecutionTier.DISTRIBUTED

    def create_runtime(self, **kwargs: Any) -> DurableConsensusRuntime:
        return DurableConsensusRuntime(**kwargs)


class DurableConsensusRuntime:
    def __init__(self, *, checkpoint_store: Any) -> None:
        self._store = checkpoint_store

    async def execute(
        self,
        *,
        session_id: str,
        execution_id: str,
        verifier_ids: tuple[str, ...],
        verify: Any,
        judge: Any,
        rubric_id: str,
        rubric_version: str,
        quorum: int,
        maximum_cost_usd: float,
        deadline: datetime,
        policy_allowed: bool = True,
        cancelled: asyncio.Event | None = None,
    ) -> DurableConsensusState:
        if len(verifier_ids) < 2 or len(verifier_ids) != len(set(verifier_ids)):
            raise ValueError("consensus requires distinct verifier identities")
        if not 2 <= quorum <= len(verifier_ids):
            raise ValueError("invalid consensus quorum")
        if not policy_allowed:
            raise PermissionError("consensus verification denied by policy")
        state = await self._store.load(session_id, execution_id)
        if state is None:
            state = DurableConsensusState(
                session_id=session_id,
                execution_id=execution_id,
                phase="created",
                rubric_id=rubric_id,
                rubric_version=rubric_version,
                verifier_ids=verifier_ids,
            )
            await self._store.save(state)
        elif not isinstance(state, DurableConsensusState):
            state = DurableConsensusState.model_validate(state.model_dump())
        if state.phase in {"completed", "escalated"}:
            return state
        completed = {vote.verifier_id for vote in state.votes}
        for verifier_id in verifier_ids:
            if verifier_id in completed:
                continue
            if cancelled is not None and cancelled.is_set():
                return await self._escalate(state, "cancelled")
            if datetime.now(UTC) >= deadline:
                return await self._escalate(state, "deadline_exceeded")
            try:
                raw = await invoke(verify, verifier_id)
                vote = ConsensusVote.model_validate(
                    {"verifier_id": verifier_id, **dict(raw)}
                )
            except Exception as exc:
                vote = ConsensusVote(
                    verifier_id=verifier_id,
                    provider_id="unavailable",
                    model_id="unavailable",
                    success=False,
                    safe_reason=f"verifier unavailable: {type(exc).__name__}",
                    confidence=0,
                )
            next_cost = state.total_cost_usd + vote.cost_usd
            if next_cost > maximum_cost_usd:
                return await self._escalate(state, "budget_exhausted")
            state = state.model_copy(
                update={
                    "phase": "verifying",
                    "votes": (*state.votes, vote),
                    "total_cost_usd": next_cost,
                }
            )
            await self._store.save(state)
        available = tuple(vote for vote in state.votes if vote.provider_id != "unavailable")
        if len(available) < quorum:
            return await self._escalate(state, "quorum_lost")
        outcomes = Counter(vote.success for vote in available)
        disagreement = len(outcomes) > 1
        state = state.model_copy(update={"phase": "judging", "disagreement": disagreement})
        await self._store.save(state)
        try:
            raw_judge = dict(await invoke(judge, state.votes, rubric_id, rubric_version))
            judge_cost = float(raw_judge.get("cost_usd", 0))
            if state.total_cost_usd + judge_cost > maximum_cost_usd:
                return await self._escalate(state, "budget_exhausted")
            decision = bool(raw_judge["success"])
            reference = str(raw_judge["evidence_reference"])
        except Exception:
            return await self._escalate(state, "judge_failed")
        requires_hitl = disagreement
        completed_state = state.model_copy(
            update={
                "phase": "escalated" if requires_hitl else "completed",
                "judge_decision": decision,
                "judge_reference": reference,
                "quorum_met": True,
                "requires_hitl": requires_hitl,
                "total_cost_usd": state.total_cost_usd + judge_cost,
                "terminal_reason": "disagreement" if requires_hitl else None,
            }
        )
        await self._store.save(completed_state)
        return completed_state

    async def _escalate(
        self, state: DurableConsensusState, reason: str
    ) -> DurableConsensusState:
        escalated = state.model_copy(
            update={
                "phase": "escalated",
                "requires_hitl": True,
                "terminal_reason": reason,
            }
        )
        await self._store.save(escalated)
        return escalated


__all__ = [
    "ConsensusVote",
    "DurableConsensusAdapter",
    "DurableConsensusRuntime",
    "DurableConsensusState",
]
