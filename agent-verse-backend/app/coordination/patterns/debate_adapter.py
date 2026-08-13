"""Durable independent-proposal, critique, and validated-vote debate adapter."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.coordination.patterns.common import invoke
from app.orchestration.strategy_adapters import ExecutionTier


class DebateProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str = Field(min_length=1)
    proposal_reference: str = Field(min_length=1)
    safe_summary: str = Field(min_length=1, max_length=2_000)
    critique_references: tuple[str, ...] = ()


class DurableDebateState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    execution_id: str
    phase: Literal["created", "proposed", "critiqued", "voted", "completed", "failed"]
    participant_ids: tuple[str, ...]
    proposals: tuple[DebateProposal, ...] = ()
    votes: tuple[tuple[str, str], ...] = ()
    winner_agent_id: str | None = None
    dissenting_agent_ids: tuple[str, ...] = ()
    requires_hitl: bool = False
    checkpoint_version: int = 1


@dataclass(frozen=True, slots=True)
class DurableDebateAdapter:
    strategy_id: str = "debate"
    execution_tier: ExecutionTier = ExecutionTier.DISTRIBUTED

    def create_runtime(self, **kwargs: Any) -> DurableDebateRuntime:
        return DurableDebateRuntime(**kwargs)


class DurableDebateRuntime:
    def __init__(self, *, checkpoint_store: Any) -> None:
        self._store = checkpoint_store

    async def execute(
        self,
        *,
        session_id: str,
        execution_id: str,
        participant_ids: tuple[str, ...],
        propose: Any,
        critique: Any,
        vote: Any,
        quorum: int,
    ) -> DurableDebateState:
        if len(participant_ids) < 2 or len(participant_ids) != len(set(participant_ids)):
            raise ValueError("debate requires distinct participants")
        if not 1 <= quorum <= len(participant_ids):
            raise ValueError("invalid debate quorum")
        state = await self._store.load(session_id, execution_id)
        if state is None:
            proposals = await asyncio.gather(
                *(invoke(propose, agent_id) for agent_id in participant_ids)
            )
            typed = tuple(
                DebateProposal(
                    agent_id=agent_id,
                    proposal_reference=str(result["proposal_reference"]),
                    safe_summary=str(result["safe_summary"]),
                )
                for agent_id, result in zip(participant_ids, proposals, strict=True)
            )
            state = DurableDebateState(
                session_id=session_id,
                execution_id=execution_id,
                phase="proposed",
                participant_ids=participant_ids,
                proposals=typed,
            )
            await self._store.save(state)
        if not isinstance(state, DurableDebateState):
            state = DurableDebateState.model_validate(state.model_dump())
        if state.phase == "proposed":
            updated: list[DebateProposal] = []
            for proposal in state.proposals:
                refs: list[str] = []
                for other in state.proposals:
                    if other.agent_id != proposal.agent_id:
                        refs.append(
                            str(
                                await invoke(
                                    critique, proposal.agent_id, other.agent_id
                                )
                            )
                        )
                updated.append(
                    proposal.model_copy(update={"critique_references": tuple(refs)})
                )
            state = state.model_copy(update={"phase": "critiqued", "proposals": tuple(updated)})
            await self._store.save(state)
        if state.phase == "critiqued":
            raw_votes = await asyncio.gather(
                *(invoke(vote, voter, state.proposals) for voter in participant_ids)
            )
            votes: list[tuple[str, str]] = []
            for voter, target in zip(participant_ids, raw_votes, strict=True):
                target_id = str(target)
                if target_id not in participant_ids or target_id == voter:
                    raise ValueError("vote targets a nonexistent or self agent")
                votes.append((voter, target_id))
            tally = Counter(target for _, target in votes)
            winner, count = sorted(tally.items(), key=lambda item: (-item[1], item[0]))[0]
            requires_hitl = count < quorum
            dissent = tuple(voter for voter, target in votes if target != winner)
            state = state.model_copy(
                update={
                    "phase": "completed" if not requires_hitl else "failed",
                    "votes": tuple(votes),
                    "winner_agent_id": winner,
                    "dissenting_agent_ids": dissent,
                    "requires_hitl": requires_hitl,
                }
            )
            await self._store.save(state)
        return state


__all__ = [
    "DebateProposal",
    "DurableDebateAdapter",
    "DurableDebateRuntime",
    "DurableDebateState",
]
