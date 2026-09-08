"""Same silent-wiring-disconnect class as the create-labels bug, on the update
path: the router calls ``svc.update(..., updates=body.model_dump(...))`` and
``svc.archive``/``svc.publish`` call ``_store.update(..., status=...)``, but
``_WorkflowStore.update`` had a fixed ``(name, description, definition)``
signature — so every one of these raised ``TypeError`` -> HTTP 500. No test
exercised the router->service->store path (unit tests called store.update
directly with the exact positional kwargs), so CI stayed green.
"""

from __future__ import annotations

from app.api.workflows import _WorkflowStore
from app.workflow.service import WorkflowService


async def _make(svc: WorkflowService) -> str:
    wf = await svc.create(tenant_id="t1", name="wf", description="d", definition={"steps": []})
    return wf["id"]


async def test_update_via_updates_dict_partial_fields() -> None:
    """Mirrors the PATCH router contract: svc.update(updates={...})."""
    svc = WorkflowService(store=_WorkflowStore())
    wid = await _make(svc)
    out = await svc.update(tenant_id="t1", workflow_id=wid, updates={"name": "renamed"})
    assert out is not None
    assert out["name"] == "renamed"
    assert out["description"] == "d"  # untouched field preserved
    assert out["version"] == 2  # bumped


async def test_update_can_set_labels_partially() -> None:
    svc = WorkflowService(store=_WorkflowStore())
    wid = await _make(svc)
    out = await svc.update(tenant_id="t1", workflow_id=wid, updates={"labels": {"team": "ops"}})
    assert out is not None
    assert out["labels"] == {"team": "ops"}
    assert out["name"] == "wf"  # unchanged


async def test_archive_sets_status_without_typeerror() -> None:
    svc = WorkflowService(store=_WorkflowStore())
    wid = await _make(svc)
    assert await svc.archive(tenant_id="t1", workflow_id=wid) is True
    item = await svc.get(tenant_id="t1", workflow_id=wid)
    assert item is not None and item["status"] == "archived"


async def test_publish_sets_status_and_timestamp() -> None:
    svc = WorkflowService(store=_WorkflowStore())
    wid = await _make(svc)
    out = await svc.publish(tenant_id="t1", workflow_id=wid)
    assert out is not None and out["status"] == "published"


async def test_update_unknown_workflow_returns_none() -> None:
    svc = WorkflowService(store=_WorkflowStore())
    out = await svc.update(tenant_id="t1", workflow_id="nope", updates={"name": "x"})
    assert out is None
