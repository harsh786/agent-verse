"""Group-chat driver: participants take round-robin turns on the shared transcript.

Each turn is one LLM call in the speaker's role; the chat ends when a participant
declares the objective done (its ``final_answer`` is the run's output) or a bound
(rounds, tokens, cost, deadline) stops it. State is the runtime's checkpointed
``GroupChatExecutionState``, so a retried run resumes after the last turn.
"""

from __future__ import annotations

from typing import Any

from app.coordination.group_chat.adapter import GroupChatRuntime
from app.coordination.group_chat.models import GroupChatExecutionState, GroupChatParticipant
from app.coordination.group_chat.speaker_policy import RoundRobinSpeakerPolicy
from app.coordination.group_chat.state_machine import GroupChatState
from app.coordination.pattern_runs.context import RunContext, RunOutcome

_PHASE = {
    GroupChatState.COMPLETED: "completed",
    GroupChatState.CANCELLED: "cancelled",
}


def _initial_state(ctx: RunContext) -> GroupChatExecutionState:
    checkpoint = ctx.document.checkpoint
    if checkpoint:
        return GroupChatExecutionState.model_validate(checkpoint)
    return GroupChatExecutionState(
        tenant_id=ctx.tenant_id,
        session_id=ctx.session_id,
        participants=tuple(GroupChatParticipant(agent_id=p) for p in ctx.participants),
        deadline=ctx.deadline,
    )


class _RunScopedTranscript:
    """Scope the runtime's ``turn:{n}:{agent}`` idempotency keys to this run, so a
    second group chat in the same session does not replay the first one's turns."""

    def __init__(self, inner: Any, execution_id: str) -> None:
        self._inner = inner
        self._execution_id = execution_id

    async def append(self, **kwargs: Any) -> Any:
        kwargs["idempotency_key"] = f"{self._execution_id}:{kwargs['idempotency_key']}"
        if not kwargs.get("provenance_chain"):
            kwargs["provenance_chain"] = (f"pattern-run:{self._execution_id}",)
        return await self._inner.append(**kwargs)

    async def page(self, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.page(*args, **kwargs)


async def run_group_chat(ctx: RunContext) -> RunOutcome:
    final: dict[str, str] = {}

    async def produce_turn(agent_id: str, recent: Any) -> dict[str, Any]:
        history = (
            "\n".join(
                f"{getattr(m, 'sender_agent_id', '?')}: {getattr(m, 'content', '')}"[:1_000]
                for m in tuple(recent)[-10:]
            )
            or "(the chat starts)"
        )
        tokens_before = ctx.llm.tokens
        data = await ctx.llm.json(
            f"Group chat. You are '{agent_id}', one of {', '.join(ctx.participants)}, "
            f"working together on: {ctx.objective[:1_500]}\n"
            f"Conversation so far:\n{history}\n"
            'Write your next message. Return {"content": str, "done": bool, '
            '"final_answer": str}. Set done to true only when the objective is fully '
            "met, and then put the complete answer in final_answer.",
            step="turn",
        )
        content = str(data.get("content") or "")[:4_000] or "(no message)"
        if bool(data.get("done")):
            final["answer"] = str(data.get("final_answer") or content)[:16_000]
            final["by"] = agent_id
        return {"content": content, "tokens": ctx.llm.tokens - tokens_before}

    async def terminate(_state: GroupChatExecutionState, _messages: Any) -> bool:
        return "answer" in final

    participants = len(ctx.participants) or 1
    state = await GroupChatRuntime(
        transcript=_RunScopedTranscript(ctx.transcript, ctx.execution_id),  # type: ignore[arg-type]
        speaker_policy=RoundRobinSpeakerPolicy(),
        checkpoint_callback=ctx.checkpoints.save,
    ).execute(
        _initial_state(ctx),
        produce_turn=produce_turn,
        terminate=terminate,
        maximum_rounds=ctx.max_rounds * participants,
        maximum_tokens=int(ctx.options.get("max_tokens", 40_000)),
        maximum_cost_usd=float(ctx.options.get("max_cost_usd", 1.0)),
    )
    return RunOutcome(
        phase=_PHASE.get(state.state, "failed"),
        terminal_reason=state.terminal_reason,
        safe_output=final.get("answer") if state.state is GroupChatState.COMPLETED else None,
        view={
            "participants": list(ctx.participants),
            "rounds": state.round_number,
            "concluded_by": final.get("by"),
        },
    )


__all__ = ["run_group_chat"]
