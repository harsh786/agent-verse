"""WF-ROUTES-500: version detail / diff and the publish-approval flow.

``router_versions`` called ``WorkflowService.get_version``, ``diff_versions``,
``submit_for_approval``, ``approve_publish`` and ``reject_publish`` — none of
which existed, so every one of those routes answered 500. Nothing ever wrote
``workflow_definition_versions`` either, so the version list was always empty.

These tests use the real ``WorkflowService`` over the in-memory
``_WorkflowStore`` and a fake run store that keeps versions and the approval
state the way the Postgres store does.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.api.workflows import _WorkflowStore
from app.workflow.service import (
    PublishApprovalRequiredError,
    WorkflowPersistenceUnavailableError,
    WorkflowService,
)

pytestmark = pytest.mark.asyncio

TENANT = "11111111-1111-1111-1111-111111111111"
ALICE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
BOB = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _definition(*steps: dict[str, Any], trigger: str = "api") -> dict[str, Any]:
    return {"name": "wf", "trigger": {"type": trigger}, "steps": list(steps)}


class _RunStore:
    """In-memory stand-in for the PostgresWorkflowRunStore version/approval API."""

    def __init__(self) -> None:
        self.versions: dict[tuple[str, str], dict[str, Any]] = {}
        self.approval: dict[str, dict[str, Any]] = {}

    async def record_definition_version(
        self,
        tenant_id: str,
        workflow_id: str,
        *,
        version: str,
        definition: dict[str, Any],
        published_by: str | None = None,
        change_summary: str | None = None,
    ) -> dict[str, Any]:
        row = {
            "version": version,
            "definition_json": copy.deepcopy(definition),
            "definition_yaml": "",
            "change_summary": change_summary,
            "published_by": published_by,
            "published_at": f"t{len(self.versions)}",
        }
        self.versions[(workflow_id, version)] = row
        return row

    async def list_versions(self, tenant_id: str, workflow_id: str) -> list[dict[str, Any]]:
        return [v for (wid, _), v in self.versions.items() if wid == workflow_id]

    async def get_definition_version(
        self, tenant_id: str, workflow_id: str, version: str
    ) -> dict[str, Any] | None:
        return self.versions.get((workflow_id, version))

    async def get_publish_approval(self, tenant_id: str, workflow_id: str) -> dict[str, Any]:
        return copy.deepcopy(
            self.approval.get(
                workflow_id,
                {
                    "requires_publish_approval": False,
                    "submission": None,
                    "approved_by": None,
                    "approved_at": None,
                    "note": None,
                },
            )
        )

    async def set_requires_publish_approval(
        self, tenant_id: str, workflow_id: str, required: bool
    ) -> bool:
        state = await self.get_publish_approval(tenant_id, workflow_id)
        state["requires_publish_approval"] = required
        self.approval[workflow_id] = state
        return True

    async def set_publish_submission(
        self, tenant_id: str, workflow_id: str, submission: dict[str, Any] | None
    ) -> bool:
        state = await self.get_publish_approval(tenant_id, workflow_id)
        state["submission"] = submission
        self.approval[workflow_id] = state
        return True

    async def record_publish_approval(
        self, tenant_id: str, workflow_id: str, *, approved_by: str, note: str
    ) -> bool:
        state = await self.get_publish_approval(tenant_id, workflow_id)
        state.update(approved_by=approved_by, approved_at="now", note=note, submission=None)
        self.approval[workflow_id] = state
        return True

    async def get_webhook_token_version(self, tenant_id: str, workflow_id: str) -> int:
        return 0


async def _svc() -> tuple[WorkflowService, _RunStore]:
    run_store = _RunStore()
    return WorkflowService(_WorkflowStore(), run_store=run_store), run_store


async def _create(svc: WorkflowService, definition: dict[str, Any]) -> str:
    wf = await svc.create(tenant_id=TENANT, name="wf", definition=definition)
    return str(wf["id"])


# ── Versions ──────────────────────────────────────────────────────────────────


async def test_publish_records_a_version_snapshot() -> None:
    svc, _ = await _svc()
    wid = await _create(svc, _definition({"id": "a", "type": "noop"}))
    published = await svc.publish(tenant_id=TENANT, workflow_id=wid, published_by=ALICE)
    assert published is not None

    versions = await svc.list_versions(tenant_id=TENANT, workflow_id=wid)
    assert [v["version"] for v in versions] == [str(published["version"])]

    detail = await svc.get_version(
        tenant_id=TENANT, workflow_id=wid, version=int(published["version"])
    )
    assert detail is not None
    assert detail["definition"]["steps"] == [{"id": "a", "type": "noop"}]
    assert detail["published_by"] == ALICE


async def test_get_version_unknown_is_none() -> None:
    svc, _ = await _svc()
    wid = await _create(svc, _definition())
    assert await svc.get_version(tenant_id=TENANT, workflow_id=wid, version=42) is None


async def test_get_version_without_run_store_is_none() -> None:
    svc = WorkflowService(_WorkflowStore(), run_store=None)
    assert await svc.get_version(tenant_id=TENANT, workflow_id="wf", version=1) is None


async def test_diff_versions_reports_step_trigger_and_input_changes() -> None:
    svc, _ = await _svc()
    wid = await _create(
        svc,
        {
            **_definition({"id": "a", "type": "noop"}, {"id": "b", "type": "noop"}),
            "inputs": {"x": {"type": "string"}, "gone": {"type": "string"}},
        },
    )
    v1 = await svc.publish(tenant_id=TENANT, workflow_id=wid)
    assert v1 is not None
    ver1 = int(v1["version"])  # the in-memory store returns its live dict
    await svc.unpublish(tenant_id=TENANT, workflow_id=wid)
    await svc.update(
        tenant_id=TENANT,
        workflow_id=wid,
        updates={
            "definition": {
                **_definition(
                    {"id": "a", "type": "noop", "config": {"k": 1}},
                    {"id": "c", "type": "noop"},
                    trigger="webhook",
                ),
                "inputs": {"x": {"type": "integer"}, "new": {"type": "string"}},
            }
        },
    )
    v2 = await svc.publish(tenant_id=TENANT, workflow_id=wid)
    assert v2 is not None

    diff = await svc.diff_versions(
        tenant_id=TENANT,
        workflow_id=wid,
        version_a=ver1,
        version_b=int(v2["version"]),
    )
    assert diff["added_steps"] == ["c"]
    assert diff["removed_steps"] == ["b"]
    assert diff["modified_steps"] == ["a"]
    assert diff["trigger_changed"] is True
    assert {(c["name"], c["change"]) for c in diff["input_changes"]} == {
        ("gone", "removed"),
        ("new", "added"),
        ("x", "modified"),
    }


async def test_diff_with_unknown_version_raises_value_error() -> None:
    svc, _ = await _svc()
    wid = await _create(svc, _definition())
    v1 = await svc.publish(tenant_id=TENANT, workflow_id=wid)
    assert v1 is not None
    with pytest.raises(ValueError, match="999"):
        await svc.diff_versions(
            tenant_id=TENANT, workflow_id=wid, version_a=int(v1["version"]), version_b=999
        )


# ── Publish approval ──────────────────────────────────────────────────────────


async def test_publish_is_refused_while_approval_is_required() -> None:
    svc, _ = await _svc()
    wid = await _create(svc, _definition())
    await svc.set_requires_publish_approval(tenant_id=TENANT, workflow_id=wid, required=True)
    with pytest.raises(PublishApprovalRequiredError):
        await svc.publish(tenant_id=TENANT, workflow_id=wid)
    wf = await svc.get(tenant_id=TENANT, workflow_id=wid)
    assert wf is not None and wf["status"] == "draft"
    assert wf["requires_publish_approval"] is True


async def test_submit_approve_publishes_and_records_approver() -> None:
    svc, run_store = await _svc()
    wid = await _create(svc, _definition())
    await svc.set_requires_publish_approval(tenant_id=TENANT, workflow_id=wid, required=True)

    submitted = await svc.submit_for_approval(
        tenant_id=TENANT, workflow_id=wid, submitted_by=ALICE
    )
    assert submitted is not None and submitted["status"] == "pending_approval"

    approved = await svc.approve_publish(
        tenant_id=TENANT, workflow_id=wid, approver_id=BOB, note="LGTM"
    )
    assert approved is not None
    assert approved["status"] == "published"
    assert approved["publish_approved_by"] == BOB
    assert approved["publish_approval_note"] == "LGTM"
    assert run_store.approval[wid]["approved_by"] == BOB
    # The approved publish is a recorded version, attributed to the approver.
    versions = await svc.list_versions(tenant_id=TENANT, workflow_id=wid)
    assert [v["version"] for v in versions] == [str(approved["version"])]


async def test_submitter_cannot_approve_their_own_submission() -> None:
    svc, _ = await _svc()
    wid = await _create(svc, _definition())
    await svc.set_requires_publish_approval(tenant_id=TENANT, workflow_id=wid, required=True)
    await svc.submit_for_approval(tenant_id=TENANT, workflow_id=wid, submitted_by=ALICE)
    with pytest.raises(ValueError, match="submitter"):
        await svc.approve_publish(tenant_id=TENANT, workflow_id=wid, approver_id=ALICE, note="")
    wf = await svc.get(tenant_id=TENANT, workflow_id=wid)
    assert wf is not None and wf["status"] == "pending_approval"


async def test_edits_are_refused_while_pending_approval() -> None:
    svc, _ = await _svc()
    wid = await _create(svc, _definition())
    await svc.set_requires_publish_approval(tenant_id=TENANT, workflow_id=wid, required=True)
    await svc.submit_for_approval(tenant_id=TENANT, workflow_id=wid, submitted_by=ALICE)
    with pytest.raises(ValueError, match="pending"):
        await svc.update(
            tenant_id=TENANT,
            workflow_id=wid,
            updates={"definition": _definition({"id": "sneaky", "type": "noop"})},
        )


async def test_approve_refuses_a_definition_changed_after_submission() -> None:
    svc, _ = await _svc()
    store = _WorkflowStore()
    run_store = _RunStore()
    svc = WorkflowService(store, run_store=run_store)
    wid = await _create(svc, _definition())
    await svc.set_requires_publish_approval(tenant_id=TENANT, workflow_id=wid, required=True)
    await svc.submit_for_approval(tenant_id=TENANT, workflow_id=wid, submitted_by=ALICE)
    # A write that bypasses the service (e.g. the legacy PUT /workflows/{id}).
    await store.update(tenant_id=TENANT, workflow_id=wid, definition=_definition())
    with pytest.raises(ValueError, match="changed"):
        await svc.approve_publish(tenant_id=TENANT, workflow_id=wid, approver_id=BOB, note="")


async def test_reject_returns_to_draft() -> None:
    svc, run_store = await _svc()
    wid = await _create(svc, _definition())
    await svc.set_requires_publish_approval(tenant_id=TENANT, workflow_id=wid, required=True)
    await svc.submit_for_approval(tenant_id=TENANT, workflow_id=wid, submitted_by=ALICE)
    rejected = await svc.reject_publish(
        tenant_id=TENANT, workflow_id=wid, approver_id=BOB, note="needs work"
    )
    assert rejected is not None
    assert rejected["status"] == "draft"
    assert rejected["rejected_by"] == BOB
    assert run_store.approval[wid]["submission"] is None


async def test_submit_requires_approval_to_be_enabled_and_a_draft() -> None:
    svc, _ = await _svc()
    wid = await _create(svc, _definition())
    with pytest.raises(ValueError, match="does not require"):
        await svc.submit_for_approval(tenant_id=TENANT, workflow_id=wid, submitted_by=ALICE)
    with pytest.raises(ValueError, match="pending"):
        await svc.approve_publish(tenant_id=TENANT, workflow_id=wid, approver_id=BOB, note="")


async def test_submit_unknown_workflow_is_none() -> None:
    svc, _ = await _svc()
    assert (
        await svc.submit_for_approval(tenant_id=TENANT, workflow_id="nope", submitted_by=ALICE)
        is None
    )


async def test_approval_flow_without_run_store_is_unavailable() -> None:
    svc = WorkflowService(_WorkflowStore(), run_store=None)
    wid = await _create(svc, _definition())
    with pytest.raises(WorkflowPersistenceUnavailableError):
        await svc.submit_for_approval(tenant_id=TENANT, workflow_id=wid, submitted_by=ALICE)
    with pytest.raises(WorkflowPersistenceUnavailableError):
        await svc.set_requires_publish_approval(
            tenant_id=TENANT, workflow_id=wid, required=True
        )
