from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.coordination_magentic import router
from app.coordination.ledger.models import LedgerRevision
from app.coordination.ledger.repository import InMemoryProgressLedgerRepository
from app.coordination.magentic.human_review import MagenticHumanReviewService
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware


def _app() -> FastAPI:
    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        if key in {"a", "b"}:
            return TenantContext(
                tenant_id=f"tenant-{key}", plan=PlanTier.PROFESSIONAL, api_key_id=key
            )
        return None

    ledger = InMemoryProgressLedgerRepository()
    asyncio.run(
        ledger.append(
            LedgerRevision(
                tenant_id="tenant-a",
                session_id="session",
                version=1,
                objective="safe objective",
                idempotency_key="one",
            ),
            expected_predecessor_version=0,
        )
    )
    review = MagenticHumanReviewService()
    asyncio.run(review.issue("tenant-a", "session", "review-token-1234"))
    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    app.include_router(router)
    app.state.progress_ledger_repository = ledger
    app.state.magentic_human_review = review
    return app


def test_ledger_reads_are_paginated_immutable_and_tenant_scoped() -> None:
    client = TestClient(_app())
    assert client.get(
        "/api/v1/coordination/sessions/session/ledger", headers={"X-API-Key": "a"}
    ).status_code == 200
    page = client.get(
        "/api/v1/coordination/sessions/session/ledger/revisions?limit=1",
        headers={"X-API-Key": "a"},
    ).json()
    assert page["items"][0]["version"] == page["next_version"] == 1
    assert client.get(
        "/api/v1/coordination/sessions/session/ledger", headers={"X-API-Key": "b"}
    ).status_code == 404


def test_human_review_token_is_one_time_and_session_scoped() -> None:
    client = TestClient(_app())
    url = "/api/v1/coordination/sessions/session/magentic/human-review"
    body = {"token": "review-token-1234", "approved": True, "safe_note": "continue"}
    assert client.post(url, json=body, headers={"X-API-Key": "b"}).status_code == 409
    assert client.post(url, json=body, headers={"X-API-Key": "a"}).status_code == 200
    assert client.post(url, json=body, headers={"X-API-Key": "a"}).status_code == 409
