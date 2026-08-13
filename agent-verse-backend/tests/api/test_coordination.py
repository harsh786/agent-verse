from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.api.coordination import router
from app.coordination.store import AcceptedTransition, CoordinationSessionRecord


class Service:
    async def create_session(self, tenant, admission):
        assert tenant.tenant_id == "tenant-1"
        assert admission.authorization.actor_id == "key-1"
        return CoordinationSessionRecord(
            session_id="session-1",
            tenant_id="tenant-1",
            state="pending",
            next_sequence=1,
            version=1,
        )

    async def start_session(self, tenant, session_id, **values):
        return AcceptedTransition(
            event_id="event-1",
            session_id=session_id,
            sequence=1,
            state="active",
            version=2,
            idempotency_key=values["idempotency_key"],
        )


def app(*, authenticated: bool) -> FastAPI:
    application = FastAPI()
    application.state.coordination_service = Service()

    @application.middleware("http")
    async def tenant(request: Request, call_next):
        if authenticated:
            request.state.tenant = SimpleNamespace(
                tenant_id="tenant-1", api_key_id="key-1"
            )
        return await call_next(request)

    application.include_router(router)
    return application


async def test_create_session_is_tenant_authorized_and_versioned() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app(authenticated=True)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/coordination/v1/sessions",
            json={
                "civilization_id": "civ-1",
                "goal_id": "goal-1",
                "policy_snapshot": {"version": "p1"},
                "budget_snapshot": {"ceiling": 10},
            },
        )

    assert response.status_code == 201
    assert response.json()["session_id"] == "session-1"


async def test_coordination_api_rejects_missing_tenant() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app(authenticated=False)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/coordination/v1/sessions",
            json={
                "civilization_id": "civ-1",
                "goal_id": "goal-1",
                "policy_snapshot": {},
                "budget_snapshot": {},
            },
        )

    assert response.status_code == 401


async def test_start_session_requires_expected_version_and_idempotency() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app(authenticated=True)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/coordination/v1/sessions/session-1/start",
            json={"expected_version": 1, "idempotency_key": "start-1"},
        )

    assert response.status_code == 200
    assert response.json()["state"] == "active"
    assert response.json()["version"] == 2
