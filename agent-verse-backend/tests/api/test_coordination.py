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


# ── Foreign / unknown sessions on the legacy start/complete commands ─────────
#
# Regression for the schema-driven cross-tenant sweep: tenant B posting to
# /coordination/v1/sessions/{A's id}/start|complete made the store raise
# KeyError (RLS hides A's row), which these two handlers never mapped — every
# probe was an unhandled 500. They must answer exactly like the canonical
# /api/v1 transitions: 404 for a session the caller cannot see, 409 for a
# stale version or an illegal transition.


def _real_app() -> FastAPI:
    from app.coordination.service import CoordinationService
    from app.coordination.store import InMemoryCoordinationStore

    application = FastAPI()
    application.state.coordination_service = CoordinationService(InMemoryCoordinationStore())

    @application.middleware("http")
    async def tenant(request: Request, call_next):
        tid = request.headers.get("x-tenant", "tenant-a")
        request.state.tenant = SimpleNamespace(tenant_id=tid, api_key_id=f"key-{tid}")
        return await call_next(request)

    application.include_router(router)
    return application


_CREATE = {
    "civilization_id": "civ-1",
    "goal_id": "goal-1",
    "policy_snapshot": {},
    "budget_snapshot": {},
}


async def test_start_and_complete_on_foreign_session_are_404_not_500() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_real_app(), raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        created = await client.post(
            "/coordination/v1/sessions", json=_CREATE, headers={"x-tenant": "tenant-a"}
        )
        assert created.status_code == 201
        session_id = created.json()["session_id"]

        body = {"expected_version": 1, "idempotency_key": "probe-1"}
        for command in ("start", "complete"):
            foreign = await client.post(
                f"/coordination/v1/sessions/{session_id}/{command}",
                json=body,
                headers={"x-tenant": "tenant-b"},
            )
            assert foreign.status_code == 404, (command, foreign.text)
            unknown = await client.post(
                f"/coordination/v1/sessions/{'0' * 32}/{command}",
                json=body,
                headers={"x-tenant": "tenant-a"},
            )
            assert unknown.status_code == 404, (command, unknown.text)

        # Tenant B's probes did not touch A's session: A can still start it at v1.
        started = await client.post(
            f"/coordination/v1/sessions/{session_id}/start",
            json={"expected_version": 1, "idempotency_key": "start-1"},
            headers={"x-tenant": "tenant-a"},
        )
        assert started.status_code == 200, started.text
        assert started.json()["state"] == "active"
        assert started.json()["version"] == 2


async def test_legacy_transitions_map_conflicts_to_409() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_real_app(), raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        created = await client.post("/coordination/v1/sessions", json=_CREATE)
        session_id = created.json()["session_id"]

        # pending -> completed is not a legal transition.
        illegal = await client.post(
            f"/coordination/v1/sessions/{session_id}/complete",
            json={"expected_version": 1, "idempotency_key": "complete-early"},
        )
        assert illegal.status_code == 409, illegal.text

        # Stale optimistic version.
        stale = await client.post(
            f"/coordination/v1/sessions/{session_id}/start",
            json={"expected_version": 7, "idempotency_key": "start-stale"},
        )
        assert stale.status_code == 409, stale.text

        ok = await client.post(
            f"/coordination/v1/sessions/{session_id}/start",
            json={"expected_version": 1, "idempotency_key": "start-ok"},
        )
        done = await client.post(
            f"/coordination/v1/sessions/{session_id}/complete",
            json={"expected_version": 2, "idempotency_key": "complete-ok"},
        )
        assert ok.status_code == 200 and done.status_code == 200, (ok.text, done.text)
        assert done.json()["state"] == "completed"
