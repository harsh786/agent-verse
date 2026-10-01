"""CORE-08: debate / supervisor submission modes run on the tenant's BYOK provider.

They took ``app.state._app_provider`` unconditionally, so a tenant with its own
LLM key had those calls sent to — and paid by — the platform provider. Both
modes now make no LLM call in the request at all (CORE-07, CORE-30): the goal's
own graph runs them with the goal's BYOK-resolved, charged planner.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.goals import router as goals_router
from app.services.llm_config_store import LLMConfigReadError
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-byok", plan=PlanTier.PROFESSIONAL, api_key_id="kid-b")
_KEY = "ak_test_byok"


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


def _post(app: FastAPI, mode: str) -> Any:
    client = TestClient(app, raise_server_exceptions=False)
    return client.post(
        "/goals", json={"goal": "Pick a database", "workflow_mode": mode}, headers={"X-API-Key": _KEY}
    )


@pytest.mark.parametrize(
    ("mode", "target"),
    [
        ("supervisor", "app.agent.supervisor.SupervisorAgent"),
        ("debate", "app.agent.debate.DebateOrchestrator"),
    ],
)
def test_pattern_mode_leaves_provider_resolution_to_the_goal(mode: str, target: str) -> None:
    """CORE-07 / CORE-30: no in-request decomposition or debate, so no BYOK read
    here; the goal's own run resolves the tenant provider (and fails the goal
    honestly when the BYOK config is unusable) — never the platform provider."""
    store = _Store(error=LLMConfigReadError("db down"))
    platform = AsyncMock()
    ran = MagicMock()
    with patch(target, ran):
        resp = _post(_app(store, platform), mode)
    assert resp.status_code == 202, resp.text
    assert store.strict_calls == 0
    ran.assert_not_called()
    platform.complete.assert_not_awaited()
