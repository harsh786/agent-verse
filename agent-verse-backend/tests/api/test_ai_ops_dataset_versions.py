"""P7-3: versioned AI-Ops eval datasets, and the version a run executed.

Live EVAL-GOLDEN-VERSIONING: ``PATCH``/``PUT /ai-ops/datasets/{id}`` were 404 (no
edit API), ``version`` was always 1, and a run never recorded which dataset
version it ran. Now a published version is immutable, an edit creates a draft
version, a run pins (and publishes) one version and records it.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api import ai_ops
from app.api.ai_ops import router as ai_ops_router
from app.evals.ai_ops_jobs import RunConfig
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_A = TenantContext(tenant_id="t-ver-a", plan=PlanTier.PROFESSIONAL, api_key_id="ka")
_B = TenantContext(tenant_id="t-ver-b", plan=PlanTier.PROFESSIONAL, api_key_id="kb")
_HA = {"X-API-Key": "key-a"}
_HB = {"X-API-Key": "key-b"}
_V1 = [{"input": "q0", "expected_output": "old 0"}, {"input": "q1", "expected_output": "a1"}]


class _Goals:
    """Every goal completes, answering ``answer <input>``."""

    def __init__(self) -> None:
        self.goals: dict[str, str] = {}

    async def submit_goal(self, *, goal: str, **_: Any) -> dict[str, Any]:
        gid = f"g{len(self.goals)}"
        self.goals[gid] = goal
        return {"goal_id": gid}

    async def get_goal(self, goal_id: str, tenant_ctx: Any) -> dict[str, Any]:
        return {"goal_id": goal_id, "status": "complete"}

    async def get_events(self, goal_id: str, tenant_ctx: Any) -> list[dict[str, Any]]:
        return [{"type": "step_complete", "output": f"answer {self.goals[goal_id]}"},
                {"type": "goal_complete"}]


def _app() -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return {"key-a": _A, "key-b": _B}.get(key)

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(ai_ops_router)
    app.state.goal_service = _Goals()
    app.state.ai_ops_run_config = RunConfig(
        concurrency=4, poll_seconds=0.001, lease_seconds=30, case_timeout=30
    )
    return app


@pytest.fixture(autouse=True)
def _clean() -> None:
    ai_ops._datasets.clear()
    ai_ops._eval_results.clear()


@pytest.fixture
async def client() -> Any:
    app = _app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        c.app = app  # type: ignore[attr-defined]
        yield c


async def _create(c: AsyncClient) -> str:
    r = await c.post("/ai-ops/datasets", json={"name": "golden", "golden_tasks": _V1},
                     headers=_HA)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1
    return str(r.json()["dataset_id"])


async def _run(c: AsyncClient, did: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    started = await c.post(f"/ai-ops/datasets/{did}/run", json=body or {}, headers=_HA)
    assert started.status_code == 202, started.text
    pending = list(c.app.state.__dict__.get("_ai_ops_run_tasks", set()))  # type: ignore[attr-defined]
    await asyncio.gather(*pending)
    res = await c.get(f"/ai-ops/eval-results/{started.json()['result_id']}", headers=_HA)
    return dict(res.json())


async def test_new_dataset_is_published_version_1(client: AsyncClient) -> None:
    did = await _create(client)
    got = (await client.get(f"/ai-ops/datasets/{did}", headers=_HA)).json()
    assert got["version"] == 1 and got["status"] == "published"
    assert got["published_version"] == 1
    assert [v["version"] for v in got["versions"]] == [1]
    listed = (await client.get("/ai-ops/datasets", headers=_HA)).json()["datasets"]
    assert [(d["dataset_id"], d["version"]) for d in listed] == [(did, 1)]


@pytest.mark.parametrize("method", ["PATCH", "PUT"])
async def test_edit_creates_a_draft_and_never_mutates_the_published_version(
    client: AsyncClient, method: str
) -> None:
    did = await _create(client)
    v2 = [{"input": "q0", "expected_output": "new 0"}, _V1[1]]
    r = await client.request(method, f"/ai-ops/datasets/{did}",
                             json={"golden_tasks": v2, "description": "d2"}, headers=_HA)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2 and r.json()["status"] == "draft"
    assert r.json()["published_version"] == 1
    v1 = (await client.get(f"/ai-ops/datasets/{did}/versions/1", headers=_HA)).json()
    assert v1["golden_tasks"] == _V1 and v1["status"] == "published"
    # A second edit changes the draft in place: still version 2.
    r = await client.patch(f"/ai-ops/datasets/{did}",
                           json={"golden_tasks": [*v2, {"input": "q2"}]}, headers=_HA)
    assert r.json()["version"] == 2 and r.json()["task_count"] == 3
    versions = (await client.get(f"/ai-ops/datasets/{did}/versions", headers=_HA)).json()
    assert [(v["version"], v["status"]) for v in versions["versions"]] == [
        (2, "draft"), (1, "published")
    ]


async def test_task_crud_edits_the_draft(client: AsyncClient) -> None:
    did = await _create(client)
    r = await client.post(f"/ai-ops/datasets/{did}/tasks",
                          json={"task": {"input": "q2", "expected_output": "a2"}}, headers=_HA)
    assert r.status_code == 201 and r.json()["version"] == 2 and r.json()["task_count"] == 3
    r = await client.put(f"/ai-ops/datasets/{did}/tasks/0",
                         json={"task": {"input": "q0", "expected_output": "new 0"}}, headers=_HA)
    assert r.json()["golden_tasks"][0]["expected_output"] == "new 0"
    r = await client.delete(f"/ai-ops/datasets/{did}/tasks/1", headers=_HA)
    assert r.status_code == 200
    assert [t["input"] for t in r.json()["golden_tasks"]] == ["q0", "q2"]
    assert r.json()["version"] == 2  # all three edits went into the one draft
    assert (await client.delete(f"/ai-ops/datasets/{did}/tasks/9", headers=_HA)).status_code == 404
    bad = await client.put(f"/ai-ops/datasets/{did}/tasks/0", json={"task": {"input": " "}},
                           headers=_HA)
    assert bad.status_code == 422
    stale = await client.post(f"/ai-ops/datasets/{did}/tasks",
                              json={"task": {"input": "x"}, "if_version": 1}, headers=_HA)
    assert stale.status_code == 409


async def test_a_run_records_and_pins_the_version_it_ran(client: AsyncClient) -> None:
    did = await _create(client)
    await client.patch(f"/ai-ops/datasets/{did}", json={"golden_tasks": [
        {"input": "q0", "expected_output": "answer q0"}, _V1[1]]}, headers=_HA)
    result = await _run(client, did)
    assert result["status"] == "completed"
    assert result["dataset_version"] == 2
    assert result["dataset"] == {"dataset_id": did, "version": 2, "name": "golden"}
    assert result["cases"][0]["expected"] == "answer q0"  # the edited task text
    # Running the draft published it: it is immutable now, so the next edit is v3.
    head = (await client.get(f"/ai-ops/datasets/{did}", headers=_HA)).json()
    assert head["version"] == 2 and head["status"] == "published"
    r = await client.patch(f"/ai-ops/datasets/{did}", json={"golden_tasks": [{"input": "z"}]},
                           headers=_HA)
    assert r.json()["version"] == 3
    v2 = (await client.get(f"/ai-ops/datasets/{did}/versions/2", headers=_HA)).json()
    assert v2["golden_tasks"][0]["expected_output"] == "answer q0"
    # An explicit older version can still be run, and is recorded as such.
    old = await _run(client, did, {"dataset_version": 1})
    assert old["dataset_version"] == 1
    assert old["cases"][0]["expected"] == "old 0"
    listed = (await client.get("/ai-ops/eval-results", headers=_HA)).json()["results"]
    assert {r["dataset_version"] for r in listed} == {1, 2}


async def test_publish_endpoint(client: AsyncClient) -> None:
    did = await _create(client)
    assert (await client.post(f"/ai-ops/datasets/{did}/publish", headers=_HA)).status_code == 409
    await client.post(f"/ai-ops/datasets/{did}/tasks", json={"task": {"input": "q9"}},
                      headers=_HA)
    r = await client.post(f"/ai-ops/datasets/{did}/publish", headers=_HA)
    assert r.status_code == 200 and r.json()["status"] == "published"
    assert r.json()["version"] == 2


async def test_versions_are_tenant_isolated(client: AsyncClient) -> None:
    did = await _create(client)
    assert (await client.get(f"/ai-ops/datasets/{did}", headers=_HB)).status_code == 404
    r = await client.patch(f"/ai-ops/datasets/{did}", json={"golden_tasks": []}, headers=_HB)
    assert r.status_code == 404
    r = await client.get(f"/ai-ops/datasets/{did}/versions/1", headers=_HB)
    assert r.status_code == 404
    unknown = await client.post(f"/ai-ops/datasets/{did}/run", json={"dataset_version": 7},
                                headers=_HA)
    assert unknown.status_code == 404
