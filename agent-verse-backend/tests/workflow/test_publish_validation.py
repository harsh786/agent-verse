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


# ── WF-08: triggers with no firing path ───────────────────────────────────────


@pytest.mark.parametrize(
    "definition",
    [
        {"steps": [], "trigger": {"type": "event"}},  # DSL singular shape
        {"steps": [], "triggers": [{"type": "api"}, {"type": "file_drop"}]},  # builder shape
        {"steps": [], "trigger": {"type": "pagerduty"}},
    ],
)
async def test_publish_rejects_unsupported_trigger_types(definition: dict[str, Any]) -> None:
    svc, wid = await _publish(definition)
    with pytest.raises(ValueError, match="is not supported yet") as exc:
        await svc.publish(tenant_id="t1", workflow_id=wid)
    unsupported = (definition.get("trigger") or definition["triggers"][-1])["type"]
    assert repr(unsupported) in str(exc.value)
    item = await svc.get(tenant_id="t1", workflow_id=wid)
    assert item is not None and item["status"] == "draft"


async def test_publish_rejects_schedule_without_cron() -> None:
    svc, wid = await _publish({"steps": [], "trigger": {"type": "schedule", "schedule": {}}})
    with pytest.raises(ValueError, match="no cron"):
        await svc.publish(tenant_id="t1", workflow_id=wid)


@pytest.mark.parametrize(
    "trigger",
    [
        {"type": "schedule", "schedule": {"cron": "0 9 * * *"}},
        {"type": "webhook"},
        {"type": "api"},
    ],
)
async def test_publish_accepts_supported_triggers(trigger: dict[str, Any]) -> None:
    svc, wid = await _publish({"steps": [], "trigger": trigger})
    out = await svc.publish(tenant_id="t1", workflow_id=wid)
    assert out is not None and out["status"] == "published"


def test_publish_route_answers_422_naming_the_trigger_type() -> None:
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from app.tenancy.context import PlanTier, TenantContext
    from app.workflow.router import router

    svc = WorkflowService(store=_WorkflowStore())
    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        request.state.tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k")
        request.app.state.workflow_service = svc
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    client = TestClient(app)
    created = client.post(
        "/api/v1/workflows",
        json={"name": "wf", "definition": {"name": "wf", "steps": [], "trigger": {"type": "event"}}},
    )
    assert created.status_code == 201, created.text
    wid = created.json()["id"]

    resp = client.post(f"/api/v1/workflows/{wid}/publish")
    assert resp.status_code == 422
    assert "'event'" in resp.json()["detail"]
    check = client.post(f"/api/v1/workflows/{wid}/validate").json()
    assert check["valid"] is False and any("'event'" in e for e in check["errors"])


async def test_publish_unknown_workflow_returns_none() -> None:
    svc = WorkflowService(store=_WorkflowStore())
    assert await svc.publish(tenant_id="t1", workflow_id="nope") is None
