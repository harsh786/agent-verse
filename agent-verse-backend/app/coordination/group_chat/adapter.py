"""Bounded, checkpointable shared-transcript group chat adapter."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.coordination.contracts import Classification
from app.coordination.group_chat.models import GroupChatExecutionState
from app.coordination.group_chat.state_machine import GroupChatState, transition_group_chat
from app.coordination.transcript.models import TranscriptMessage
from app.coordination.transcript.service import TranscriptService
from app.orchestration.strategy_adapters import ExecutionTier


@dataclass(frozen=True, slots=True)
class GroupChatAdapter:
    strategy_id: str = "group_chat"
    execution_tier: ExecutionTier = ExecutionTier.DISTRIBUTED

    def create_runtime(self, **kwargs: Any) -> GroupChatRuntime:
        return GroupChatRuntime(**kwargs)


class GroupChatRuntime:
    def __init__(
        self,
        *,
        transcript: TranscriptService,
        speaker_policy: Any,
        checkpoint_callback: Any = None,
        event_callback: Any = None,
    ) -> None:
        self._transcript = transcript
        self._speaker_policy = speaker_policy
        self._checkpoint = checkpoint_callback
        self._event = event_callback

    @staticmethod
    async def _invoke(callback: Any, *args: Any, **kwargs: Any) -> Any:
        value = callback(*args, **kwargs)
        return await value if inspect.isawaitable(value) else value

    async def _save(self, state: GroupChatExecutionState) -> None:
        if self._checkpoint is not None:
            await self._invoke(self._checkpoint, state)

    async def _emit(self, event: str, state: GroupChatExecutionState) -> None:
        if self._event is not None:
            await self._invoke(
                self._event,
                event,
                {
                    "session_id": state.session_id,
                    "round_number": state.round_number,
                    "speaker_id": state.selected_speaker_id,
                },
            )

    async def execute(
        self,
        state: GroupChatExecutionState,
        *,
        produce_turn: Any,
        terminate: Any,
        cancelled: asyncio.Event | None = None,
        maximum_rounds: int = 12,
        maximum_tokens: int = 24_000,
        maximum_cost_usd: float = 1.0,
    ) -> GroupChatExecutionState:
        if state.state is GroupChatState.CREATED:
            state = state.model_copy(
                update={"state": transition_group_chat(state.state, GroupChatState.ACTIVE)}
            )
            await self._save(state)
        if state.state is GroupChatState.AWAITING_HUMAN:
            return state
        active = tuple(item.agent_id for item in state.participants if item.active)
        if not active:
            failed = state.model_copy(
                update={"state": GroupChatState.FAILED, "terminal_reason": "no_active_participants"}
            )
            await self._save(failed)
            return failed
        maximum_rounds = max(1, min(maximum_rounds, 100))
        while state.round_number < maximum_rounds:
            reason = None
            if cancelled is not None and cancelled.is_set():
                reason = "cancelled"
            elif datetime.now(UTC) >= state.deadline:
                reason = "deadline_exceeded"
            elif state.token_count >= maximum_tokens:
                reason = "token_limit"
            elif state.cost_usd >= maximum_cost_usd:
                reason = "cost_limit"
            if reason is not None:
                terminal = (
                    GroupChatState.CANCELLED if reason == "cancelled" else GroupChatState.FAILED
                )
                stopped = state.model_copy(update={"state": terminal, "terminal_reason": reason})
                await self._save(stopped)
                return stopped

            recent = await self._transcript.page(
                state.tenant_id,
                state.session_id,
                after_sequence=max(0, state.last_sequence - 20),
                limit=20,
            )
            decision = await self._invoke(
                self._speaker_policy.select,
                active,
                turn=state.round_number,
                context={"messages": recent, "round": state.round_number},
            )
            state = state.model_copy(update={"selected_speaker_id": decision.agent_id})
            await self._save(state)
            await self._emit("group_chat.speaker_selected.v1", state)
            participant = next(
                item for item in state.participants if item.agent_id == decision.agent_id
            )
            if participant.is_human:
                waiting = state.model_copy(update={"state": GroupChatState.AWAITING_HUMAN})
                await self._save(waiting)
                await self._emit("group_chat.awaiting_human.v1", waiting)
                return waiting
            turn = await self._invoke(produce_turn, decision.agent_id, recent)
            next_tokens = state.token_count + int(turn.get("tokens", 0))
            next_cost = state.cost_usd + float(turn.get("cost_usd", 0.0))
            if next_tokens > maximum_tokens or next_cost > maximum_cost_usd:
                reason = "token_limit" if next_tokens > maximum_tokens else "cost_limit"
                limited = state.model_copy(
                    update={"state": GroupChatState.FAILED, "terminal_reason": reason}
                )
                await self._save(limited)
                return limited
            message: TranscriptMessage = await self._transcript.append(
                tenant_id=state.tenant_id,
                session_id=state.session_id,
                sender_agent_id=decision.agent_id,
                message_type=str(turn.get("message_type", "message")),
                content=str(turn["content"]),
                classification=Classification(str(turn.get("classification", "internal"))),
                idempotency_key=f"turn:{state.round_number + 1}:{decision.agent_id}",
                provenance_chain=tuple(turn.get("provenance_chain", ())),
            )
            state = state.model_copy(
                update={
                    "round_number": state.round_number + 1,
                    "last_sequence": message.sequence,
                    "token_count": next_tokens,
                    "cost_usd": next_cost,
                }
            )
            await self._save(state)
            if bool(await self._invoke(terminate, state, (*recent, message))):
                completed = state.model_copy(
                    update={
                        "state": transition_group_chat(state.state, GroupChatState.COMPLETED),
                        "terminal_reason": "termination_condition",
                    }
                )
                await self._save(completed)
                await self._emit("group_chat.completed.v1", completed)
                return completed
        limited = state.model_copy(
            update={"state": GroupChatState.FAILED, "terminal_reason": "round_limit"}
        )
        await self._save(limited)
        return limited

    async def resume_human_turn(
        self,
        state: GroupChatExecutionState,
        *,
        human_agent_id: str,
        content: str,
        idempotency_key: str,
        classification: Classification = Classification.INTERNAL,
    ) -> GroupChatExecutionState:
        if state.state is not GroupChatState.AWAITING_HUMAN:
            raise ValueError("group chat is not awaiting a human")
        selected = next(
            (
                participant
                for participant in state.participants
                if participant.agent_id == human_agent_id
            ),
            None,
        )
        if selected is None or not selected.active or not selected.is_human:
            raise PermissionError("human is not the selected active participant")
        if state.selected_speaker_id != human_agent_id:
            raise PermissionError("human is not the selected speaker")
        message = await self._transcript.append(
            tenant_id=state.tenant_id,
            session_id=state.session_id,
            sender_agent_id=human_agent_id,
            message_type="human",
            content=content,
            classification=classification,
            idempotency_key=idempotency_key,
        )
        resumed = state.model_copy(
            update={
                "state": transition_group_chat(state.state, GroupChatState.ACTIVE),
                "round_number": state.round_number + 1,
                "last_sequence": message.sequence,
                "selected_speaker_id": None,
            }
        )
        await self._save(resumed)
        await self._emit("group_chat.resumed.v1", resumed)
        return resumed


__all__ = ["GroupChatAdapter", "GroupChatRuntime"]
