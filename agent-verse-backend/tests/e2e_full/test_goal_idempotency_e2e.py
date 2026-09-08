"""e2e_full: goal submission idempotency on the live path.

Proves the wired idempotency store: two submits with the same ``Idempotency-Key``
create the goal once and the duplicate is rejected with 409 — against the booted
app with real Redis. Runs goals inline (no Celery worker) and pins a deterministic
provider so the first submit is a normal accepted goal.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from app.providers.fake import FakeProvider

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def fresh_client(app: Any, client: Any) -> AsyncIterator[Any]:
    """A dedicated tenant per test so cumulative goal caps don't leak across tests.

    (In production goals run via Celery, which decrements the concurrent-goal
    counter; the inline e2e path nulls the task queue, so isolate the tenant.)
    """
    from httpx import ASGITransport, AsyncClient

    email = f"idem-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "Idem", "email": email})
    assert resp.status_code == 201, f"signup failed: {resp.status_code} {resp.text}"
    api_key = resp.json()["api_key"]
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://e2e-full", headers={"X-API-Key": api_key}
    ) as c:
        yield c


@pytest.fixture
def _inline_goals(app: Any) -> Any:
    gs = app.state.goal_service
    prev_override = getattr(app.state, "_llm_provider_override", None)
    prev_queue = gs._task_queue
    app.state._llm_provider_override = FakeProvider(
        responses=[
            '{"steps": ["do the thing"]}',
            "done",
            '{"success": true, "reason": "ok"}',
        ]
    )
    gs._task_queue = None
    try:
        yield
    finally:
        app.state._llm_provider_override = prev_override
        gs._task_queue = prev_queue


async def test_duplicate_idempotency_key_is_rejected(
    fresh_client: Any, _inline_goals: Any
) -> None:
    key = f"idem-{uuid.uuid4().hex}"
    headers = {"Idempotency-Key": key}

    first = await fresh_client.post(
        "/goals", json={"goal": "Summarize the report"}, headers=headers
    )
    assert first.status_code == 202, f"first submit failed: {first.status_code} {first.text}"
    goal_id = first.json()["goal_id"]
    assert goal_id

    second = await fresh_client.post(
        "/goals", json={"goal": "Summarize the report"}, headers=headers
    )
    assert second.status_code == 409, (
        f"duplicate Idempotency-Key must be rejected with 409, got "
        f"{second.status_code} {second.text}"
    )


async def test_distinct_idempotency_keys_both_accepted(
    fresh_client: Any, _inline_goals: Any
) -> None:
    r1 = await fresh_client.post(
        "/goals", json={"goal": "task one"}, headers={"Idempotency-Key": uuid.uuid4().hex}
    )
    r2 = await fresh_client.post(
        "/goals", json={"goal": "task two"}, headers={"Idempotency-Key": uuid.uuid4().hex}
    )
    assert r1.status_code == 202 and r2.status_code == 202
    assert r1.json()["goal_id"] != r2.json()["goal_id"]
