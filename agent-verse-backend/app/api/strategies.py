"""Tenant-authorized strategy catalogue, readiness, and certification APIs."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request, status

from app.orchestration.execution_drivers import strategy_availability
from app.orchestration.strategy_certification import CertificationEvaluator, RuntimeEvidence
from app.orchestration.strategy_evidence import StrategyEvidenceRecorder, StrategyRunEvidence
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


def _recorder(request: Request) -> StrategyEvidenceRecorder | None:
    recorder = getattr(request.app.state, "strategy_evidence", None)
    return recorder if isinstance(recorder, StrategyEvidenceRecorder) else None


def _certifying(rows: list[StrategyRunEvidence]) -> tuple[RuntimeEvidence, ...]:
    return tuple(evidence for evidence in (item.as_runtime_evidence() for item in rows) if evidence)


async def _evidence(
    request: Request, tenant_id: str, capability: StrategyCapability
) -> list[StrategyRunEvidence]:
    """The tenant's recorded runtime evidence for *capability* (none if no recorder)."""
    recorder = _recorder(request)
    return [] if recorder is None else await recorder.list_evidence(tenant_id, capability)


def _catalogue_item(
    capability: StrategyCapability,
    certification: CertificationEvaluator,
    rows: list[StrategyRunEvidence],
    *,
    ready: bool,
    readiness_reasons: tuple[str, ...],
) -> dict[str, Any]:
    # Derived from recorded evidence only — never "certified" without it.
    derived = certification.derive_state(capability, _certifying(rows))
    # Registered adapter logic is not "runnable": only a strategy a driver actually runs
    # is available (and can be ready); the rest is experimental / cross-cutting / n.a.
    availability = strategy_availability(capability)
    runnable = availability.availability == "available"
    reasons = list(readiness_reasons)
    if not runnable and availability.reason:
        reasons.append(availability.reason)
    return {
        "strategy_id": capability.strategy_id,
        "family": capability.spec.family.value,
        "execution_tier": capability.execution_tier.value,
        "adapter_version": capability.adapter_version,
        "state_schema_version": capability.state_schema_version,
        "derived_state": derived.state.value,
        "availability": availability.availability,
        "execution_driver": availability.execution_driver,
        "ready": ready and runnable,
        "readiness_reasons": reasons,
        "certified": derived.certified,
        "runtime_evidence": StrategyEvidenceRecorder.summarize(rows),
        "required_dependencies": list(capability.readiness_requirements),
        "default_limits": capability.default_limits.model_dump(mode="json"),
    }


@router.get("")
async def list_strategies(request: Request) -> dict[str, Any]:
    tenant = _tenant(request)
    certification: CertificationEvaluator = request.app.state.strategy_certification
    readiness: ReadinessEvaluator = request.app.state.strategy_readiness
    capabilities = sorted(_registry(request).list_all(), key=lambda value: value.strategy_id)
    recorder = _recorder(request)
    evidence = (
        {}
        if recorder is None
        else await recorder.evidence_by_strategy(tenant.tenant_id, capabilities)
    )
    strategies = []
    for item in capabilities:
        decision = await readiness.evaluate(item, production=True)
        strategies.append(
            _catalogue_item(
                item,
                certification,
                evidence.get(item.strategy_id, []),
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
    decision = await request.app.state.strategy_readiness.evaluate(capability, production=True)
    return _catalogue_item(
        capability,
        request.app.state.strategy_certification,
        await _evidence(request, _tenant(request).tenant_id, capability),
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
    availability = strategy_availability(capability)
    runnable = availability.availability == "available"
    reason_codes = [*decision.blocking_reasons, *decision.degraded_reasons]
    if not runnable and availability.reason:
        reason_codes.append(availability.reason)
    return {
        "strategy_id": capability.strategy_id,
        "adapter_version": capability.adapter_version,
        "availability": availability.availability,
        "ready": decision.ready and runnable,
        "degraded": bool(decision.degraded_reasons),
        "reason_codes": reason_codes,
        "checked_at": decision.checked_at,
    }


@router.get("/{strategy_id}/certification")
async def get_strategy_certification(request: Request, strategy_id: str) -> dict[str, Any]:
    capability = _capability(request, strategy_id)
    evaluator: CertificationEvaluator = request.app.state.strategy_certification
    rows = await _evidence(request, _tenant(request).tenant_id, capability)
    decision = evaluator.derive_state(capability, _certifying(rows))
    return {
        "strategy_id": capability.strategy_id,
        "adapter_version": capability.adapter_version,
        "derived_state": decision.state.value,
        "certified": decision.certified,
        "missing_evidence": list(decision.missing_or_invalid_categories),
        "runtime_evidence": StrategyEvidenceRecorder.summarize(rows),
    }
