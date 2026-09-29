from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.goals import router as goals_router
from app.api.strategies import router as strategies_router
from app.orchestration.strategy_certification import CertificationEvaluator
from app.orchestration.strategy_readiness import ReadinessEvaluator
from app.orchestration.strategy_registry import build_default_registry
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

TENANT = TenantContext("tenant-1", PlanTier.PROFESSIONAL, "key-1")


def client(service: AsyncMock | None = None) -> TestClient:
    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        return TENANT if key == "valid" else None

    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    app.include_router(goals_router)
    app.include_router(strategies_router)
    app.state.goal_service = service or AsyncMock()
    app.state.strategy_registry = build_default_registry()
    app.state.strategy_readiness = ReadinessEvaluator()
    app.state.strategy_certification = CertificationEvaluator()
    return TestClient(app, raise_server_exceptions=False)


def test_catalogue_and_detail_expose_versioned_derived_metadata() -> None:
    response = client().get("/strategies", headers={"X-API-Key": "valid"})

    assert response.status_code == 200
    react = next(item for item in response.json()["strategies"] if item["strategy_id"] == "react")
    assert react["adapter_version"] == "1.0.0"
    assert react["state_schema_version"] == 1
    assert react["derived_state"] in {"partial", "implemented", "certified"}
    assert isinstance(react["required_dependencies"], list)


def test_readiness_and_certification_return_safe_reason_codes() -> None:
    api = client()
    readiness = api.get("/strategies/react/readiness", headers={"X-API-Key": "valid"})
    certification = api.get(
        "/strategies/react/certification", headers={"X-API-Key": "valid"}
    )

    assert readiness.status_code == 200
    assert readiness.json()["ready"] is False
    assert readiness.json()["reason_codes"]
    assert certification.status_code == 200
    assert certification.json()["derived_state"] in {"partial", "implemented"}


def test_goal_submission_accepts_additive_strategy_controls() -> None:
    service = AsyncMock()
    service.submit_goal.return_value = {"id": "goal-1", "status": "planning"}
    response = client(service).post(
        "/goals",
        headers={"X-API-Key": "valid"},
        json={
            "goal": "research safely",
            "strategy_override": "react",
            "auxiliary_strategies": ["reflection"],
            "pattern_limits": {
                "calls": 5,
                "nodes": 10,
                "edges": 15,
                "depth": 3,
                "fan_out": 2,
                "rounds": 2,
                "tokens": 5000,
                "duration_seconds": 60,
                "cost_usd": 1.0,
            },
        },
    )

    assert response.status_code == 202
    execution_context = service.submit_goal.call_args.kwargs["execution_context"]
    assert execution_context["strategy_runtime"]["primary_strategy"] == "react"
    assert execution_context["strategy_runtime"]["limits"]["calls"] == 5


def test_unknown_override_is_sanitized_422_and_existing_body_remains_valid() -> None:
    service = AsyncMock()
    service.submit_goal.return_value = {"id": "goal-1", "status": "planning"}
    api = client(service)

    invalid = api.post(
        "/goals",
        headers={"X-API-Key": "valid"},
        json={"goal": "goal", "strategy_override": "../../secret"},
    )
    existing = api.post(
        "/goals",
        headers={"X-API-Key": "valid"},
        json={"goal": "existing request"},
    )

    assert invalid.status_code == 422
    assert "secret" not in invalid.text
    assert existing.status_code == 202


def test_catalogue_marks_non_admitted_strategies_experimental() -> None:
    """Many strategies are registered DISTRIBUTED/IMPLEMENTED but the executor admits only
    supervisor / goal_tree / debate — the catalogue must not present the rest as runnable."""
    from app.orchestration.strategy_executor import default_distributed_admission
    from app.orchestration.strategy_readiness import DependencyProbeResult

    api = client()
    # Even with every dependency probe green, a strategy with no driver is not ready.
    evaluator = api.app.state.strategy_readiness  # type: ignore[attr-defined]
    for capability in build_default_registry().list_all():
        for dependency in capability.readiness_requirements:
            if dependency not in evaluator._probes and dependency != "registry_contract":
                evaluator.register(dependency, DependencyProbeResult.ready)
    catalogue = api.get("/strategies", headers={"X-API-Key": "valid"}).json()["strategies"]
    by_id = {item["strategy_id"]: item for item in catalogue}

    for item in catalogue:
        if item["execution_tier"] != "distributed":
            continue
        request = type("R", (), {"strategy_id": item["strategy_id"]})()
        admitted, _ = default_distributed_admission(request)  # type: ignore[arg-type]
        expected = "available" if admitted else "experimental"
        assert item["availability"] == expected, item["strategy_id"]
        if not admitted:
            assert item["ready"] is False
            assert "strategy_execution_not_implemented" in item["readiness_reasons"]

    assert by_id["supervisor"]["execution_driver"] == "strategy_runner"
    assert by_id["react"]["availability"] == "available"
    assert by_id["react"]["ready"] is True
    assert by_id["rewoo"]["availability"] == "experimental"
    assert by_id["rewoo"]["ready"] is False
    assert by_id["guardrails"]["availability"] == "cross_cutting"
    assert by_id["prospective_memory"]["availability"] == "not_available"

    readiness = api.get("/strategies/magentic/readiness", headers={"X-API-Key": "valid"})
    assert readiness.json()["ready"] is False
    assert readiness.json()["availability"] == "experimental"


def test_override_without_goal_execution_driver_is_422() -> None:
    """ReWOO / CodeAct / magentic have adapter logic but no goal driver: accepting them
    would run a plain ReAct loop under their name."""
    service = AsyncMock()
    service.submit_goal.return_value = {"id": "goal-1", "status": "planning"}
    api = client(service)
    for strategy_id in ("rewoo", "codeact", "magentic"):
        response = api.post(
            "/goals",
            headers={"X-API-Key": "valid"},
            json={"goal": "goal", "strategy_override": strategy_id},
        )
        assert response.status_code == 422, strategy_id
    service.submit_goal.assert_not_called()
    accepted = api.post(
        "/goals",
        headers={"X-API-Key": "valid"},
        json={"goal": "goal", "strategy_override": "supervisor"},
    )
    assert accepted.status_code == 202


def test_goal_explain_is_tenant_authorized_and_excludes_private_reasoning() -> None:
    service = AsyncMock()
    service.get_goal.return_value = {
        "goal_id": "goal-1",
        "execution_context": {
            "runtime_profile": {
                "profile_version": 2,
                "primary_strategy": {"strategy_id": "react", "adapter_version": "1.0.0"},
                "auxiliary_strategies": [],
                "rejected_alternatives": [],
                "effective_limits": {"calls": 5},
                "readiness_snapshot_ref": "readiness-1",
            },
            "safe_trace": {"status": "succeeded"},
            "private_reasoning": "must never escape",
            "cost_usd": 0.25,
        },
    }
    api = client(service)

    unauthorized = api.get("/goals/goal-1/explain")
    response = api.get("/goals/goal-1/explain", headers={"X-API-Key": "valid"})

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert "private_reasoning" not in response.text
    assert response.json()["profile_version"] == 2
