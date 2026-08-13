"""Tenant-authorized strategy catalogue, readiness, and certification APIs."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request, status

from app.orchestration.strategy_certification import CertificationEvaluator
from app.orchestration.strategy_readiness import ReadinessEvaluator
from app.orchestration.strategy_registry import StrategyCapability, StrategyRegistry

router = APIRouter(prefix="/strategies", tags=["strategies"])


def _tenant(request: Request) -> Any:
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")
    return tenant


def _registry(request: Request) -> StrategyRegistry:
    return cast(StrategyRegistry, request.app.state.strategy_registry)


def _capability(request: Request, strategy_id: str) -> StrategyCapability:
    _tenant(request)
    try:
        return _registry(request).resolve(strategy_id).capability
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Strategy not found") from exc


def _catalogue_item(
    capability: StrategyCapability,
    certification: CertificationEvaluator,
    *,
    ready: bool,
    readiness_reasons: tuple[str, ...],
) -> dict[str, Any]:
    derived = certification.derive_state(capability, ())
    return {
        "strategy_id": capability.strategy_id,
        "family": capability.spec.family.value,
        "execution_tier": capability.execution_tier.value,
        "adapter_version": capability.adapter_version,
        "state_schema_version": capability.state_schema_version,
        "derived_state": derived.state.value,
        "ready": ready,
        "readiness_reasons": list(readiness_reasons),
        "certified": derived.certified,
        "required_dependencies": list(capability.readiness_requirements),
        "default_limits": capability.default_limits.model_dump(mode="json"),
    }


@router.get("")
async def list_strategies(request: Request) -> dict[str, Any]:
    _tenant(request)
    certification: CertificationEvaluator = request.app.state.strategy_certification
    readiness: ReadinessEvaluator = request.app.state.strategy_readiness
    strategies = []
    for item in sorted(_registry(request).list_all(), key=lambda value: value.strategy_id):
        decision = await readiness.evaluate(item, production=True)
        strategies.append(
            _catalogue_item(
                item,
                certification,
                ready=decision.ready,
                readiness_reasons=(
                    *decision.blocking_reasons,
                    *decision.degraded_reasons,
                ),
            )
        )
    return {"strategies": strategies}


@router.get("/{strategy_id}")
async def get_strategy(request: Request, strategy_id: str) -> dict[str, Any]:
    capability = _capability(request, strategy_id)
    decision = await request.app.state.strategy_readiness.evaluate(
        capability, production=True
    )
    return _catalogue_item(
        capability,
        request.app.state.strategy_certification,
        ready=decision.ready,
        readiness_reasons=(
            *decision.blocking_reasons,
            *decision.degraded_reasons,
        ),
    )


@router.get("/{strategy_id}/readiness")
async def get_strategy_readiness(request: Request, strategy_id: str) -> dict[str, Any]:
    capability = _capability(request, strategy_id)
    evaluator: ReadinessEvaluator = request.app.state.strategy_readiness
    decision = await evaluator.evaluate(capability, production=True)
    return {
        "strategy_id": capability.strategy_id,
        "adapter_version": capability.adapter_version,
        "ready": decision.ready,
        "degraded": bool(decision.degraded_reasons),
        "reason_codes": [*decision.blocking_reasons, *decision.degraded_reasons],
        "checked_at": decision.checked_at,
    }


@router.get("/{strategy_id}/certification")
async def get_strategy_certification(request: Request, strategy_id: str) -> dict[str, Any]:
    capability = _capability(request, strategy_id)
    evaluator: CertificationEvaluator = request.app.state.strategy_certification
    decision = evaluator.derive_state(capability, ())
    return {
        "strategy_id": capability.strategy_id,
        "adapter_version": capability.adapter_version,
        "derived_state": decision.state.value,
        "certified": decision.certified,
        "missing_evidence": list(decision.missing_or_invalid_categories),
    }
