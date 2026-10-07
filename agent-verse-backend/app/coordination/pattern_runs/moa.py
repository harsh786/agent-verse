"""Mixture-of-Agents driver: layered proposals, unique-deployment quorum, aggregation."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any

import structlog

from app.coordination.moa.adapter import MoARuntime
from app.coordination.moa.aggregator import AggregationInput
from app.coordination.moa.layer_planner import LayerPlanner
from app.coordination.moa.models import ModelCandidate
from app.coordination.pattern_runs.context import RunContext, RunOutcome
from app.coordination.pattern_runs.llm import provider_model

_EST_CALL_COST_USD = 0.01

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class _Deployment:
    provider: Any
    provider_id: str
    model: str


def _provider_id(provider: Any) -> str:
    return str(getattr(provider, "_agentverse_provider_type", "") or type(provider).__name__)


def proposer_pool(
    primary: Any, configured: list[Any] | None, models: list[str] | None
) -> list[_Deployment]:
    """Distinct (provider, model) deployments available to MoA proposers.

    Sources, de-duplicated by (provider id, model): each configured provider at
    its default model (``app.state.moa_providers``), then the run's
    ``proposer_models`` requested through the primary provider (model-routing
    backends such as OpenRouter or the on-prem dispatcher), then the primary.
    """
    pool: dict[tuple[str, str], _Deployment] = {}
    for provider in configured or ():
        model = provider_model(provider)
        pool.setdefault(
            (_provider_id(provider), model), _Deployment(provider, _provider_id(provider), model)
        )
    for model in models or ():
        if model.strip():
            key = (_provider_id(primary), model.strip())
            pool.setdefault(key, _Deployment(primary, key[0], key[1]))
    # The run's own provider proposes on the coordination role's model (saved
    # Model Registry order first), not on its env default.
    from app.ai_router.role_preference import resolve_role_model

    primary_model = resolve_role_model("coordination_moa", provider=primary) or provider_model(
        primary
    )
    primary_key = (_provider_id(primary), primary_model)
    pool.setdefault(primary_key, _Deployment(primary, *primary_key))
    return list(pool.values())


def configured_provider_pool() -> list[Any]:
    """Every configured real LLM provider (each at its default model), for MoA proposers."""
    from app.providers.fake import FakeProvider
    from app.providers.registry import _detect_providers, _instantiate_provider

    pool: list[Any] = []
    for cfg in _detect_providers():
        try:
            provider = _instantiate_provider(cfg)
        except Exception as exc:
            logger.debug("moa_provider_unavailable", type=cfg.provider_type, error=str(exc)[:80])
            continue
        if provider is None or isinstance(provider, FakeProvider):
            continue
        if not getattr(provider, "_agentverse_provider_type", None):
            provider._agentverse_provider_type = cfg.provider_type
        pool.append(provider)
    return pool


def default_proposer_models() -> list[str]:
    """Operator default for ``proposer_models`` (comma-separated MOA_PROPOSER_MODELS)."""
    return [
        item.strip() for item in os.getenv("MOA_PROPOSER_MODELS", "").split(",") if item.strip()
    ]


def _candidate(name: str, deployment: _Deployment) -> ModelCandidate:
    return ModelCandidate(
        candidate_id=name,
        provider_id=deployment.provider_id,
        model_family=deployment.model or "default",
        deployment_id=f"{deployment.provider_id}:{deployment.model or 'default'}:{name}",
        region="default",
        failure_domain=f"{deployment.provider_id}:{name}",
        healthy=True,
        context_limit=100_000,
        estimated_cost_usd=_EST_CALL_COST_USD,
        estimated_latency_ms=1_000,
        capabilities=frozenset({"general"}),
    )


async def run_moa(
    ctx: RunContext,
    *,
    repository: Any,
    provider: Any,
    configured_providers: list[Any] | None = None,
) -> RunOutcome:
    pool = proposer_pool(
        provider,
        configured_providers,
        list(ctx.options.get("proposer_models") or ()) or default_proposer_models(),
    )
    assignment = {name: pool[index % len(pool)] for index, name in enumerate(ctx.participants)}
    distinct_models = {(d.provider_id, d.model) for d in assignment.values()}
    if len(distinct_models) == 1:
        # Only one model is configured: proposals are personas of the same model,
        # so they are not independent samples. Keep running, but say so.
        diversity = "single_model_personas"
        logger.warning(
            "moa_single_model_personas",
            execution_id=ctx.execution_id,
            model=next(iter(distinct_models))[1],
            hint="configure several providers or proposer_models for real diversity",
        )
    elif len(distinct_models) == len(assignment):
        diversity = "multi_model"
    else:
        diversity = "partial_multi_model"
    proposer_models = {name: d.model for name, d in assignment.items()}
    await ctx.publish(
        {
            "type": "event",
            "event_type": "pattern_run.moa_diversity.v1",
            "payload": {
                "execution_id": ctx.execution_id,
                "diversity": diversity,
                "proposer_models": proposer_models,
            },
        }
    )
    await ctx.update_view(diversity=diversity, proposer_models=proposer_models)
    proposers = tuple(_candidate(name, assignment[name]) for name in ctx.participants)
    by_candidate = {item.candidate_id: assignment[item.candidate_id] for item in proposers}
    aggregator = _candidate(
        "aggregator", _Deployment(provider, _provider_id(provider), provider_model(provider))
    )
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
        deployment = by_candidate[candidate.candidate_id]
        answer = await ctx.llm.text(
            f"You are proposer '{candidate.candidate_id}' in a Mixture-of-Agents layer "
            f"{layer_index}. Give your best independent answer.\n"
            f"Objective: {context['objective']}{refinement}",
            step=f"layer-{layer_index}-{candidate.candidate_id}",
            provider=deployment.provider,
            model=deployment.model or None,
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
            "diversity": diversity,
            "proposer_models": proposer_models,
            "proposers": [item.candidate_id for item in proposers],
        },
    )


__all__ = ["run_moa"]
