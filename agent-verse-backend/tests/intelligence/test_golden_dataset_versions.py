"""MEM-54: golden datasets are versioned rows — editable, with immutable past versions.

Tasks were one unversioned JSON array on the suite row: no edit or delete, no
dataset version, and runs did not record which tasks they executed.
"""

from __future__ import annotations

from typing import Any
import pytest
from httpx import ASGITransport, AsyncClient

from app.intelligence.eval_suite_store import EvalSuiteStore
from app.main import create_app


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.core.config import get_settings
    from tests.intelligence._eval_fakes import FakeGoals

    monkeypatch.setattr(get_settings(), "eval_suite_goal_poll_seconds", 0.01)
    app = create_app()
    goals = FakeGoals()
    app.state.goal_service = goals
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "T", "email": "ds@t.com"})
        c.headers["X-API-Key"] = r.json()["api_key"]
        c.goals = goals  # type: ignore[attr-defined]
        yield c


async def _suite(c: AsyncClient) -> str:
    return str((await c.post("/intelligence/eval-suites", json={})).json()["suite_id"])


async def _add(c: AsyncClient, sid: str, goal: str, **kw: Any) -> dict[str, Any]:
    body = {"goal": goal, "expected_tools": ["kb.search"], **kw}
    r = await c.post(f"/intelligence/eval-suites/{sid}/tasks", json=body)
    assert r.status_code == 201, r.text
    data: dict[str, Any] = r.json()
    return data


async def _tasks(c: AsyncClient, sid: str, version: int | None = None) -> list[dict[str, Any]]:
    params = {} if version is None else {"version": version}
    r = await c.get(f"/intelligence/eval-suites/{sid}/tasks", params=params)
    assert r.status_code == 200, r.text
    tasks: list[dict[str, Any]] = r.json()["tasks"]
    return tasks


