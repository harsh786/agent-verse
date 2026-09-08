"""e2e_full: per-role model selection is observable on the live goal path.

Phase-3 *model-router* dimension. Each of the three agent roles (planner,
executor, verifier) has its model resolved independently by the wired
``ModelRouter`` during planning, and that decision is surfaced as a
``model_route_selected`` runtime-decision-trace SSE event carrying the model
chosen for every role. Proven against the booted app with real Postgres +
Redis, goals run inline with a deterministic provider.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from app.providers.base import CompletionResponse
from app.providers.fake import FakeProvider

from .conftest import collect_sse, wait_for_status

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


class _CompletingProvider(FakeProvider):
    # The model_route_selected event reads each role provider's ``_default_model``
    # for the executor/verifier slots; give the fake one so those roles surface a
    # concrete model rather than an empty string.
    _default_model = "fake-role-model"

    async def complete(self, request: Any) -> Any:  # type: ignore[override]
        self.call_history.append(request)
        schema = getattr(request, "response_schema", None)
        props = (schema or {}).get("properties", {}) if isinstance(schema, dict) else {}
        if "steps" in props:
            content = '{"steps": ["Carry out the task"]}'
        elif "success" in props:
            content = '{"success": true, "reason": "ok"}'
        else:
            content = "Task carried out."
        return CompletionResponse(content=content, model="fake", input_tokens=5, output_tokens=5)


@pytest_asyncio.fixture(loop_scope="session")
async def router_client(app: Any, client: Any) -> AsyncIterator[Any]:
    from httpx import ASGITransport, AsyncClient

    email = f"router-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "Router", "email": email})
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


async def test_model_route_selected_event_names_all_three_roles(
    router_client: Any, _inline_provider: Any
) -> None:
    submit = await router_client.post("/goals", json={"goal": "Plan and run a small task"})
    assert submit.status_code == 202, f"submit failed: {submit.status_code} {submit.text}"
    goal_id = submit.json()["goal_id"]

    await wait_for_status(router_client, goal_id, {"complete", "failed"}, timeout=30.0)

    events = await collect_sse(router_client, goal_id, until="goal_complete", timeout=15.0)
    routes = [
        payload
        for payload in (_parse(raw) for raw in events)
        if payload and payload.get("type") == "model_route_selected"
    ]
    assert routes, (
        "no model_route_selected event on the trace — per-role model selection "
        f"was not surfaced. events seen: {[_type(r) for r in events]}"
    )

    route = routes[0]
    # Every role's chosen model is present and observable — the point of the
    # per-role router (each role can be tuned/priced independently).
    for role in ("planner", "executor", "verifier"):
        assert role in route, f"role {role!r} missing from model route event: {route!r}"
        assert route[role] != "", f"role {role!r} resolved to an empty model: {route!r}"
    assert "cost_class" in route, f"cost_class missing from model route event: {route!r}"


def _parse(raw: str) -> dict[str, Any] | None:
    try:
        return json.loads(raw)  # type: ignore[no-any-return]
    except Exception:
        return None


def _type(raw: str) -> str | None:
    parsed = _parse(raw)
    return parsed.get("type") if parsed else None
