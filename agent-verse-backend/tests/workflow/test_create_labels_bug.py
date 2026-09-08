"""WT-E2 finding: POST /workflows returned 500 on two counts —

1. ``WorkflowService.create`` passes ``labels=`` to ``_WorkflowStore.create``,
   which had no ``labels`` parameter -> ``TypeError``.
2. ``WorkflowResponse.labels`` (``dict[str, str]``) is a required field, but the
   store record never carried a ``labels`` key, so response validation failed
   even when create succeeded. ``WorkflowService.create`` also coerced ``{}`` to
   ``[]`` (``labels or []``), which would fail the ``dict[str, str]`` response
   schema.

The canonical label type is ``dict[str, str]`` (see WorkflowCreateRequest /
WorkflowResponse in app/workflow/router.py).
"""

from __future__ import annotations

from app.api.workflows import _WorkflowStore
from app.workflow.service import WorkflowService


async def test_workflow_store_create_accepts_and_carries_labels() -> None:
    store = _WorkflowStore()  # in-memory (no db)
    wf = await store.create(
        tenant_id="t1",
        name="wf",
        description="d",
        definition={"steps": []},
        labels={"team": "ops", "env": "prod"},
    )
    assert wf["labels"] == {"team": "ops", "env": "prod"}


async def test_workflow_store_create_defaults_labels_to_empty_dict() -> None:
    store = _WorkflowStore()
    wf = await store.create(tenant_id="t1", name="wf", description="", definition={})
    # Required by WorkflowResponse.labels (dict[str, str]) — never a list.
    assert wf["labels"] == {}


async def test_service_create_delegates_labels_without_typeerror() -> None:
    svc = WorkflowService(store=_WorkflowStore())
    wf = await svc.create(
        tenant_id="t1",
        name="wf2",
        description="",
        definition={"steps": []},
        labels={"x": "1"},
    )
    assert wf["name"] == "wf2"
    assert wf["labels"] == {"x": "1"}


async def test_service_create_empty_labels_is_dict_not_list() -> None:
    svc = WorkflowService(store=_WorkflowStore())
    wf = await svc.create(tenant_id="t1", name="wf3", description="", definition={})
    assert wf["labels"] == {}  # not [] — must satisfy dict[str, str] response schema
