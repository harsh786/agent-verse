"""Checkpointed layered Mixture-of-Agents execution runtime."""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.coordination.moa.aggregator import build_aggregation_input
from app.coordination.moa.models import MoALayer, MoAPlan, MoAProposal, ModelCandidate
from app.coordination.moa.quorum import evaluate_quorum
from app.coordination.patterns.common import invoke
from app.orchestration.strategy_adapters import ExecutionTier


class MoAExecutionState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    session_id: str
    execution_id: str
    phase: Literal[
        "admitted", "layer_dispatching", "layer_collecting", "layer_aggregating",
        "synthesizing", "completed", "failed", "cancelled"
    ] = "admitted"
    completed_layers: int = Field(default=0, ge=0)
    aggregate_references: tuple[str, ...] = ()
    safe_output: str | None = Field(default=None, max_length=8_000)
    total_cost_usd: float = Field(default=0, ge=0)
    degraded: bool = False
    terminal_reason: str | None = None
    checkpoint_version: int = 1


@dataclass(frozen=True, slots=True)
class MoAAdapter:
    strategy_id: str = "mixture_of_agents"
    execution_tier: ExecutionTier = ExecutionTier.DISTRIBUTED

    def create_runtime(self, **kwargs: Any) -> MoARuntime:
        return MoARuntime(**kwargs)


