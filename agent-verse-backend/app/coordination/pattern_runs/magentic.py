"""Magentic driver: ledger-driven orchestration with human review on reset exhaustion."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterator
from typing import Any

from app.coordination.ledger.models import LedgerRevision
from app.coordination.magentic.adapter import MagenticRuntime
from app.coordination.magentic.models import ParticipantCandidate, StallAssessment
from app.coordination.pattern_runs.context import RunContext, RunOutcome
from app.coordination.pattern_runs.llm import string_list

_CAPABILITY = "general"


class _LoadBalancedParticipants(tuple[ParticipantCandidate, ...]):
    """Participants whose ``current_load`` is the number of rounds already assigned.

    ``select_participant`` prefers the least-loaded eligible agent, so reporting
    live assignment counts spreads the ledger's work across the participants.
    """

    assignments: Counter[str]

    def __new__(cls, agent_ids: tuple[str, ...]) -> _LoadBalancedParticipants:
        base = tuple(
            ParticipantCandidate(
                agent_id=agent_id,
                capabilities=frozenset({_CAPABILITY}),
                available=True,
                policy_eligible=True,
                current_load=0,
                estimated_latency_ms=1_000,
            )
            for agent_id in agent_ids
        )
        instance = super().__new__(cls, base)
        instance.assignments = Counter()
        return instance

    def __iter__(self) -> Iterator[ParticipantCandidate]:
        for candidate in super().__iter__():
            yield candidate.model_copy(
                update={"current_load": self.assignments[candidate.agent_id]}
            )


async def run_magentic(ctx: RunContext, *, ledger_repository: Any) -> RunOutcome:
    config = ctx.document.config
    extra_resets = int(config.get("approved_resets", 0))
    runtime = MagenticRuntime(ledger_repository=ledger_repository, checkpoint_store=ctx.checkpoints)
    participants = _LoadBalancedParticipants(ctx.participants)
    current = await ledger_repository.current(ctx.tenant_id, ctx.session_id)
    if current is not None:
        participants.assignments.update(current.assignment_history)
    results: dict[str, str] = dict(ctx.document.view.get("results") or {})

    async def plan(goal: str) -> dict[str, Any]:
        data = await ctx.llm.json(
            "You are the Magentic orchestrator. Build a task ledger for the goal.\n"
            f"Goal: {goal}\n"
            'Return {"objective": str, "open_work": [2-5 concrete work items], '
            '"satisfaction_criteria": [1-3 checkable criteria]}',
            step="plan",
        )
        open_work = string_list(data.get("open_work"), limit=5) or (goal[:500],)
        criteria = string_list(data.get("satisfaction_criteria"), limit=3) or (
            "The goal is fully answered",
        )
        return {
            "objective": str(data.get("objective") or goal)[:4_000],
            "open_work": open_work,
            "satisfaction_criteria": criteria,
        }

    async def run_participant(agent_id: str, ledger: LedgerRevision) -> dict[str, Any]:
        participants.assignments[agent_id] += 1
        round_number = len(ledger.assignment_history) + 1
        if ledger.open_work:
            item = ledger.open_work[0]
            data = await ctx.llm.json(
                f"You are participant '{agent_id}' in a Magentic team.\n"
                f"Objective: {ledger.objective}\n"
                f"Completed so far: {list(ledger.completed_work)}\n"
                f"Your work item: {item}\n"
                'Do the work. Return {"result": str, "completed": bool, '
                '"verified_facts": [short facts established], "blockers": [str]}',
                step=f"round-{round_number}",
            )
            result = str(data.get("result") or "")[:4_000]
            completed = bool(data.get("completed")) and bool(result)
            await ctx.say(
                agent_id,
                f"[magentic round {round_number}] {item}: {result or '(no result)'}",
                step=f"magentic:round:{round_number}",
                message_type="evidence",
            )
            updates: dict[str, Any] = {
                "last_action_signature": f"{agent_id}:{item}",
                "blockers": string_list(data.get("blockers"), limit=5),
                "verified_facts": tuple(
                    dict.fromkeys(
                        (*ledger.verified_facts, *string_list(data.get("verified_facts"), limit=5))
                    )
                ),
            }
            if completed:
                results[item] = result
                await ctx.update_view(results=results)
                digest = hashlib.sha256(result.encode()).hexdigest()[:16]
                updates |= {
                    "open_work": ledger.open_work[1:],
                    "completed_work": (*ledger.completed_work, item),
                    "evidence_references": (
                        *ledger.evidence_references,
                        f"magentic://{ctx.execution_id}/{round_number}#{digest}",
                    ),
                }
            return updates
        # No open work left: verify the satisfaction criteria against the results.
        data = await ctx.llm.json(
            f"Objective: {ledger.objective}\nResults: {results}\n"
            f"Criteria: {list(ledger.satisfaction_criteria)}\n"
            'Which criteria are satisfied by the results? Return {"satisfied": [criteria '
            'copied exactly], "missing_work": [work still needed, if any]}',
            step=f"verify-{round_number}",
        )
        satisfied = tuple(
            item
            for item in string_list(data.get("satisfied"), limit=10)
            if item in ledger.satisfaction_criteria
        )
        return {
            "last_action_signature": f"{agent_id}:verify",
            "satisfied_criteria": tuple(dict.fromkeys((*ledger.satisfied_criteria, *satisfied))),
            "open_work": string_list(data.get("missing_work"), limit=3),
            "confidence": 0.9 if satisfied else 0.3,
        }

    async def replan(ledger: LedgerRevision, stall: StallAssessment) -> dict[str, Any]:
        data = await ctx.llm.json(
            f"The Magentic team stalled ({', '.join(stall.reasons)}).\n"
            f"Objective: {ledger.objective}\nOpen work: {list(ledger.open_work)}\n"
            f"Blockers: {list(ledger.blockers)}\n"
            'Replan. Return {"open_work": [revised work items], "contradicted_facts": [str]}',
            step=f"replan-{ledger.reset_count + 1}",
        )
        return {
            "open_work": string_list(data.get("open_work"), limit=5) or ledger.open_work,
            "contradicted_facts": string_list(data.get("contradicted_facts"), limit=5),
            "stall_evidence_reference": f"stall://{ctx.execution_id}/{ledger.version}",
        }

    async def synthesize(ledger: LedgerRevision) -> str:
        answer = await ctx.llm.text(
            f"Objective: {ledger.objective}\nVerified facts: {list(ledger.verified_facts)}\n"
            f"Work results: {results}\nWrite the final answer.",
            step="synthesize",
            max_tokens=1_200,
        )
        await ctx.say("orchestrator", answer, step="magentic:final", message_type="decision")
        return answer

    base_rounds = ctx.max_rounds
    state, output = await runtime.execute(
        tenant_id=ctx.tenant_id,
        session_id=ctx.session_id,
        execution_id=ctx.execution_id,
        goal=ctx.objective,
        plan=plan,
        participants=participants,
        required_capabilities=frozenset({_CAPABILITY}),
        run_participant=run_participant,
        replan=replan,
        synthesize=synthesize,
        maximum_rounds=base_rounds * (1 + extra_resets),
        maximum_resets=int(config.get("max_resets", 1)) + extra_resets,
        deadline=ctx.deadline,
    )
    return RunOutcome(
        phase=state.phase,
        terminal_reason=state.terminal_reason,
        safe_output=output,
        view={
            "results": results,
            "ledger_version": state.ledger_version,
            "round_number": state.round_number,
            "reset_count": state.reset_count,
            "selected_agent_id": state.selected_agent_id,
        },
    )


__all__ = ["run_magentic"]
