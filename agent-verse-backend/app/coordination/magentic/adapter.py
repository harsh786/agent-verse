"""Checkpointed ledger-driven Magentic strategy runtime."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.coordination.ledger.models import LedgerRevision
from app.coordination.magentic.models import MagenticState, ParticipantCandidate
from app.coordination.magentic.participant_policy import select_participant
from app.coordination.magentic.progress_evaluator import evaluate_progress
from app.coordination.magentic.replan_manager import ReplanManager
from app.coordination.magentic.stall_detector import StallDetector
from app.coordination.patterns.common import invoke
from app.orchestration.strategy_adapters import ExecutionTier


@dataclass(frozen=True, slots=True)
class MagenticAdapter:
    strategy_id: str = "magentic"
    execution_tier: ExecutionTier = ExecutionTier.DISTRIBUTED

    def create_runtime(self, **kwargs: Any) -> MagenticRuntime:
        return MagenticRuntime(**kwargs)


class MagenticRuntime:
    def __init__(self, *, ledger_repository: Any, checkpoint_store: Any) -> None:
        self._ledger = ledger_repository
        self._checkpoints = checkpoint_store

    async def execute(
        self,
        *,
        tenant_id: str,
        session_id: str,
        execution_id: str,
        goal: str,
        plan: Any,
        participants: tuple[ParticipantCandidate, ...],
        required_capabilities: frozenset[str],
        run_participant: Any,
        replan: Any,
        synthesize: Any,
        maximum_rounds: int,
        maximum_resets: int,
        deadline: datetime,
        cancelled: asyncio.Event | None = None,
    ) -> tuple[MagenticState, str | None]:
        state = await self._checkpoints.load(session_id, execution_id)
        if state is not None and not isinstance(state, MagenticState):
            state = MagenticState.model_validate(state.model_dump())
        if state is not None and state.phase in {
            "completed",
            "failed",
            "cancelled",
            "awaiting_human",
        }:
            return state, state.safe_output
        current = await self._ledger.current(tenant_id, session_id)
        if state is None:
            initial = dict(await invoke(plan, goal))
            current = await self._ledger.append(
                LedgerRevision(
                    tenant_id=tenant_id,
                    session_id=session_id,
                    version=1,
                    objective=str(initial.get("objective", goal)),
                    open_work=tuple(initial.get("open_work", ())),
                    satisfaction_criteria=tuple(initial.get("satisfaction_criteria", ())),
                    blockers=tuple(initial.get("blockers", ())),
                    idempotency_key=f"{execution_id}:initial-plan",
                ),
                expected_predecessor_version=0,
            )
            state = MagenticState(
                tenant_id=tenant_id,
                session_id=session_id,
                execution_id=execution_id,
                phase="planning",
                ledger_version=current.version,
            )
            await self._checkpoints.save(state)
        if current is None:
            raise RuntimeError("Magentic ledger is unavailable")
        detector = StallDetector(max_no_progress=1, max_repeated_actions=2)
        manager = ReplanManager(max_resets=maximum_resets)
        while state.round_number < maximum_rounds:
            if cancelled is not None and cancelled.is_set():
                state = state.model_copy(
                    update={"phase": "cancelled", "terminal_reason": "cancelled"}
                )
                await self._checkpoints.save(state)
                return state, None
            if datetime.now(UTC) >= deadline:
                state = state.model_copy(
                    update={"phase": "failed", "terminal_reason": "deadline_exceeded"}
                )
                await self._checkpoints.save(state)
                return state, None
            decision = select_participant(
                participants,
                required_capabilities=required_capabilities,
                remaining_deadline_ms=max(
                    0, int((deadline - datetime.now(UTC)).total_seconds() * 1_000)
                ),
            )
            state = state.model_copy(
                update={"phase": "executing", "selected_agent_id": decision.selected_agent_id}
            )
            await self._checkpoints.save(state)
            updates = dict(await invoke(run_participant, decision.selected_agent_id, current))
            next_revision = current.model_copy(
                update={
                    **{
                        key: value
                        for key, value in updates.items()
                        if key in LedgerRevision.model_fields
                        and key not in {"tenant_id", "session_id", "version", "idempotency_key"}
                    },
                    "version": current.version + 1,
                    "predecessor_version": current.version,
                    "assignment_history": (
                        *current.assignment_history,
                        decision.selected_agent_id,
                    ),
                    "idempotency_key": f"{execution_id}:round:{state.round_number + 1}",
                }
            )
            next_revision = await self._ledger.append(
                next_revision, expected_predecessor_version=current.version
            )
            assessment = evaluate_progress(current, next_revision)
            state = state.model_copy(
                update={
                    "phase": "assessing",
                    "round_number": state.round_number + 1,
                    "ledger_version": next_revision.version,
                }
            )
            await self._checkpoints.save(state)
            current = next_revision
            criteria_met = set(current.satisfaction_criteria) <= set(current.satisfied_criteria)
            if not current.open_work and criteria_met:
                output = str(await invoke(synthesize, current))[:8_000]
                state = state.model_copy(update={"phase": "completed", "safe_output": output})
                await self._checkpoints.save(state)
                return state, output
            stall = detector.observe(
                revision_version=current.version,
                assessment=assessment,
                action_signature=current.last_action_signature,
                blockers=current.blockers,
            )
            if stall.stalled:
                try:
                    proposed = dict(await invoke(replan, current, stall))
                    replanned = manager.replan(
                        current,
                        open_work=tuple(proposed.get("open_work", current.open_work)),
                        contradicted_facts=tuple(proposed.get("contradicted_facts", ())),
                        stall_evidence_reference=str(
                            proposed.get("stall_evidence_reference", f"stall://{current.version}")
                        ),
                        idempotency_key=f"{execution_id}:reset:{current.reset_count + 1}",
                    )
                except RuntimeError:
                    state = state.model_copy(
                        update={
                            "phase": "awaiting_human",
                            "terminal_reason": "reset_exhausted",
                        }
                    )
                    await self._checkpoints.save(state)
                    return state, None
                current = await self._ledger.append(
                    replanned, expected_predecessor_version=next_revision.version
                )
                state = state.model_copy(
                    update={
                        "phase": "replanning",
                        "reset_count": current.reset_count,
                        "ledger_version": current.version,
                    }
                )
                await self._checkpoints.save(state)
        state = state.model_copy(update={"phase": "failed", "terminal_reason": "round_limit"})
        await self._checkpoints.save(state)
        return state, None


__all__ = ["MagenticAdapter", "MagenticRuntime"]
