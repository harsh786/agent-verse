"""Publishing refuses a definition that cannot work once live (422 via ValueError)."""

from __future__ import annotations

from typing import Any

import pytest

from app.api.workflows import _WorkflowStore
from app.workflow.service import WorkflowService


async def _publish(definition: dict[str, Any]) -> tuple[WorkflowService, str]:
    svc = WorkflowService(store=_WorkflowStore())
    wf = await svc.create(tenant_id="t1", name="wf", definition=definition)
    return svc, str(wf["id"])


def _gate(strategy: str) -> dict[str, Any]:
    return {
        "id": "gate",
        "type": "hitl",
        "assignee": {"role": "finance", "strategy": strategy},
        "actions": [{"id": "approve"}, {"id": "reject"}],
    }


# ── WF-04: skill_based assignment has no skills registry ─────────────────────


async def test_publish_rejects_skill_based_assignment() -> None:
    svc, wid = await _publish({"steps": [_gate("skill_based")]})
    with pytest.raises(ValueError, match="skill_based"):
        await svc.publish(tenant_id="t1", workflow_id=wid)
    item = await svc.get(tenant_id="t1", workflow_id=wid)
    assert item is not None and item["status"] == "draft"


@pytest.mark.parametrize("strategy", ["round_robin", "least_busy", "specific"])
async def test_publish_accepts_supported_assignment(strategy: str) -> None:
    svc, wid = await _publish({"steps": [_gate(strategy)]})
    out = await svc.publish(tenant_id="t1", workflow_id=wid)
    assert out is not None and out["status"] == "published"


async def test_publish_unknown_workflow_returns_none() -> None:
    svc = WorkflowService(store=_WorkflowStore())
    assert await svc.publish(tenant_id="t1", workflow_id="nope") is None
