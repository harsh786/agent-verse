"""B2-OPEN-2 (workflow webhooks): a dead-lettered /wf-hooks delivery is retried
under the delivery's own dedup key.

The live path dedupes sender retries on ``webhook:<delivery id>`` through the
run store's (tenant, workflow, idempotency_key) index, but the DLQ row kept no
key and the retry task ran the payload without one — so a delivery whose run
could not start, then redelivered successfully by the sender, ran a second time
when the DLQ retry fired (and vice versa).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.workflow.celery_tasks import retry_dead_letter_webhooks_async
from tests.workflow.test_webhook_hardening import _T, _published, env  # noqa: F401


class _DlqStore:
    """The env's token store + an in-memory workflow_webhook_events table."""

    def __init__(self, base: Any) -> None:
        self._base = base
        self.rows: dict[str, dict[str, Any]] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)

    async def record_webhook_failure(self, **kw: Any) -> str:
        event_id = f"ev-{len(self.rows) + 1}"
        self.rows[event_id] = {"id": event_id, "status": "failed", **kw}
        return event_id

    async def get_retryable_webhooks(self, max_attempts: int = 3) -> list[dict[str, Any]]:
        return [
            {
                "id": r["id"],
                "tenant_id": r["tenant_id"],
                "workflow_id": r["workflow_id"],
                "payload": r["payload"],
                "idempotency_key": r.get("idempotency_key"),
            }
            for r in self.rows.values()
            if r["status"] == "failed"
        ]

    async def mark_webhook_attempt(self, **kw: Any) -> str:
        row = self.rows[kw["event_id"]]
        row["status"] = "succeeded" if kw.get("run_id") else "failed"
        row["run_id"] = kw.get("run_id")
        return str(row["status"])


def _arm(env: dict[str, Any]) -> tuple[Any, _DlqStore]:  # noqa: F811
    runner = env["runner"]
    store = _DlqStore(runner._run_store)
    runner._run_store = store
    return runner, store


def _fail_next_run(runner: Any) -> None:
    original = runner.run

    async def once(**kw: Any) -> str:
        runner.run = original
        raise RuntimeError("broker unavailable")

    runner.run = once


@pytest.mark.asyncio
async def test_dead_letter_keeps_the_delivery_key(env: dict[str, Any]) -> None:  # noqa: F811
    client, svc = env["client"], env["svc"]
    runner, store = _arm(env)
    wid = await _published(svc)
    path = client.get(f"/api/v1/workflows/{wid}/webhook").json()["webhook_path"]

    _fail_next_run(runner)
    r = client.post(path, json={"n": 1}, headers={"X-Delivery-Id": "d-7"})

    assert r.status_code == 202 and r.json()["status"] == "queued_for_retry"
    (row,) = store.rows.values()
    assert row["idempotency_key"] == "webhook:d-7"


@pytest.mark.asyncio
async def test_retry_after_a_successful_redelivery_starts_no_second_run(
    env: dict[str, Any],  # noqa: F811
) -> None:
    client, svc = env["client"], env["svc"]
    runner, store = _arm(env)
    wid = await _published(svc)
    path = client.get(f"/api/v1/workflows/{wid}/webhook").json()["webhook_path"]
    headers = {"X-Delivery-Id": "d-8"}

    _fail_next_run(runner)
    assert client.post(path, json={"n": 1}, headers=headers).status_code == 202
    redelivered = client.post(path, json={"n": 1}, headers=headers)
    assert redelivered.status_code == 200
    run_id = redelivered.json()["run_id"]

    counts = await retry_dead_letter_webhooks_async(runner)

    assert counts["succeeded"] == 1
    assert len(runner.runs) == 1  # the delivery ran once
    (row,) = store.rows.values()
    assert row["run_id"] == run_id


@pytest.mark.asyncio
async def test_redelivery_after_a_successful_retry_starts_no_second_run(
    env: dict[str, Any],  # noqa: F811
) -> None:
    client, svc = env["client"], env["svc"]
    runner, _store = _arm(env)
    wid = await _published(svc)
    path = client.get(f"/api/v1/workflows/{wid}/webhook").json()["webhook_path"]
    headers = {"X-Delivery-Id": "d-9"}

    _fail_next_run(runner)
    assert client.post(path, json={"n": 1}, headers=headers).status_code == 202
    await retry_dead_letter_webhooks_async(runner)
    redelivered = client.post(path, json={"n": 1}, headers=headers)

    assert redelivered.status_code == 200
    assert len(runner.runs) == 1


@pytest.mark.asyncio
async def test_delivery_without_an_id_is_retried_without_a_key(
    env: dict[str, Any],  # noqa: F811
) -> None:
    client, svc = env["client"], env["svc"]
    runner, store = _arm(env)
    wid = await _published(svc)
    path = client.get(f"/api/v1/workflows/{wid}/webhook").json()["webhook_path"]

    _fail_next_run(runner)
    assert client.post(path, json={"n": 1}).status_code == 202
    (row,) = store.rows.values()
    assert "idempotency_key" not in row
    await retry_dead_letter_webhooks_async(runner)
    assert len(runner.runs) == 1 and "idempotency_key" not in runner.runs[0]
    assert row["tenant_id"] == _T
