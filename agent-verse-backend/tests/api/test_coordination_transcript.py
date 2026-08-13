from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.coordination_transcript import router
from app.coordination.contracts import Classification
from app.coordination.replay import SequenceReplay
from app.coordination.transcript.repository import InMemoryTranscriptRepository
from app.coordination.transcript.service import TranscriptService
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware


def test_transcript_api_pages_and_enforces_tenant_scope() -> None:
    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        if key in {"a", "b"}:
            return TenantContext(
                tenant_id=f"tenant-{key}", plan=PlanTier.PROFESSIONAL, api_key_id=key
            )
        return None

    service = TranscriptService(InMemoryTranscriptRepository())
    for number in range(3):
        asyncio.run(
            service.append(
                tenant_id="tenant-a",
                session_id="session",
                sender_agent_id="agent",
                message_type="message",
                content=f"message {number}",
                classification=Classification.INTERNAL,
                idempotency_key=f"message-{number}",
            )
        )
    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    app.include_router(router)
    app.state.transcript_service = service
    client = TestClient(app)
    first = client.get(
        "/api/v1/coordination/sessions/session/messages?limit=2",
        headers={"X-API-Key": "a"},
    )
    second = client.get(
        "/api/v1/coordination/sessions/session/messages?after_sequence=2&limit=2",
        headers={"X-API-Key": "a"},
    )
    other = client.get(
        "/api/v1/coordination/sessions/session/messages",
        headers={"X-API-Key": "b"},
    )
    assert [item["sequence"] for item in first.json()["items"]] == [1, 2]
    assert first.json()["has_more"] is True
    assert [item["sequence"] for item in second.json()["items"]] == [3]
    assert other.json()["items"] == []


def test_sse_replay_honors_last_event_id() -> None:
    class Repository:
        async def page(self, **kwargs):
            if kwargs["after_sequence"] != 2:
                return []
            return [
                {
                    "tenant_id": kwargs["tenant_id"],
                    "session_id": kwargs["session_id"],
                    "sequence": 3,
                    "event_id": "event-3",
                    "schema_version": 1,
                    "event_type": "handoff.accepted.v1",
                    "payload": {"safe": True},
                }
            ]

    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        if key != "a":
            return None
        return TenantContext(
            tenant_id="tenant-a", plan=PlanTier.PROFESSIONAL, api_key_id="a"
        )

    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    app.include_router(router)
    app.state.transcript_service = TranscriptService(InMemoryTranscriptRepository())
    app.state.coordination_replay = SequenceReplay(Repository())
    response = TestClient(app).get(
        "/api/v1/coordination/sessions/session/events",
        headers={"X-API-Key": "a", "Last-Event-ID": "2"},
    )
    assert response.status_code == 200
    assert "id: 3" in response.text
    assert "event: handoff.accepted.v1" in response.text