class MoARuntime:
    def __init__(self, *, repository: Any, checkpoint_store: Any) -> None:
        self._repository = repository
        self._checkpoints = checkpoint_store

    async def execute(
        self,
        *,
        tenant_id: str,
        session_id: str,
        execution_id: str,
        objective: str,
        plan: MoAPlan,
        candidates: tuple[ModelCandidate, ...],
        propose: Any,
        aggregate: Any,
        maximum_cost_usd: float,
        allow_degraded: bool = False,
        cancelled: asyncio.Event | None = None,
    ) -> MoAExecutionState:
        loaded = await self._checkpoints.load(session_id, execution_id)
        state: MoAExecutionState | None
        if loaded is None:
            state = None
        elif isinstance(loaded, MoAExecutionState):
            state = loaded
        else:
            state = MoAExecutionState.model_validate(loaded.model_dump())
        if state is not None and state.phase in {"completed", "failed", "cancelled"}:
            return state
        if state is None:
            if plan.worst_case_cost_usd > maximum_cost_usd:
                raise ValueError("MoA plan exceeds runtime cost ceiling")
            state = MoAExecutionState(
                tenant_id=tenant_id,
                session_id=session_id,
                execution_id=execution_id,
            )
            await self._checkpoints.save(state)
        by_deployment = {item.deployment_id: item for item in candidates}
        prior_ids: tuple[str, ...] = ()
        for layer_plan in plan.layers[state.completed_layers:]:
            if cancelled is not None and cancelled.is_set():
                state = state.model_copy(
                    update={"phase": "cancelled", "terminal_reason": "cancelled"}
                )
                await self._checkpoints.save(state)
                return state
            layer = layer_plan.model_copy(
                update={
                    "tenant_id": tenant_id,
                    "session_id": session_id,
                    "strategy_execution_id": execution_id,
                    "idempotency_key": f"{execution_id}:layer:{layer_plan.layer_index}",
                }
            )
            await self._repository.create_layer(layer)
            state = state.model_copy(update={"phase": "layer_dispatching"})
            await self._checkpoints.save(state)

            async def run(
                candidate: ModelCandidate,
                current_layer: MoALayer = layer,
                predecessors: tuple[str, ...] = prior_ids,
            ) -> MoAProposal:
                proposal_id = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{tenant_id}:{execution_id}:{current_layer.layer_index}:"
                    f"{candidate.deployment_id}:1",
                ).hex
                try:
                    raw = dict(
                        await invoke(
                            propose,
                            current_layer.layer_index,
                            candidate,
                            {
                                "objective": objective,
                                "predecessor_proposal_ids": predecessors,
                            },
                        )
                    )
                    safe_excerpt = str(raw["safe_excerpt"])
                    valid = not bool(
                        re.search(
                            r"ignore previous instructions|reveal secrets?",
                            safe_excerpt,
                            re.IGNORECASE,
                        )
                    )
                    rejection = None if valid else "unsafe_content"
                except Exception as exc:
                    raw = {}
                    safe_excerpt = ""
                    valid = False
                    rejection = f"provider_error:{type(exc).__name__}"
                return MoAProposal(
                    proposal_id=proposal_id,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    strategy_execution_id=execution_id,
                    layer_index=current_layer.layer_index,
                    participant_id=candidate.candidate_id,
                    provider_id=candidate.provider_id,
                    model_family=candidate.model_family,
                    deployment_id=candidate.deployment_id,
                    region=candidate.region,
                    failure_domain=candidate.failure_domain,
                    proposal_reference=str(raw.get("proposal_reference", f"error://{proposal_id}")),
                    safe_excerpt=safe_excerpt,
                    evidence_references=tuple(raw.get("evidence_references", ())),
                    predecessor_proposal_ids=predecessors,
                    valid=valid,
                    rejection_reason=rejection,
                    tokens=int(raw.get("tokens", 0)),
                    latency_ms=int(raw.get("latency_ms", 0)),
                    cost_usd=float(raw.get("cost_usd", 0)),
                    quality_score=int(raw.get("quality_score", 0)),
                    attempt=1,
                    idempotency_key=(
                        f"{execution_id}:{current_layer.layer_index}:"
                        f"{candidate.deployment_id}:1"
                    ),
                )

            proposals = tuple(
                await asyncio.gather(
                    *(run(by_deployment[item]) for item in layer.deployment_ids)
                )
            )
            stored = tuple(
                [await self._repository.save_proposal(item) for item in proposals]
            )
            cost = state.total_cost_usd + sum(item.cost_usd for item in stored)
            if cost > maximum_cost_usd:
                state = state.model_copy(
                    update={"phase": "failed", "terminal_reason": "cost_limit"}
                )
                await self._checkpoints.save(state)
                return state
            quorum = evaluate_quorum(stored, required=layer.quorum)
            valid = tuple(item for item in stored if item.valid)
            if not quorum.met:
                if not allow_degraded or not valid:
                    state = state.model_copy(
                        update={"phase": "failed", "terminal_reason": "quorum_failed"}
                    )
                    await self._checkpoints.save(state)
                    return state
                best = min(
                    valid,
                    key=lambda item: (-item.quality_score, item.proposal_id),
                )
                state = state.model_copy(
                    update={
                        "phase": "completed",
                        "safe_output": best.safe_excerpt,
                        "degraded": True,
                        "total_cost_usd": cost,
                    }
                )
                await self._checkpoints.save(state)
                return state
            aggregation_input = build_aggregation_input(
                stored, maximum_characters=16_000
            )
            state = state.model_copy(update={"phase": "layer_aggregating"})
            await self._checkpoints.save(state)
            raw_aggregate = dict(
                await invoke(aggregate, layer.layer_index, aggregation_input)
            )
            aggregate_reference = str(raw_aggregate["aggregate_reference"])
            output = str(raw_aggregate["safe_output"])
            state = state.model_copy(
                update={
                    "completed_layers": state.completed_layers + 1,
                    "aggregate_references": (
                        *state.aggregate_references,
                        aggregate_reference,
                    ),
                    "safe_output": output[:8_000],
                    "total_cost_usd": cost,
                }
            )
            await self._checkpoints.save(state)
            prior_ids = tuple(item.proposal_id for item in valid)
        completed = state.model_copy(update={"phase": "completed"})
        await self._checkpoints.save(completed)
        return completed


__all__ = ["MoAAdapter", "MoAExecutionState", "MoARuntime"]
