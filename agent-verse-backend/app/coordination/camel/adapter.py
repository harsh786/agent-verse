"""Checkpointed, canonical-transcript CAMEL runtime."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.coordination.camel.inception import build_inception
from app.coordination.camel.models import CamelState, RoleContract
from app.coordination.camel.termination import normalize_utterance, terminal_reason
from app.coordination.contracts import Classification
from app.coordination.patterns.common import invoke
from app.orchestration.strategy_adapters import ExecutionTier


@dataclass(frozen=True, slots=True)
class CamelAdapter:
    strategy_id: str = "camel"
    execution_tier: ExecutionTier = ExecutionTier.DISTRIBUTED

    def create_runtime(self, **kwargs: Any) -> CamelRuntime:
        return CamelRuntime(**kwargs)


class CamelRuntime:
    def __init__(self, *, checkpoint_store: Any, transcript_service: Any) -> None:
        self._checkpoints = checkpoint_store
        self._transcript = transcript_service

    async def execute(
        self,
        *,
        tenant_id: str,
        session_id: str,
        execution_id: str,
        roles: tuple[RoleContract, ...],
        available_tools: frozenset[str],
        available_connectors: frozenset[str],
        platform_authority: frozenset[str],
        run_turn: Any,
        authorize_tools: Any,
        maximum_turns: int,
        maximum_tokens: int,
        maximum_cost_usd: float,
        deadline: datetime,
        cancelled: asyncio.Event | None = None,
    ) -> CamelState:
        loaded = await self._checkpoints.load(session_id, execution_id)
        state = (
            loaded
            if isinstance(loaded, CamelState)
            else CamelState.model_validate(loaded.model_dump())
            if loaded is not None
            else CamelState(tenant_id=tenant_id, session_id=session_id, execution_id=execution_id)
        )
        if state.phase in {"completed", "failed", "cancelled", "awaiting_human"}:
            return state
        artifact = build_inception(
            roles,
            available_tools=available_tools,
            available_connectors=available_connectors,
            platform_authority=platform_authority,
            maximum_context_characters=16_000,
        )
        if state.contract_digest is None:
            state = state.model_copy(
                update={"phase": "inception", "contract_digest": artifact.contract_digest}
            )
            await self._checkpoints.save(state)
        elif state.contract_digest != artifact.contract_digest:
            raise RuntimeError("role contract changed during execution")
        while state.turn_count < maximum_turns:
            if cancelled is not None and cancelled.is_set():
                state = state.model_copy(
                    update={"phase": "cancelled", "terminal_reason": "cancelled"}
                )
                await self._checkpoints.save(state)
                return state
            role = roles[state.turn_count % len(roles)]
            raw = dict(await invoke(run_turn, role, {"turn_count": state.turn_count}))
            requested_tools = frozenset(str(item) for item in raw.get("requested_tools", ()))
            if not requested_tools <= role.tool_allowlist or not await invoke(
                authorize_tools, role.role_name, requested_tools
            ):
                state = state.model_copy(
                    update={"phase": "failed", "terminal_reason": "tool_authorization_denied"}
                )
                await self._checkpoints.save(state)
                return state
            content = str(raw.get("content", ""))[:16_000]
            normalized = normalize_utterance(content)
            repeated = (
                state.repeated_utterances + 1
                if normalized and normalized == state.last_normalized_utterance
                else 0
            )
            next_turn = state.turn_count + 1
            await self._transcript.append(
                tenant_id=tenant_id,
                session_id=session_id,
                sender_agent_id=role.role_name,
                message_type="message",
                content=content,
                classification=Classification.INTERNAL,
                idempotency_key=f"{execution_id}:turn:{next_turn}",
            )
            state = state.model_copy(
                update={
                    "phase": "evaluating_termination",
                    "turn_count": next_turn,
                    "token_count": state.token_count + int(raw.get("tokens", 0)),
                    "cost_usd": state.cost_usd + float(raw.get("cost_usd", 0)),
                    "last_normalized_utterance": normalized,
                    "repeated_utterances": repeated,
                }
            )
            reason = terminal_reason(
                completed=bool(raw.get("completed")),
                agreement=bool(raw.get("agreement")),
                turn_count=state.turn_count,
                maximum_turns=maximum_turns,
                tokens=state.token_count,
                maximum_tokens=maximum_tokens,
                cost_usd=state.cost_usd,
                maximum_cost_usd=maximum_cost_usd,
                deadline=deadline,
                repeated_utterances=state.repeated_utterances,
            )
            if reason is not None:
                phase = "completed" if reason == "completed" else "failed"
                state = state.model_copy(
                    update={
                        "phase": phase,
                        "terminal_reason": reason,
                        "safe_output": str(raw.get("safe_output", ""))[:8_000] or None,
                    }
                )
                await self._checkpoints.save(state)
                return state
            state = state.model_copy(update={"phase": "dialoguing"})
            await self._checkpoints.save(state)
        return state


__all__ = ["CamelAdapter", "CamelRuntime"]
