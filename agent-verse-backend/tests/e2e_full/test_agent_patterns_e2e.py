"""e2e_full: the core agent loop engages plan → execute → verify → complete.

Phase-3 *agent-patterns* dimension. A submitted goal must drive the real
LangGraph state machine through its four lifecycle phases and reach a terminal
``complete`` status — proven against the booted app (manage_pools=True) with
real Postgres + Redis, goals run inline (no Celery worker).

The proof is the persisted SSE decision trace: after the goal completes, the
replay stream carries ``plan_ready`` (planner produced steps) →
``step_started`` / ``step_complete`` (executor ran a step) → ``verification_done``
(verifier judged success) → ``goal_complete``. A deterministic provider that
branches on the request's response schema makes each phase emit a fixed,
correct answer regardless of how many auxiliary LLM calls the loop makes.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from app.providers.base import CompletionResponse
from app.providers.fake import FakeProvider

from .conftest import collect_sse, wait_for_status

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


class _PlanExecVerifyProvider(FakeProvider):
    """Deterministic provider: one clean plan → step output → success verdict."""

    async def complete(self, request: Any) -> Any:  # type: ignore[override]
        self.call_history.append(request)
        schema = getattr(request, "response_schema", None)
        props = (schema or {}).get("properties", {}) if isinstance(schema, dict) else {}
        if "steps" in props:
            content = '{"steps": ["Draft the summary paragraph"]}'
        elif "success" in props:
            content = '{"success": true, "reason": "summary produced as requested"}'
        else:
            content = "Here is the drafted summary paragraph."
        return CompletionResponse(content=content, model="fake", input_tokens=5, output_tokens=5)


@pytest_asyncio.fixture(loop_scope="session")
async def patterns_client(app: Any, client: Any) -> AsyncIterator[Any]:
    """Fresh tenant per test so cumulative plan/concurrency caps don't leak."""
    from httpx import ASGITransport, AsyncClient

    email = f"patterns-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "Patterns", "email": email})
    assert resp.status_code == 201, f"signup failed: {resp.status_code} {resp.text}"
    api_key = resp.json()["api_key"]
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://e2e-full", headers={"X-API-Key": api_key}
    ) as c:
        yield c


@pytest.fixture
def _inline_provider(app: Any) -> Any:
    gs = app.state.goal_service
    prev_override = getattr(app.state, "_llm_provider_override", None)
    prev_queue = gs._task_queue
    app.state._llm_provider_override = _PlanExecVerifyProvider()
    gs._task_queue = None
    try:
        yield
    finally:
        app.state._llm_provider_override = prev_override
        gs._task_queue = prev_queue


async def test_goal_runs_plan_execute_verify_complete(
    patterns_client: Any, _inline_provider: Any
) -> None:
    submit = await patterns_client.post("/goals", json={"goal": "Summarize the quarterly report"})
    assert submit.status_code == 202, f"submit failed: {submit.status_code} {submit.text}"
    goal_id = submit.json()["goal_id"]
    assert goal_id

    # The loop must reach a terminal complete status.
    final = await wait_for_status(patterns_client, goal_id, "complete", timeout=30.0)
    assert str(final.get("status")) == "complete"

    # The persisted decision trace proves all four phases were engaged, in order.
    events = await collect_sse(patterns_client, goal_id, until="goal_complete", timeout=15.0)
    types = [e for e in (_event_type(raw) for raw in events) if e]

    assert "plan_ready" in types, f"planner phase missing from trace: {types}"
    assert "step_started" in types, f"execute phase (start) missing from trace: {types}"
    assert "step_complete" in types, f"execute phase (complete) missing from trace: {types}"
    assert "verification_done" in types, f"verify phase missing from trace: {types}"
    assert "goal_complete" in types, f"completion event missing from trace: {types}"

    # Ordering: plan precedes execute precedes verify precedes complete.
    assert types.index("plan_ready") < types.index("step_started"), (
        f"plan must precede execute: {types}"
    )
    assert types.index("step_complete") <= types.index("verification_done"), (
        f"execute must precede verify: {types}"
    )
    assert types.index("verification_done") <= types.index("goal_complete"), (
        f"verify must precede complete: {types}"
    )


def _event_type(raw: str) -> str | None:
    import json

    try:
        return str(json.loads(raw).get("type"))
    except Exception:
        return None
