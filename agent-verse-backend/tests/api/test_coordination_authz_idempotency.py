"""Regression: coordination session creation self-granted its permission and
discarded the Idempotency-Key.

* ``AuthorizationContext(permissions={"coordination:create"})`` was built for
  every caller, so ``CoordinationService._require`` could never deny — a viewer
  key could create sessions;
* ``POST /api/v1/coordination/sessions`` required an Idempotency-Key and then
  ``del``-ed it, so a client retry created a duplicate session.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.api.coordination import router
from app.coordination.service import CoordinationService, session_id_for_idempotency_key
from app.coordination.store import InMemoryCoordinationStore

_BODY = {
    "civilization_id": "civ-1",
    "goal_id": "goal-1",
    "policy_snapshot": {},
    "budget_snapshot": {},
}


def _app(**tenant: Any) -> FastAPI:
    app = FastAPI()
    app.state.coordination_service = CoordinationService(InMemoryCoordinationStore())

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        request.state.tenant = SimpleNamespace(
            tenant_id=tenant.get("tenant_id", "t1"),
            api_key_id="k",
            roles=tenant.get("roles", ()),
            scopes=tenant.get("scopes", ()),
        )
        return await call_next(request)

    app.include_router(router)
    return app


async def _post(app: FastAPI, key: str = "idem-1") -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.post(
            "/api/v1/coordination/sessions", json=_BODY, headers={"Idempotency-Key": key}
        )


@pytest.mark.parametrize("roles", [(), ("viewer",), ("approver",)])
async def test_non_operator_cannot_create(roles: tuple[str, ...]) -> None:
    assert (await _post(_app(roles=roles))).status_code == 403


async def test_scoped_key_without_create_scope_cannot_create() -> None:
    r = await _post(_app(roles=("admin",), scopes=("goals:read",)))
    assert r.status_code == 403


@pytest.mark.parametrize("roles", [("operator",), ("admin",)])
async def test_operator_or_admin_can_create(roles: tuple[str, ...]) -> None:
    assert (await _post(_app(roles=roles))).status_code == 202


async def test_retry_with_same_idempotency_key_returns_the_same_session() -> None:
    app = _app(roles=("operator",))
    first = (await _post(app, "retry-me")).json()
    second = (await _post(app, "retry-me")).json()
    other = (await _post(app, "different")).json()
    assert first["session_id"] == second["session_id"]
    assert other["session_id"] != first["session_id"]
    assert first["session_id"] == session_id_for_idempotency_key("t1", "retry-me")


def test_idempotency_ids_are_tenant_scoped_and_fit_the_column() -> None:
    a = session_id_for_idempotency_key("t1", "k")
    b = session_id_for_idempotency_key("t2", "k")
    assert a != b and len(a) == 32
