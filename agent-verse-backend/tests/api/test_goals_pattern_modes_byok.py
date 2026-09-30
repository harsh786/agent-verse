"""CORE-08: debate / supervisor submission modes run on the tenant's BYOK provider.

They took ``app.state._app_provider`` unconditionally, so a tenant with its own
LLM key had those calls sent to — and paid by — the platform provider,
bypassing its chosen vendor and data-handling choice. A tenant BYOK config that
is broken or unreadable now refuses the submission instead of falling back.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.goals import router as goals_router
from app.providers.guarded_completion import GuardedDecisionProvider
from app.providers.tenant_provider import TenantProviderError
from app.services.llm_config_store import LLMConfigReadError
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-byok", plan=PlanTier.PROFESSIONAL, api_key_id="kid-b")
_KEY = "ak_test_byok"
_CFG = {"provider": "anthropic", "encrypted_key": "enc", "model": "m"}


class _Store:
    def __init__(self, cfg: dict[str, Any] | None = None, error: Exception | None = None):
        self.cfg, self.error, self.strict_calls = cfg, error, 0

    async def get_config(self, tenant_id: str, *, strict: bool = False) -> Any:
        self.strict_calls += int(strict)
        if self.error is not None:
            raise self.error
        return self.cfg


def _app(store: _Store, platform: Any) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(goals_router)
    svc = AsyncMock()
    svc._check_budget_preflight = AsyncMock(return_value=None)
    svc.submit_goal.return_value = {"id": "g", "goal_id": "g", "status": "planning", "goal": "x"}
    app.state.goal_service = svc
    app.state._app_provider = platform
    app.state.llm_config_store = store
    return app


class _Orchestrator:
    captured: dict[str, Any] = {}

    def __init__(self, *, provider: Any, rounds: int) -> None:
        _Orchestrator.captured["provider"] = provider

    async def run(self, goal: str) -> Any:
        return MagicMock(winning_proposal="A", consensus_level=1.0, winning_agent="agent_1")


class _Supervisor:
    captured: dict[str, Any] = {}

    def __init__(self, *, planner_provider: Any, goal_service: Any, max_parallel: int) -> None:
        _Supervisor.captured["provider"] = planner_provider

    async def run(self, goal: str, tenant_ctx: Any) -> Any:
        return MagicMock(tasks=[])


def _post(app: FastAPI, mode: str) -> Any:
    client = TestClient(app, raise_server_exceptions=False)
    return client.post(
        "/goals", json={"goal": "Pick a database", "workflow_mode": mode}, headers={"X-API-Key": _KEY}
    )


@pytest.mark.parametrize(
    ("mode", "target", "fake"),
    [
        ("debate", "app.agent.debate.DebateOrchestrator", _Orchestrator),
        ("supervisor", "app.agent.supervisor.SupervisorAgent", _Supervisor),
    ],
)
def test_pattern_mode_uses_the_tenant_byok_provider(mode: str, target: str, fake: Any) -> None:
    tenant_provider, platform = MagicMock(name="tenant"), MagicMock(name="platform")
    store = _Store(cfg=_CFG)
    with (
        patch(target, fake),
        patch(
            "app.providers.tenant_provider.build_tenant_provider", return_value=tenant_provider
        ) as build,
    ):
        resp = _post(_app(store, platform), mode)

    assert resp.status_code == 202, resp.text
    provider = fake.captured["provider"]
    assert isinstance(provider, GuardedDecisionProvider)
    assert provider.inner is tenant_provider
    assert build.call_args.kwargs["tenant_id"] == _CTX.tenant_id
    assert store.strict_calls == 1  # strict read: "unknown" is not "no BYOK"


def test_tenant_without_byok_keeps_the_platform_provider() -> None:
    platform = MagicMock(name="platform")
    with patch("app.agent.debate.DebateOrchestrator", _Orchestrator):
        resp = _post(_app(_Store(cfg=None), platform), "debate")
    assert resp.status_code == 202, resp.text
    assert _Orchestrator.captured["provider"].inner is platform


def test_broken_byok_config_refuses_instead_of_using_the_platform() -> None:
    platform = MagicMock(name="platform")
    ran = MagicMock()
    with (
        patch("app.agent.debate.DebateOrchestrator", ran),
        patch(
            "app.providers.tenant_provider.build_tenant_provider",
            side_effect=TenantProviderError("decrypt failed"),
        ),
    ):
        resp = _post(_app(_Store(cfg=_CFG), platform), "debate")
    assert resp.status_code == 422, resp.text
    assert "decrypt failed" in resp.text
    ran.assert_not_called()


def test_unreadable_byok_store_is_a_503() -> None:
    ran = MagicMock()
    with patch("app.agent.supervisor.SupervisorAgent", ran):
        resp = _post(_app(_Store(error=LLMConfigReadError("db down")), MagicMock()), "supervisor")
    assert resp.status_code == 503, resp.text
    ran.assert_not_called()


def test_supervisor_failure_does_not_echo_the_exception_text() -> None:
    """CORE-07 (partial): the 500 used to carry str(exc) — internal detail."""

    class _Boom:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def run(self, goal: str, tenant_ctx: Any) -> Any:
            raise RuntimeError("secret-internal-dsn postgresql://user:pw@db/x")

    with patch("app.agent.supervisor.SupervisorAgent", _Boom):
        resp = _post(_app(_Store(cfg=None), MagicMock()), "supervisor")
    assert resp.status_code == 500
    assert "secret-internal-dsn" not in resp.text
    assert "postgresql://" not in resp.text
