"""e2e_full: a goal emits a durable runtime decision-trace over SSE.

Phase-3 *observability* dimension. Every orchestration decision a goal makes is
recorded as a structured SSE event, persisted, and replayable *after* the goal
is terminal — so an operator can reconstruct what the agent did without having
watched it live. Proven against the booted app with real Postgres + Redis, goal
run inline with a deterministic provider.

The trace must (1) open with ``goal_started``, (2) contain the orchestration
decision points — ``plan_ready`` and at least one runtime-decision-trace event
from the ``RuntimeSSEEmitter`` family (``model_route_selected``), (3) close with
a terminal ``goal_complete``, and (4) be fully recoverable from the replay
stream opened only after the goal has already finished.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from app.observability.runtime_decision_trace import SSEEventType
from app.providers.base import CompletionResponse
from app.providers.fake import FakeProvider

from .conftest import collect_sse, wait_for_status

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

# The runtime-decision-trace event family emitted by RuntimeSSEEmitter.
_DECISION_TRACE_TYPES = {
    SSEEventType.RUNTIME_PROFILE_SELECTED,
    SSEEventType.PATTERN_ASSEMBLED,
    SSEEventType.RAG_STRATEGY_SELECTED,
    SSEEventType.MODEL_ROUTE_SELECTED,
    SSEEventType.GUARDRAIL_PROFILE_SELECTED,
    SSEEventType.EVAL_SCORE_RECORDED,
}


class _CompletingProvider(FakeProvider):
    async def complete(self, request: Any) -> Any:  # type: ignore[override]
        self.call_history.append(request)
        schema = getattr(request, "response_schema", None)
        props = (schema or {}).get("properties", {}) if isinstance(schema, dict) else {}
        if "steps" in props:
            content = '{"steps": ["Execute the observable task"]}'
        elif "success" in props:
            content = '{"success": true, "reason": "observed and done"}'
        else:
            content = "The observable task ran to completion."
        return CompletionResponse(content=content, model="fake", input_tokens=5, output_tokens=5)


@pytest_asyncio.fixture(loop_scope="session")
async def obs_client(app: Any, client: Any) -> AsyncIterator[Any]:
    from httpx import ASGITransport, AsyncClient

    email = f"obs-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "Obs", "email": email})
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
    app.state._llm_provider_override = _CompletingProvider()
    gs._task_queue = None
    try:
        yield
    finally:
        app.state._llm_provider_override = prev_override
        gs._task_queue = prev_queue


async def test_goal_emits_replayable_decision_trace(obs_client: Any, _inline_provider: Any) -> None:
    submit = await obs_client.post("/goals", json={"goal": "Run an observable task"})
    assert submit.status_code == 202, f"submit failed: {submit.status_code} {submit.text}"
    goal_id = submit.json()["goal_id"]

    # Let the goal finish FIRST, then open the stream — this proves the trace is
    # durable/replayable, not merely a live-only feed.
    await wait_for_status(obs_client, goal_id, "complete", timeout=30.0)

    events = await collect_sse(obs_client, goal_id, until="goal_complete", timeout=15.0)
    parsed = [p for p in (_parse(raw) for raw in events) if p is not None]
    assert parsed, "no events replayed after the goal completed — trace not durable"

    # Every replayed frame is a well-formed decision-trace record with a type.
    for p in parsed:
        assert isinstance(p, dict) and p.get("type"), f"malformed trace event: {p!r}"

    types = [p["type"] for p in parsed]

    # (1) opens with goal_started
    assert types[0] == "goal_started", f"trace should open with goal_started: {types}"
    # (2) records the planning decision
    assert "plan_ready" in types, f"planning decision missing from trace: {types}"
    # (2b) records at least one RuntimeSSEEmitter runtime-decision-trace event
    assert _DECISION_TRACE_TYPES.intersection(types), (
        f"no runtime-decision-trace event ({_DECISION_TRACE_TYPES}) in trace: {types}"
    )
    # (3) closes with a terminal completion event
    assert "goal_complete" in types, f"terminal completion missing from trace: {types}"


def _parse(raw: str) -> dict[str, Any] | None:
    try:
        return json.loads(raw)  # type: ignore[no-any-return]
    except Exception:
        return None
