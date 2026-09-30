from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.coordination_handoffs import router
from app.coordination.handoffs.repository import InMemoryHandoffRepository
from app.coordination.handoffs.service import HandoffService
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware


class Membership:
    async def active_member(self, _tenant: str, _civilization: str, agent: str) -> bool:
        return agent in {"source", "target"}

    async def connector_allowlist(
        self, _tenant: str, _civilization: str, _agent: str
    ) -> frozenset[str]:
        return frozenset({"shared"})


def _app() -> FastAPI:
    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        if key in {"a", "b"}:
            return TenantContext(
                tenant_id=f"tenant-{key}", plan=PlanTier.PROFESSIONAL, api_key_id=key
            )
        return None

    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    app.include_router(router)
    app.state.handoff_service = HandoffService(
        InMemoryHandoffRepository(), membership=Membership()
    )
    return app


def _create(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/v1/coordination/sessions/session-a/handoffs",
        headers={"X-API-Key": "a", "Idempotency-Key": "request-1"},
        json={
            "civilization_id": "civilization",
            "source_agent_id": "source",
            "target_agent_id": "target",
            "task_summary": "bounded task",
            "remaining_budget_usd": 1,
            "deadline": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "acceptance_token": "long-enough-secret",
        },
    )
    assert response.status_code == 201
    assert "acceptance_token_digest" not in response.json()
    return response.json()


def test_handoff_api_is_idempotent_tenant_and_session_scoped() -> None:
    client = TestClient(_app())
    created = _create(client)
    handoff_id = created["handoff_id"]
    duplicate = _create(client)
    assert duplicate["handoff_id"] == handoff_id
    assert client.get(
        f"/api/v1/coordination/sessions/session-a/handoffs/{handoff_id}",
        headers={"X-API-Key": "b"},
    ).status_code == 404
    assert client.get(
        f"/api/v1/coordination/sessions/wrong/handoffs/{handoff_id}",
        headers={"X-API-Key": "a"},
    ).status_code == 404


def test_handoff_api_accept_replay_conflict_and_missing_token() -> None:
    client = TestClient(_app())
    created = _create(client)
    url = (
        "/api/v1/coordination/sessions/session-a/handoffs/"
        f"{created['handoff_id']}/accept"
    )
    missing = client.post(
        url,
        headers={"X-API-Key": "a", "Idempotency-Key": "missing"},
        json={"expected_version": 1},
    )
    assert missing.status_code == 422
    accepted = client.post(
        url,
        headers={"X-API-Key": "a", "Idempotency-Key": "accept-1"},
        json={"expected_version": 1, "acceptance_token": "long-enough-secret"},
    )
    replay = client.post(
        url,
        headers={"X-API-Key": "a", "Idempotency-Key": "accept-1"},
        json={"expected_version": 1, "acceptance_token": "long-enough-secret"},
    )
    stale = client.post(
        url,
        headers={"X-API-Key": "a", "Idempotency-Key": "accept-2"},
        json={"expected_version": 1, "acceptance_token": "long-enough-secret"},
    )
    assert accepted.status_code == replay.status_code == 200
    assert accepted.json() == replay.json()
    assert stale.status_code == 409


def test_handoff_api_target_reports_progress_and_completion_with_token() -> None:
    """ORG-22: a handoff can reach COMPLETED over REST (only with the token)."""
    resumed: list[str] = []
    app = _app()
    app.state.handoff_service = HandoffService(
        InMemoryHandoffRepository(),
        membership=Membership(),
        resume_parent=lambda record: resumed.append(record.state.value),
    )
    client = TestClient(app)
    created = _create(client)
    base = f"/api/v1/coordination/sessions/session-a/handoffs/{created['handoff_id']}"
    token = "long-enough-secret"

    def post(action: str, key: str, body: dict[str, object]) -> Any:
        return client.post(
            f"{base}/{action}", headers={"X-API-Key": "a", "Idempotency-Key": key}, json=body
        )

    accepted = post("accept", "accept", {"expected_version": 1, "acceptance_token": token})
    assert accepted.status_code == 200
    assert post("start", "start-0", {"expected_version": 2}).status_code == 422
    wrong = post("start", "start-1", {"expected_version": 2, "acceptance_token": "x" * 20})
    assert wrong.status_code == 403
    started = post("start", "start-2", {"expected_version": 2, "acceptance_token": token})
    assert started.json()["state"] == "executing"
    completed = post(
        "complete",
        "complete",
        {"expected_version": 3, "acceptance_token": token, "result_reference": "artifact://r"},
    )
    assert completed.status_code == 200
    assert completed.json()["state"] == "completed"
    assert completed.json()["result_reference"] == "artifact://r"
    assert resumed == ["completed"]
