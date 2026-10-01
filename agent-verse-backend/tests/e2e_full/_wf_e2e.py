"""Shared helpers for the workflow e2e_full tests that drive a real Celery worker."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

API = "/api/v1"
SETTLED = {"complete", "failed", "cancelled", "timed_out", "paused", "waiting_hitl"}


async def create_workflow(tenant_client: Any, definition: dict[str, Any]) -> str:
    resp = await tenant_client.post(
        f"{API}/workflows",
        json={"name": f"e2e-{uuid.uuid4().hex[:8]}", "definition": definition},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def trigger(tenant_client: Any, workflow_id: str, inputs: dict[str, Any] | None = None) -> str:
    resp = await tenant_client.post(
        f"{API}/workflows/{workflow_id}/trigger", json={"inputs": inputs or {}}
    )
    assert resp.status_code == 202, resp.text
    return str(resp.json()["run_id"])


async def poll_run(
    tenant_client: Any, run_id: str, wanted: set[str] | None = None, *, timeout: float = 60.0
) -> dict[str, Any]:
    wanted = wanted or SETTLED
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        resp = await tenant_client.get(f"{API}/runs/{run_id}")
        if resp.status_code == 200:
            last = resp.json()
            if str(last.get("status")) in wanted:
                return last
        await asyncio.sleep(0.5)
    raise AssertionError(f"run {run_id} did not reach {wanted} in {timeout}s; last={last!r}")


async def pending_approval(tenant_client: Any, run_id: str, *, timeout: float = 30.0) -> str:
    deadline = asyncio.get_event_loop().time() + timeout
    last: Any = None
    while asyncio.get_event_loop().time() < deadline:
        resp = await tenant_client.get(f"{API}/approvals")
        if resp.status_code == 200:
            last = resp.json()
            for item in last.get("items", []):
                if item.get("run_id") == run_id and item.get("status") == "pending":
                    return str(item["request_id"])
        await asyncio.sleep(0.5)
    raise AssertionError(f"no pending approval for run {run_id}; last={last!r}")


async def steps_by_id(tenant_client: Any, run_id: str) -> dict[str, dict[str, Any]]:
    resp = await tenant_client.get(f"{API}/runs/{run_id}/steps")
    assert resp.status_code == 200, resp.text
    return {str(s["step_id"]): s for s in resp.json()}


def gate(step_id: str = "gate", **extra: Any) -> dict[str, Any]:
    return {
        "id": step_id,
        "type": "hitl",
        "assignee": {"strategy": "specific", "specific_user": "anonymous"},
        "actions": [{"id": "approve"}, {"id": "reject"}],
        **extra,
    }
