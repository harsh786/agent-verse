"""The goal ReadinessGate runs on real dependency health and blocks when it must.

Regression: ``_check_readiness`` fed the gate ``DependencyHealth.all_healthy()`` (a
constant) and nothing called it, so READINESS_GATE (default on) never blocked anything.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from app.core import runtime_flags
from app.core.errors import ServiceUnavailableError
from app.observability.health import HealthCheck, HealthRegistry
from app.runtime_readiness.dependency_health import DepStatus
from app.runtime_readiness.health_probe import collect_dependency_health
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(tenant_id="tenant-r", plan=PlanTier.PROFESSIONAL, api_key_id="k")


async def _ok() -> None:
    return None


async def _down() -> None:
    raise ConnectionError("connection refused")


async def _hang() -> None:
    await asyncio.sleep(5)


def _state(*checks: HealthCheck, provider: Any = None) -> SimpleNamespace:
    return SimpleNamespace(
        health=HealthRegistry(list(checks)),
        _app_provider=provider if provider is not None else object(),
        embedder=None,
    )


async def test_health_reflects_the_registry_checks() -> None:
    health = await collect_dependency_health(
        _state(HealthCheck("postgres", _down), HealthCheck("redis", _ok))
    )
    assert health.postgres is DepStatus.UNAVAILABLE
    assert health.redis is DepStatus.HEALTHY
    assert health.llm_provider is DepStatus.HEALTHY


async def test_unconfigured_dependency_is_unknown_not_down() -> None:
    health = await collect_dependency_health(SimpleNamespace(health=HealthRegistry([])))
    assert health.postgres is DepStatus.UNKNOWN
    assert health.redis is DepStatus.UNKNOWN


async def test_a_hanging_probe_times_out_as_unavailable() -> None:
    health = await collect_dependency_health(_state(HealthCheck("postgres", _hang)), timeout=0.05)
    assert health.postgres is DepStatus.UNAVAILABLE


async def test_missing_llm_provider_blocks_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr("app.core.config.get_provider_env", lambda _name: "")
    from app.providers.fake import FakeProvider

    health = await collect_dependency_health(_state(provider=FakeProvider()))
    assert health.llm_provider is DepStatus.UNAVAILABLE
    monkeypatch.setenv("ENVIRONMENT", "development")
    health = await collect_dependency_health(_state(provider=FakeProvider()))
    assert health.llm_provider is DepStatus.DEGRADED


def _svc(state: Any) -> GoalService:
    svc = GoalService()
    svc._app_state = state
    return svc


async def test_submission_is_refused_when_postgres_is_down() -> None:
    svc = _svc(_state(HealthCheck("postgres", _down)))
    with pytest.raises(ServiceUnavailableError) as info:
        await svc.submit_goal(goal="do work", priority="normal", dry_run=False, tenant_ctx=TENANT)
    assert info.value.code == "PLATFORM_NOT_READY"
    assert "postgres" in info.value.message
    # Refused before any record / slot was taken.
    assert svc._goals == {}


async def test_check_readiness_reports_blocking_dependency() -> None:
    ready, reason = await _svc(_state(HealthCheck("postgres", _down)))._check_readiness(
        None, tenant_ctx=TENANT
    )
    assert ready is False
    assert "postgres" in reason

    ready, reason = await _svc(_state(HealthCheck("postgres", _ok)))._check_readiness(
        None, tenant_ctx=TENANT
    )
    assert (ready, reason) == (True, "")


async def test_gate_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    patched = replace(runtime_flags.get_runtime_flags(), readiness_gate=False)
    monkeypatch.setattr(runtime_flags, "get_runtime_flags", lambda: patched)
    ready, _ = await _svc(_state(HealthCheck("postgres", _down)))._check_readiness(None)
    assert ready is True


async def test_tenant_byok_satisfies_the_llm_requirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr("app.core.config.get_provider_env", lambda _name: "")
    from app.providers.fake import FakeProvider

    svc = _svc(_state(provider=FakeProvider()))
    ready, reason = await svc._check_readiness(None, tenant_ctx=TENANT)
    assert ready is False and "llm_provider" in reason

    async def byok(_tenant_ctx: TenantContext) -> dict[str, Any]:
        return {"provider": "anthropic"}

    svc._resolve_tenant_llm_config = byok  # type: ignore[method-assign]
    ready, _ = await svc._check_readiness(None, tenant_ctx=TENANT)
    assert ready is True


async def test_readiness_error_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(*_: Any, **__: Any) -> Any:
        raise RuntimeError("probe bug")

    monkeypatch.setattr("app.runtime_readiness.health_probe.collect_dependency_health", broken)
    ready, reason = await _svc(_state())._check_readiness(None, tenant_ctx=TENANT)
    assert ready is False
    assert reason == "Readiness check failed: RuntimeError"
