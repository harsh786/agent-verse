"""WF-30: a published workflow is immutable; runs pin the version they started on.

PATCH /api/v1/workflows/{id}, legacy PUT /workflows/{id} and restore-version
rewrote the live definition of a PUBLISHED workflow in place: new and resumed
runs used it at once with no version snapshot and no publish re-approval.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.api.workflows import _WorkflowStore
from app.workflow.service import WorkflowPublishedError, WorkflowService
from tests.workflow.test_service_versions_approval import TENANT, _definition, _RunStore


async def _published() -> tuple[WorkflowService, str]:
    svc = WorkflowService(_WorkflowStore(), run_store=_RunStore())
    wf = await svc.create(
        tenant_id=TENANT, name="wf", definition=_definition({"id": "a", "type": "noop"})
    )
    wid = str(wf["id"])
    assert await svc.publish(tenant_id=TENANT, workflow_id=wid) is not None
    return svc, wid


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "updates",
    [{"definition": _definition({"id": "b", "type": "noop"})}, {"name": "renamed"}],
)
async def test_definition_or_name_edit_of_published_is_refused(updates: dict[str, Any]) -> None:
    svc, wid = await _published()
    with pytest.raises(WorkflowPublishedError, match="unpublish"):
        await svc.update(tenant_id=TENANT, workflow_id=wid, updates=updates)
    current = await svc.get(tenant_id=TENANT, workflow_id=wid)
    assert current is not None
    assert current["definition"]["steps"] == [{"id": "a", "type": "noop"}]
    assert current["name"] == "wf"


@pytest.mark.asyncio
async def test_published_metadata_edit_is_allowed() -> None:
    svc, wid = await _published()
    out = await svc.update(tenant_id=TENANT, workflow_id=wid, updates={"description": "d"})
    assert out is not None and out["description"] == "d"


@pytest.mark.asyncio
async def test_restore_version_onto_published_is_refused() -> None:
    svc, wid = await _published()
    versions = await svc.list_versions(tenant_id=TENANT, workflow_id=wid)
    with pytest.raises(WorkflowPublishedError):
        await svc.restore_version(
            tenant_id=TENANT, workflow_id=wid, version=versions[0]["version"]
        )


@pytest.mark.asyncio
async def test_unpublished_workflow_can_be_edited_again() -> None:
    svc, wid = await _published()
    await svc.unpublish(tenant_id=TENANT, workflow_id=wid)
    out = await svc.update(
        tenant_id=TENANT,
        workflow_id=wid,
        updates={"definition": _definition({"id": "b", "type": "noop"})},
    )
    assert out is not None


def test_router_patch_on_published_is_409() -> None:
    from fastapi import FastAPI, HTTPException

    from app.workflow import router as wf_router

    svc, wid = asyncio.run(_published())

    class _Req:
        app = FastAPI()
        state = type("S", (), {})()

    req: Any = _Req()
    req.app.state.workflow_service = svc
    req.state.tenant = type("T", (), {"tenant_id": TENANT, "api_key_id": "k"})()
    body = wf_router.WorkflowUpdateRequest(definition={"name": "x", "steps": []})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wf_router.update_workflow(wid, body, req))
    assert exc.value.status_code == 409
    assert "unpublish" in str(exc.value.detail)


def test_legacy_put_on_published_is_409() -> None:
    from tests.api.test_workflows_legacy_acl import _BODY, _T, _client, _create

    client, store, _ = _client()
    wid = _create(client)
    asyncio.run(store.update(tenant_id=_T, workflow_id=wid, status="published"))
    resp = client.put(f"/workflows/{wid}", json=_BODY)
    assert resp.status_code == 409, resp.text
    assert "unpublish" in resp.json()["detail"]


# ── Runs pin the published version ───────────────────────────────────────────


class _PinStore:
    """Run-store double: live definition + version snapshots + run rows."""

    def __init__(self) -> None:
        self.current: dict[str, Any] = _definition({"id": "v2step", "type": "transform"})
        self.versions = {
            "2": _definition({"id": "v1step", "type": "transform"}),
        }
        self.live_version: str | None = "2"
        self.runs: dict[str, dict[str, Any]] = {}

    async def get_live_definition(
        self, workflow_id: str, tenant_id: str
    ) -> tuple[dict[str, Any], str | None]:
        if self.live_version:
            return self.versions[self.live_version], self.live_version
        return self.current, None

    async def get_run_definition(
        self, tenant_id: str, run_id: str, workflow_id: str
    ) -> dict[str, Any]:
        pinned = (self.runs[run_id].get("run_metadata") or {}).get("definition_version")
        return self.versions[pinned] if pinned else self.current

    async def get_definition(self, workflow_id: str, tenant_id: str) -> dict[str, Any]:
        return self.current

    async def create(self, **kw: Any) -> str:
        self.runs[kw["run_id"]] = dict(kw)
        return str(kw["run_id"])

    async def get(self, tenant_id: str, run_id: str) -> dict[str, Any] | None:
        return self.runs.get(run_id)


@pytest.mark.asyncio
async def test_run_pins_the_published_version_and_reloads_it() -> None:
    from unittest.mock import MagicMock

    from app.workflow.runner import WorkflowRunner

    store = _PinStore()
    compiled: list[list[str]] = []
    compiler = MagicMock()
    compiler.compile.side_effect = lambda d: compiled.append([s.id for s in d.steps]) or MagicMock()
    runner = WorkflowRunner(compiler, run_store=store, celery_app=MagicMock())
    from unittest.mock import patch

    with patch("app.workflow.celery_tasks.execute_workflow_run") as task:
        run_id = await runner.run(workflow_id="w", tenant_id=TENANT, inputs={})
    assert task.apply_async.called
    assert store.runs[run_id]["run_metadata"]["definition_version"] == "2"

    # The workflow is later unpublished, edited and republished as v3.
    store.versions["3"] = store.current
    store.live_version = "3"
    definition = await runner._load_run_definition(run_id, "w", TENANT)
    assert [s.id for s in definition.steps] == ["v1step"]
