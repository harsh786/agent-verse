"""e2e_full: a completed goal produces an eval scorecard.

Phase-3 *evals* dimension. A completed goal can be scored by the wired
``EvalRunner`` into a multi-dimension scorecard (per-dimension scores + an
average + pass/fail). Proven against the booted app with real Postgres + Redis,
goals run inline with a deterministic provider.

The scoring seam is ``POST /goals/{id}/eval`` (the "Run Eval" action), which
runs the ``EvalRunner`` and caches the result; ``GET /goals/{id}/eval`` returns
the cached scorecard.

A second test documents a real wiring disconnect (``xfail(strict=True)``):
scoring is *also* meant to happen automatically on ``goal_complete``, but the
completion hook reads ``getattr(self._app_state, "eval_runner")`` where
``_app_state`` is the FastAPI *app* object (``goal_service._app_state = app``),
while the runner is bound to ``app.state.eval_runner`` — so the auto-scoring
lookup is always ``None`` and ``GET /eval`` reports ``not_evaluated`` until an
on-demand run happens.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from app.providers.base import CompletionResponse
from app.providers.fake import FakeProvider

from .conftest import wait_for_status

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


class _CompletingProvider(FakeProvider):
    async def complete(self, request: Any) -> Any:  # type: ignore[override]
        self.call_history.append(request)
        schema = getattr(request, "response_schema", None)
        props = (schema or {}).get("properties", {}) if isinstance(schema, dict) else {}
        if "steps" in props:
            content = '{"steps": ["Produce the requested analysis"]}'
        elif "success" in props:
            content = '{"success": true, "reason": "analysis complete and correct"}'
        else:
            content = "The analysis is complete: revenue rose across all three regions."
        return CompletionResponse(content=content, model="fake", input_tokens=6, output_tokens=6)


@pytest_asyncio.fixture(loop_scope="session")
async def evals_client(app: Any, client: Any) -> AsyncIterator[Any]:
    from httpx import ASGITransport, AsyncClient

    email = f"evals-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "Evals", "email": email})
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


async def test_completed_goal_produces_scorecard(evals_client: Any, _inline_provider: Any) -> None:
    submit = await evals_client.post("/goals", json={"goal": "Analyze regional revenue"})
    assert submit.status_code == 202, f"submit failed: {submit.status_code} {submit.text}"
    goal_id = submit.json()["goal_id"]
    await wait_for_status(evals_client, goal_id, "complete", timeout=30.0)

    # Run the wired EvalRunner over the completed goal → a multi-dimension card.
    scored = await evals_client.post(f"/goals/{goal_id}/eval")
    assert scored.status_code == 200, f"eval run failed: {scored.status_code} {scored.text}"
    card = scored.json()
    assert isinstance(card.get("scores"), dict) and card["scores"], (
        f"scorecard carries no per-dimension scores: {card!r}"
    )
    for dim, value in card["scores"].items():
        assert 0.0 <= float(value) <= 1.0, f"score {dim}={value} out of range: {card!r}"

    # The cached scorecard is then readable and reports "evaluated".
    got = await evals_client.get(f"/goals/{goal_id}/eval")
    assert got.status_code == 200, f"eval fetch failed: {got.status_code} {got.text}"
    cached = got.json()
    assert cached["status"] == "evaluated", f"scorecard not cached after run: {cached!r}"
    assert cached["average_score"] is not None
    assert 0.0 <= float(cached["average_score"]) <= 1.0
    assert cached["passed"] in (True, False)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Auto-eval on goal_complete is disconnected: the completion hook reads "
        "getattr(self._app_state, 'eval_runner') but _app_state is the FastAPI "
        "app while eval_runner is on app.state — so the lookup is always None and "
        "GET /eval stays 'not_evaluated' until an on-demand POST /eval runs."
    ),
)
async def test_scorecard_is_produced_automatically_on_completion(
    evals_client: Any, _inline_provider: Any
) -> None:
    submit = await evals_client.post("/goals", json={"goal": "Analyze the churn cohort"})
    assert submit.status_code == 202
    goal_id = submit.json()["goal_id"]
    await wait_for_status(evals_client, goal_id, "complete", timeout=30.0)

    # DESIRED: scoring happens automatically on completion, no on-demand call.
    got = await evals_client.get(f"/goals/{goal_id}/eval")
    assert got.status_code == 200
    assert got.json()["status"] == "evaluated", "auto-eval on completion did not populate a scorecard"
