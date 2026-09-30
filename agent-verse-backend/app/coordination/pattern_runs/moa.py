"""Mixture-of-Agents driver: layered proposals, unique-deployment quorum, aggregation."""

from __future__ import annotations

import uuid
from typing import Any

from app.coordination.moa.adapter import MoARuntime
from app.coordination.moa.aggregator import AggregationInput
from app.coordination.moa.layer_planner import LayerPlanner
from app.coordination.moa.models import ModelCandidate
from app.coordination.pattern_runs.context import RunContext, RunOutcome

_EST_CALL_COST_USD = 0.01


def _candidate(name: str, *, provider_id: str, model: str) -> ModelCandidate:
    # Proposers are distinct personas on the tenant's configured provider: their
    # deployment ids (and failure domains) are per persona, so quorum counts
    # independent proposals — the view reports this as single-provider diversity.
    return ModelCandidate(
        candidate_id=name,
        provider_id=provider_id,
        model_family=model or "default",
        deployment_id=f"{model or 'default'}:{name}",
        region="default",
        failure_domain=name,
        healthy=True,
        context_limit=100_000,
        estimated_cost_usd=_EST_CALL_COST_USD,
        estimated_latency_ms=1_000,
        capabilities=frozenset({"general"}),
    )


async def run_moa(ctx: RunContext, *, repository: Any, provider: Any) -> RunOutcome:
    provider_id = type(provider).__name__
    model = str(getattr(provider, "default_model", "") or getattr(provider, "_default_model", ""))
    proposers = tuple(
        _candidate(name, provider_id=provider_id, model=model) for name in ctx.participants
    )
    aggregator = _candidate("aggregator", provider_id=provider_id, model=model)
    layers = max(1, min(int(ctx.options.get("layers", 2)), 3))
    maximum_cost = float(ctx.options.get("max_cost_usd", 1.0))
    plan = LayerPlanner().plan(
        candidates=(*proposers, aggregator),
        layers=layers,
        fan_out=len(proposers),
        quorum=max(1, len(proposers) - 1),
        aggregator_id=aggregator.deployment_id,
        maximum_cost_usd=maximum_cost,
        deadline=ctx.deadline,
        replacement_waves=0,
    )
    # The planner derives layer ids from (index, aggregator) only; make them unique
    # per tenant execution so persisted layer rows never collide across runs.
    plan = plan.model_copy(
        update={
            "layers": tuple(
                layer.model_copy(
                    update={
                        "layer_id": uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"{ctx.tenant_id}:{ctx.execution_id}:moa-layer:{layer.layer_index}",
                        ).hex
                    }
                )
                for layer in plan.layers
            )
        }
    )
    aggregates: dict[int, str] = {}

    async def propose(
        layer_index: int, candidate: ModelCandidate, context: dict[str, Any]
    ) -> dict[str, Any]:
        prior = aggregates.get(layer_index - 1)
        refinement = f"\nPrevious layer's aggregate answer to improve on:\n{prior}" if prior else ""
        answer = await ctx.llm.text(
            f"You are proposer '{candidate.candidate_id}' in a Mixture-of-Agents layer "
            f"{layer_index}. Give your best independent answer.\n"
            f"Objective: {context['objective']}{refinement}",
            step=f"layer-{layer_index}-{candidate.candidate_id}",
        )
        return {
            "safe_excerpt": answer[:2_000],
            "proposal_reference": f"moa://{ctx.execution_id}/{layer_index}/{candidate.candidate_id}",
            "evidence_references": (),
            "cost_usd": _EST_CALL_COST_USD,
        }

    async def aggregate(layer_index: int, aggregation: AggregationInput) -> dict[str, Any]:
        answer = await ctx.llm.text(
            f"You are the aggregator. Synthesize one answer for the objective from these "
            f"attributed proposals, keeping what is correct.\nObjective: {ctx.objective}\n"
            f"{aggregation.prompt}",
            step=f"aggregate-{layer_index}",
            max_tokens=1_200,
        )
        aggregates[layer_index] = answer
        await ctx.say(
            "aggregator",
            f"[moa layer {layer_index}] {answer}",
            step=f"moa:aggregate:{layer_index}",
            message_type="decision",
        )
        return {
            "aggregate_reference": f"moa://{ctx.execution_id}/{layer_index}/aggregate",
            "safe_output": answer,
        }

    state = await MoARuntime(repository=repository, checkpoint_store=ctx.checkpoints).execute(
        tenant_id=ctx.tenant_id,
        session_id=ctx.session_id,
        execution_id=ctx.execution_id,
        objective=ctx.objective,
        plan=plan,
        candidates=(*proposers, aggregator),
        propose=propose,
        aggregate=aggregate,
        maximum_cost_usd=maximum_cost,
        allow_degraded=bool(ctx.options.get("allow_degraded", False)),
    )
    return RunOutcome(
        phase=state.phase,
        terminal_reason=state.terminal_reason,
        safe_output=state.safe_output,
        view={
            "layers": layers,
            "completed_layers": state.completed_layers,
            "degraded": state.degraded,
            "diversity": "single_provider_personas",
            "proposers": [item.candidate_id for item in proposers],
        },
    )


__all__ = ["run_moa"]
