"""WF-07: failed /wf-hooks deliveries are dead-lettered, and the retry task
records every attempt so a row is retried at most 3 times and never again once
it succeeded.

Old bugs: nothing inserted into ``workflow_webhook_events`` (a delivery whose
run could not start was lost), and ``retry_dead_letter_webhooks`` never updated
attempts/status, so any row would have been re-run every 5 minutes forever.
"""

from __future__ import annotations

import hashlib
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.workflow.celery_tasks import retry_dead_letter_webhooks_async
from app.workflow.webhook_router import router as webhook_router
from app.workflow.webhook_tokens import make_webhook_token


class _DlqStore:
    """In-memory stand-in mirroring the SQL semantics of the DLQ methods."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def record_webhook_failure(self, **kw: Any) -> str:
        event_id = f"ev-{len(self.rows) + 1}"
        self.rows[event_id] = {**kw, "id": event_id, "status": "failed", "attempts": 0}
        return event_id

    async def get_retryable_webhooks(self, max_attempts: int = 3) -> list[dict[str, Any]]:
        return [
            {
                "id": r["id"],
                "tenant_id": r["tenant_id"],
                "workflow_id": r["workflow_id"],
                "payload": r["payload"],
            }
            for r in self.rows.values()
            if r["status"] in ("pending", "failed") and r["attempts"] < max_attempts
        ]

    async def mark_webhook_attempt(
        self,
        *,
        tenant_id: str,
        event_id: str,
        run_id: str | None = None,
        error: str | None = None,
        max_attempts: int = 3,
    ) -> str | None:
        row = self.rows[event_id]
        if row["status"] not in ("pending", "failed"):
            return None
        row["attempts"] += 1
        if run_id:
            row.update(status="succeeded", run_id=run_id)
        else:
            row.update(
                status="dead" if row["attempts"] >= max_attempts else "failed",
                last_error=error,
            )
        return str(row["status"])


def _client(runner: Any) -> TestClient:
    app = FastAPI()
    app.include_router(webhook_router)
    svc = AsyncMock()
    svc.get = AsyncMock(
        return_value={"status": "published", "definition": {"triggers": [{"type": "webhook"}]}}
    )
    svc.webhook_token_version = AsyncMock(return_value=0)
    app.state.workflow_service = svc
    app.state.workflow_runner = runner
    return TestClient(app)


def test_delivery_whose_run_cannot_start_is_dead_lettered_once() -> None:
    store = _DlqStore()
    runner = AsyncMock()
    runner._run_store = store
    runner.run = AsyncMock(side_effect=RuntimeError("broker unreachable"))
    token = make_webhook_token("tenant-1", "wf-1")

    resp = _client(runner).post(f"/wf-hooks/{token}", json={"invoice": "INV-9"})

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued_for_retry" and body["delivery_id"] == "ev-1"
    assert list(store.rows) == ["ev-1"]
    row = store.rows["ev-1"]
    assert row["tenant_id"] == "tenant-1" and row["workflow_id"] == "wf-1"
    assert row["payload"] == {"invoice": "INV-9"}
    assert row["error"] == "broker unreachable"
    # Only a fingerprint of the credential-bearing token is stored.
    assert row["token_fingerprint"] == hashlib.sha256(token.encode()).hexdigest()[:16]
    assert token not in str(row)


def test_undeliverable_and_unstorable_delivery_answers_503() -> None:
    store = _DlqStore()
    store.record_webhook_failure = AsyncMock(side_effect=RuntimeError("db down"))  # type: ignore[method-assign]
    runner = AsyncMock()
    runner._run_store = store
    runner.run = AsyncMock(side_effect=RuntimeError("broker unreachable"))
    token = make_webhook_token("tenant-1", "wf-1")

    assert _client(runner).post(f"/wf-hooks/{token}", json={}).status_code == 503


@pytest.mark.asyncio
async def test_retry_marks_success_and_never_reruns_a_succeeded_row() -> None:
    store = _DlqStore()
    await store.record_webhook_failure(
        tenant_id="t", workflow_id="wf", token_fingerprint="f", payload={"a": 1}, error="x"
    )
    runner = AsyncMock()
    runner._run_store = store
    runner.run = AsyncMock(return_value="run-1")

    first = await retry_dead_letter_webhooks_async(runner)
    assert first["succeeded"] == 1
    assert store.rows["ev-1"]["status"] == "succeeded"
    assert runner.run.await_args.kwargs["trigger_payload"] == {"a": 1}

    again = await retry_dead_letter_webhooks_async(runner)
    assert again["retried"] == 0
    assert runner.run.await_count == 1


@pytest.mark.asyncio
async def test_retry_gives_up_as_dead_after_three_attempts() -> None:
    store = _DlqStore()
    await store.record_webhook_failure(
        tenant_id="t", workflow_id="wf", token_fingerprint="f", payload={}, error="x"
    )
    runner = AsyncMock()
    runner._run_store = store
    runner.run = AsyncMock(side_effect=RuntimeError("still down"))

    for _ in range(5):
        await retry_dead_letter_webhooks_async(runner)

    row = store.rows["ev-1"]
    assert row["status"] == "dead" and row["attempts"] == 3
    assert row["last_error"] == "still down"
    assert runner.run.await_count == 3
