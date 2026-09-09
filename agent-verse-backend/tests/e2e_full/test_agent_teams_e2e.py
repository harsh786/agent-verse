"""e2e_full: AI Agent Team -- supervisor decomposition + real sub-agent execution.

Phase-3 *AI Agent Team* dimension (Row 20). ``POST /goals`` with
``workflow_mode="supervisor"`` drives ``app.agent.supervisor.SupervisorAgent``: an LLM
decomposes the goal into independent sub-tasks, each sub-task is dispatched as a REAL
goal via ``GoalService.submit_goal`` (persisted, driven through the full
plan -> execute -> verify ``AgentGraph`` loop -- not mocked), and the sub-agent results
are synthesized into one final answer. This proves the team/coordination path
end-to-end: decomposition ("team formation") -> parallel sub-agent execution ->
durable persistence -> retrieval -> synthesis, against the booted app
(``manage_pools=True``) with real Postgres + Redis, goals run inline (no Celery worker).

``SupervisorAgent``'s HTTP response only surfaces internal ephemeral task ids -- see
``app/agent/supervisor.py``'s ``SubAgentTask``, which has no ``goal_id`` field, so the
real ``goal_id`` returned by ``goal_service.submit_goal`` for each sub-task is never
threaded back onto the task or exposed in the API response. So the proof of real
persisted state here is ``GET /goals`` (``list_goals``), which reads the tenant-scoped
``Goal`` records created by the sub-task dispatch by their goal text -- a real API
response shape backed by the same ``GoalService`` instance, not a mock.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from app.providers.base import CompletionResponse
from app.providers.fake import FakeProvider

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

SUBTASK_A = "Investigate Q3 competitor pricing across the top 3 rivals"
SUBTASK_B = "Draft a go-to-market messaging outline for the new plan tier"
SYNTHESIS_TEXT = "Team synthesis: pricing intel gathered and messaging drafted; ready to launch."


def _prompt_text(request: Any) -> str:
    parts = []
    for message in request.messages:
        content = message.content
        parts.append(content if isinstance(content, str) else str(content))
    return " ".join(parts)


class _TeamProvider(FakeProvider):
    """Deterministic provider driving both halves of the team flow:

    - ``app.state._app_provider`` -- consulted directly by the supervisor's
      decompose/synthesize LLM calls in ``app/api/goals.py``.
    - ``app.state._llm_provider_override`` -- consulted by ``GoalService`` when it
      builds the ``AgentGraph`` for each dispatched sub-goal (same seam used by
      ``tests/e2e_full/test_agent_patterns_e2e.py``).
    """

    async def complete(self, request: Any) -> Any:  # type: ignore[override]
        self.call_history.append(request)
        prompt = _prompt_text(request)
        schema = getattr(request, "response_schema", None)
        props = (schema or {}).get("properties", {}) if isinstance(schema, dict) else {}

        if "sub_tasks" in props or "goal decomposer" in prompt:
            content = (
                '{"sub_tasks": ['
                f'{{"goal": "{SUBTASK_A}", "optional": false}}, '
                f'{{"goal": "{SUBTASK_B}", "optional": false}}'
                "]}"
            )
        elif "steps" in props:
            content = '{"steps": ["Carry out the sub-task and report findings"]}'
        elif "success" in props:
            content = '{"success": true, "reason": "sub-task completed as requested"}'
        elif "Synthesize a coherent" in prompt:
            content = SYNTHESIS_TEXT
        else:
            content = "Findings recorded for this sub-task."
        return CompletionResponse(content=content, model="fake", input_tokens=5, output_tokens=5)


@pytest_asyncio.fixture(loop_scope="session")
async def team_client(app: Any, client: Any) -> AsyncIterator[Any]:
    """Fresh tenant per test so cumulative plan/concurrency caps don't leak."""
    from httpx import ASGITransport, AsyncClient

    email = f"team-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "Team", "email": email})
    assert resp.status_code == 201, f"signup failed: {resp.status_code} {resp.text}"
    api_key = resp.json()["api_key"]
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://e2e-full", headers={"X-API-Key": api_key}
    ) as c:
        yield c


@pytest.fixture
def _team_provider(app: Any) -> Any:
    gs = app.state.goal_service
    prev_app_provider = getattr(app.state, "_app_provider", None)
    prev_override = getattr(app.state, "_llm_provider_override", None)
    prev_queue = gs._task_queue
    provider = _TeamProvider()
    app.state._app_provider = provider
    app.state._llm_provider_override = provider
    gs._task_queue = None
    try:
        yield provider
    finally:
        app.state._app_provider = prev_app_provider
        app.state._llm_provider_override = prev_override
        gs._task_queue = prev_queue


async def test_supervisor_forms_team_and_persists_subagent_results(
    app: Any, client: Any, team_client: Any, _team_provider: Any
) -> None:
    """A supervisor-mode goal decomposes into a real 2-agent team; each sub-goal runs
    the full agent loop to completion and both are durably persisted + retrievable via
    GET /goals for this tenant only -- proving the team path is real, not a mock."""
    submit = await team_client.post(
        "/goals",
        json={
            "goal": "Prepare our Q3 competitive response plan",
            "workflow_mode": "supervisor",
            "supervisor_max_parallel": 5,
        },
    )
    assert submit.status_code == 202, f"submit failed: {submit.status_code} {submit.text}"
    body = submit.json()

    assert body["mode"] == "supervisor"
    assert body["success"] is True
    assert len(body["sub_goal_ids"]) == 2
    assert SYNTHESIS_TEXT in body["synthesized_result"]
    sub_tasks = {t["goal"]: t for t in body["sub_tasks"]}
    assert set(sub_tasks) == {SUBTASK_A, SUBTASK_B}
    assert all(t["status"] == "complete" for t in sub_tasks.values()), sub_tasks

    # Durable proof: each sub-task was dispatched as a REAL goal (persisted Goal
    # record, driven through plan -> execute -> verify) and is retrievable for this
    # tenant, in a terminal state -- not merely an in-memory dataclass on the response.
    listing = await team_client.get("/goals")
    assert listing.status_code == 200
    by_text = {g["goal"]: g for g in listing.json()["goals"]}
    for sub_goal in (SUBTASK_A, SUBTASK_B):
        assert sub_goal in by_text, f"sub-agent goal not persisted/retrievable: {sub_goal!r}"
        assert by_text[sub_goal]["status"] == "complete"

    # Negative assertion: an unrelated tenant cannot see this team's persisted work --
    # the feature must FAIL this test if tenant scoping on the goals list regresses.
    from httpx import ASGITransport, AsyncClient

    outsider_email = f"team-outsider-{uuid.uuid4().hex[:12]}@example.com"
    outsider_signup = await client.post(
        "/tenants/signup", json={"name": "Outsider", "email": outsider_email}
    )
    assert outsider_signup.status_code == 201
    outsider_key = outsider_signup.json()["api_key"]
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://e2e-full",
        headers={"X-API-Key": outsider_key},
    ) as outsider_client:
        outsider_listing = await outsider_client.get("/goals")
        assert outsider_listing.status_code == 200
        outsider_texts = {g["goal"] for g in outsider_listing.json()["goals"]}
        assert SUBTASK_A not in outsider_texts
        assert SUBTASK_B not in outsider_texts