async def test_every_edit_is_a_new_version_and_old_versions_are_unchanged(
    client: AsyncClient,
) -> None:
    sid = await _suite(client)
    a = await _add(client, sid, "first")
    assert a["dataset_version"] == 1
    b = await _add(client, sid, "second")
    assert b["dataset_version"] == 2

    r = await client.patch(
        f"/intelligence/eval-suites/{sid}/tasks/{a['task_id']}", json={"goal": "first, edited"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["dataset_version"] == 3

    r = await client.delete(f"/intelligence/eval-suites/{sid}/tasks/{b['task_id']}")
    assert r.status_code == 200 and r.json()["dataset_version"] == 4

    assert [t["goal"] for t in await _tasks(client, sid)] == ["first, edited"]
    assert [t["goal"] for t in await _tasks(client, sid, 2)] == ["first", "second"]
    assert [t["goal"] for t in await _tasks(client, sid, 3)] == ["first, edited", "second"]
    suite = (await client.get(f"/intelligence/eval-suites/{sid}")).json()
    assert suite["dataset_version"] == 4 and suite["task_count"] == 1


async def test_an_edit_keeps_unspecified_fields(client: AsyncClient) -> None:
    sid = await _suite(client)
    a = await _add(client, sid, "g", forbidden_tools=["db.drop"], min_score=0.6)
    await client.patch(f"/intelligence/eval-suites/{sid}/tasks/{a['task_id']}",
                       json={"expected_output_contains": ["done"]})
    (task,) = await _tasks(client, sid)
    assert task["forbidden_tools"] == ["db.drop"] and task["min_score"] == 0.6
    assert task["expected_tools"] == ["kb.search"]
    assert task["expected_output_contains"] == ["done"]


async def test_an_edit_that_removes_every_check_is_refused(client: AsyncClient) -> None:
    sid = await _suite(client)
    a = await _add(client, sid, "g")
    r = await client.patch(f"/intelligence/eval-suites/{sid}/tasks/{a['task_id']}",
                           json={"expected_tools": []})
    assert r.status_code == 422
    suite = (await client.get(f"/intelligence/eval-suites/{sid}")).json()
    assert suite["dataset_version"] == 1


async def test_unknown_task_is_404(client: AsyncClient) -> None:
    sid = await _suite(client)
    assert (await client.patch(f"/intelligence/eval-suites/{sid}/tasks/nope",
                               json={"goal": "x"})).status_code == 404
    assert (await client.delete(f"/intelligence/eval-suites/{sid}/tasks/nope")).status_code == 404


async def test_a_run_records_the_dataset_version_and_runs_that_versions_tasks(
    client: AsyncClient,
) -> None:
    sid = await _suite(client)
    await _add(client, sid, "one")
    await _add(client, sid, "two")
    r = await client.post(f"/intelligence/eval-suites/{sid}/run")
    assert r.status_code == 202 and r.json()["dataset_version"] == 2
    import asyncio

    for _ in range(300):
        runs = (await client.get(f"/intelligence/eval-suites/{sid}/results")).json()
        if runs[0]["status"] == "completed":
            break
        await asyncio.sleep(0.02)
    assert runs[0]["dataset_version"] == 2 and runs[0]["status"] == "completed"
    goals = client.goals  # type: ignore[attr-defined]
    assert sorted(s["goal"] for s in goals.submits) == ["one", "two"]


async def test_export_then_import_round_trips_a_dataset(client: AsyncClient) -> None:
    src = await _suite(client)
    await _add(client, src, "alpha", expected_output="A")
    await _add(client, src, "beta", min_score=0.5)
    doc = (await client.get(f"/intelligence/eval-suites/{src}/export")).json()
    assert doc["dataset_version"] == 2 and doc["task_count"] == 2

    dst = await _suite(client)
    r = await client.post(f"/intelligence/eval-suites/{dst}/import", json={"tasks": doc["tasks"]})
    assert r.status_code == 201, r.text
    assert r.json()["dataset_version"] == 1  # one import = one version
    copied = await _tasks(client, dst)
    assert [(t["goal"], t["min_score"]) for t in copied] == [("alpha", 0.8), ("beta", 0.5)]
    assert [t["task_id"] for t in copied] == [t["task_id"] for t in doc["tasks"]]

    # Importing the same ids again (append) conflicts; replace swaps the dataset.
    again = await client.post(f"/intelligence/eval-suites/{dst}/import",
                              json={"tasks": doc["tasks"][:1]})
    assert again.status_code == 409
    r = await client.post(
        f"/intelligence/eval-suites/{dst}/import",
        json={"tasks": [{"goal": "only", "expected_tools": ["x"]}], "replace": True},
    )
    assert r.status_code == 201 and r.json()["dataset_version"] == 2
    assert [t["goal"] for t in await _tasks(client, dst)] == ["only"]
    assert len(await _tasks(client, dst, 1)) == 2


async def test_import_refuses_a_task_without_checks(client: AsyncClient) -> None:
    sid = await _suite(client)
    r = await client.post(f"/intelligence/eval-suites/{sid}/import",
                          json={"tasks": [{"goal": "nothing to check"}]})
    assert r.status_code == 422


async def test_export_of_a_version_that_does_not_exist_is_404(client: AsyncClient) -> None:
    sid = await _suite(client)
    assert (await client.get(f"/intelligence/eval-suites/{sid}/export",
                             params={"version": 9})).status_code == 404


async def test_store_pages_large_datasets() -> None:
    store = EvalSuiteStore(None, "page-tenant")
    await store.create("big", name="big", description="")
    await store.import_tasks(
        "big", [{"goal": f"g{i}", "expected_tools": ["t"]} for i in range(1200)], replace=False
    )
    first = await store.list_tasks("big", limit=500)
    third = await store.list_tasks("big", limit=500, offset=1000)
    assert len(first) == 500 and len(third) == 200
    assert [t async for t in store.iter_tasks("big", 1)][-1]["goal"] == "g1199"
    suite = await store.get("big")
    assert suite is not None and suite["tasks_truncated"] is True
    assert len(suite["tasks"]) == 200
