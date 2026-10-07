"""a10-F235-01: promote a completed goal to a golden task of an eval suite.

``/eval/golden-datasets`` was a 501 stub over two tables nothing read. Golden
datasets are now one model — the versioned golden tasks of eval suites — and
``POST /intelligence/eval-suites/{id}/tasks/from-goal/{goal_id}`` turns a
completed goal into one: input = goal text (+ context), expected = verified
answer + tools called + sources cited, as a new dataset version, audited.

Real-Postgres coverage: ``tests/integration/test_golden_promotion_pg.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.errors import NotFoundError
from app.intelligence.eval_suite import GoldenTask, cited_sources, score_golden_task
from app.main import create_app
from tests.intelligence._eval_fakes import FakeGoals

SOURCE_EVENTS: list[dict[str, Any]] = [
    {"type": "knowledge_retrieved", "citations": [{"source": "runbook.md", "score": 0.9}]},
    {"type": "tool_call_complete", "tool_name": "kb.search", "output": "found the runbook"},
    {"type": "tool_call_complete", "tool_name": "pager.page", "success": False},
    # Bridge-wrapped (worker) events read the same.
    {"type": "synthesis_complete", "payload": {"type": "synthesis_complete",
                                               "citations": [{"source": "postmortem-42"}]}},
    {"type": "step_complete", "output": "Restart the ingest worker, then replay the DLQ."},
    {"type": "goal_complete"},
]


class PromotableGoals(FakeGoals):
    """FakeGoals plus finished goals that can be promoted."""

    def __init__(self) -> None:
        super().__init__()
        self.finished: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {
            "g-done": (
                {"goal_id": "g-done", "status": "complete", "goal": "Fix the stuck ingest queue",
                 "agent_id": "a1", "dry_run": False},
                SOURCE_EVENTS,
            ),
            "g-failed": (
                {"goal_id": "g-failed", "status": "failed", "goal": "x", "dry_run": False},
                [{"type": "goal_failed"}],
            ),
            "g-dry": (
                {"goal_id": "g-dry", "status": "complete", "goal": "x", "dry_run": True},
                SOURCE_EVENTS,
            ),
            "g-silent": (
                {"goal_id": "g-silent", "status": "complete", "goal": "x", "dry_run": False},
                [{"type": "goal_complete"}],
            ),
        }

    async def get_goal(self, goal_id: str, tenant_ctx: Any) -> dict[str, Any]:
        if goal_id in self.finished:
            return dict(self.finished[goal_id][0])
        if goal_id in self.goals:
            return await super().get_goal(goal_id, tenant_ctx)
        raise NotFoundError(f"Goal not found: {goal_id}")

    async def get_events(self, goal_id: str, tenant_ctx: Any) -> list[dict[str, Any]]:
        if goal_id in self.finished:
            return list(self.finished[goal_id][1])
        if goal_id in self.goals:
            return await super().get_events(goal_id, tenant_ctx)
        raise NotFoundError(f"Goal not found: {goal_id}")


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "eval_suite_goal_poll_seconds", 0.01)
    app = create_app()
    goals = PromotableGoals()
    app.state.goal_service = goals
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "T", "email": "promote@t.com"})
        c.headers["X-API-Key"] = r.json()["api_key"]
        c.app = app  # type: ignore[attr-defined]
        c.goals = goals  # type: ignore[attr-defined]
        yield c


async def _suite(c: AsyncClient) -> str:
    return str((await c.post("/intelligence/eval-suites", json={})).json()["suite_id"])


def _promote_url(sid: str, gid: str) -> str:
    return f"/intelligence/eval-suites/{sid}/tasks/from-goal/{gid}"


async def test_promote_creates_a_new_version_with_the_verified_expectation(
    client: AsyncClient,
) -> None:
    sid = await _suite(client)
    r = await client.post(
        _promote_url(sid, "g-done"),
        json={"context": "Queue: ingest-main", "tags": ["incident"]},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["dataset_version"] == 1 and body["task_id"] == "goal-g-done"

    tasks = (await client.get(f"/intelligence/eval-suites/{sid}/tasks")).json()["tasks"]
    assert len(tasks) == 1
    t = tasks[0]
    assert t["goal"] == "Fix the stuck ingest queue\n\nContext:\nQueue: ingest-main"
    assert t["expected_output"] == "Restart the ingest worker, then replay the DLQ."
    assert t["expected_tools"] == ["kb.search"]  # the failed call is not expected
    assert t["expected_citations"] == ["runbook.md", "postmortem-42"]
    assert t["source_goal_id"] == "g-done"
    assert t["tags"] == ["incident", "promoted-from-goal", "agent:a1"]
    assert t["revision"] == 1


async def test_promote_options_can_drop_tools_and_citations(client: AsyncClient) -> None:
    sid = await _suite(client)
    r = await client.post(
        _promote_url(sid, "g-done"), json={"include_tools": False, "include_citations": False}
    )
    assert r.status_code == 201, r.text
    task = r.json()["task"]
    assert task["expected_tools"] == [] and task["expected_citations"] == []
    assert task["expected_output"]  # the answer is always the reference


async def test_promoting_twice_into_one_suite_is_a_conflict(client: AsyncClient) -> None:
    sid = await _suite(client)
    assert (await client.post(_promote_url(sid, "g-done"))).status_code == 201
    again = await client.post(_promote_url(sid, "g-done"))
    assert again.status_code == 409, again.text
    other = await _suite(client)
    assert (await client.post(_promote_url(other, "g-done"))).status_code == 201


@pytest.mark.parametrize(
    ("goal_id", "status"),
    [("g-failed", 422), ("g-dry", 422), ("g-silent", 422), ("nope", 404)],
)
async def test_only_a_completed_executed_answered_goal_is_promotable(
    client: AsyncClient, goal_id: str, status: int
) -> None:
    sid = await _suite(client)
    r = await client.post(_promote_url(sid, goal_id))
    assert r.status_code == status, r.text
    assert (await client.get(f"/intelligence/eval-suites/{sid}")).json()["dataset_version"] == 0


async def test_unknown_suite_is_404(client: AsyncClient) -> None:
    assert (await client.post(_promote_url("no-such-suite", "g-done"))).status_code == 404


async def test_promotion_is_audited(client: AsyncClient) -> None:
    sid = await _suite(client)
    assert (await client.post(_promote_url(sid, "g-done"))).status_code == 201
    audit = client.app.state.audit_log  # type: ignore[attr-defined]
    hit = [
        e
        for evs in audit._log.values()
        for e in evs
        if e.tool_name == "eval.golden_task.promote"
    ]
    assert hit and hit[-1].outcome == "golden_task_promoted" and hit[-1].goal_id == "g-done"
    assert hit[-1].note == f"suite={sid} task=goal-g-done"


async def test_a_suite_run_includes_the_promoted_task(client: AsyncClient) -> None:
    sid = await _suite(client)
    assert (await client.post(_promote_url(sid, "g-done"))).status_code == 201
    r = await client.post(f"/intelligence/eval-suites/{sid}/run")
    assert r.status_code == 202 and r.json()["dataset_version"] == 1
    for _ in range(300):
        runs = (await client.get(f"/intelligence/eval-suites/{sid}/results")).json()
        if runs[0]["status"] == "completed":
            break
        await asyncio.sleep(0.02)
    assert runs[0]["status"] == "completed" and runs[0]["total"] == 1
    goals = client.goals  # type: ignore[attr-defined]
    assert [s["goal"] for s in goals.submits] == ["Fix the stuck ingest queue"]
    result = runs[0]["task_results"][0]
    assert result["task_id"] == "goal-g-done"
    # The fake rerun called tool "t" and cited nothing: the golden checks catch it.
    assert "Required citation 'runbook.md' was not cited" in result["failure_reasons"]
    assert "Required tool 'kb.search' was not called" in result["failure_reasons"]


async def test_the_retired_stub_route_is_gone(client: AsyncClient) -> None:
    assert (await client.get("/eval/golden-datasets")).status_code == 404


def test_cited_sources_reads_flat_and_wrapped_events() -> None:
    assert cited_sources(SOURCE_EVENTS) == ["runbook.md", "postmortem-42"]


async def test_scorer_checks_expected_citations() -> None:
    task = GoldenTask(goal="g", expected_citations=["runbook.md"], min_score=1.0)
    assert task.has_checks
    ok = await score_golden_task(
        task, events=SOURCE_EVENTS, goal_id="x", judge=None, tenant_ctx=None
    )
    assert ok.passed, ok.failure_reasons
    miss = await score_golden_task(
        GoldenTask(goal="g", expected_citations=["other.md"]),
        events=SOURCE_EVENTS, goal_id="x", judge=None, tenant_ctx=None,
    )
    assert not miss.passed
    assert "Required citation 'other.md' was not cited" in miss.failure_reasons
