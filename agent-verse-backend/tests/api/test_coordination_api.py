from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.api.coordination import router
from app.coordination.state_machines import InvalidTransitionError
from app.coordination.store import AcceptedTransition, CoordinationSessionRecord


class Service:
    async def create_session(self, tenant, admission):
        assert tenant.tenant_id == "tenant-1"
        return CoordinationSessionRecord(
            session_id="session-1",
            tenant_id=tenant.tenant_id,
            state="pending",
            next_sequence=1,
            version=1,
        )

    async def get_session(self, tenant, session_id):
        if session_id == "missing":
            raise KeyError(session_id)
        return CoordinationSessionRecord(
            session_id=session_id,
            tenant_id=tenant.tenant_id,
            state="paused",
            next_sequence=4,
            version=3,
        )

    async def cancel_session(self, tenant, session_id, **values):
        if session_id == "terminal":
            raise InvalidTransitionError("invalid transition")
        return AcceptedTransition(
            event_id="event-cancel",
            session_id=session_id,
            sequence=3,
            state="cancelling",
            version=values["expected_version"] + 1,
            idempotency_key=values["idempotency_key"],
        )

    async def resume_session(self, tenant, session_id, **values):
        return AcceptedTransition(
            event_id="event-resume",
            session_id=session_id,
            sequence=4,
            state="active",
            version=values["expected_version"] + 1,
            idempotency_key=values["idempotency_key"],
        )


def _app() -> FastAPI:
    application = FastAPI()
    application.state.coordination_service = Service()

    @application.middleware("http")
    async def tenant(request: Request, call_next):
        request.state.tenant = SimpleNamespace(
            tenant_id="tenant-1", api_key_id="key-1"
        )
        return await call_next(request)

    application.include_router(router)
    return application


async def test_create_session_is_async_and_returns_location() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/coordination/sessions",
            headers={"Idempotency-Key": "create-1"},
            json={
                "civilization_id": "civ-1",
                "goal_id": "goal-1",
                "policy_snapshot": {},
                "budget_snapshot": {},
            },
        )

    assert response.status_code == 202
    assert response.headers["location"].endswith("/sessions/session-1")
    assert response.json()["version"] == 1


async def test_read_cancel_resume_and_conflict_contracts() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        read = await client.get("/api/v1/coordination/sessions/session-1")
        cancel = await client.post(
            "/api/v1/coordination/sessions/session-1/cancel",
            headers={"Idempotency-Key": "cancel-1"},
            json={"expected_version": 3},
        )
        resume = await client.post(
            "/api/v1/coordination/sessions/session-1/resume",
            headers={"Idempotency-Key": "resume-1"},
            json={"expected_version": 3},
        )
        missing = await client.get("/api/v1/coordination/sessions/missing")
        conflict = await client.post(
            "/api/v1/coordination/sessions/terminal/cancel",
            headers={"Idempotency-Key": "cancel-2"},
            json={"expected_version": 3},
        )

    assert read.status_code == 200
    assert cancel.status_code == resume.status_code == 202
    assert cancel.headers["location"].endswith("/sessions/session-1")
    assert missing.status_code == 404
    assert conflict.status_code == 409


async def test_commands_require_idempotency_header() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/coordination/sessions/session-1/cancel",
            json={"expected_version": 3},
        )
    assert response.status_code == 422
