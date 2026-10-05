"""e2e_full (WF-01): a TWO-approval workflow resumes from persisted step results
in a real Celery worker — earlier steps are never replayed, and the second
approval finishes the run instead of re-suspending at the first gate.

Workflow: ``pre -> gate1 -> mid -> gate2 -> after``. ``pre`` is an ``emit_event``
step, so every execution of it publishes on Redis pub/sub — a real side effect
this test counts by subscribing to the channel before the run starts.

Old bug (Celery path): an approval step's decided result was persisted as
``running`` (the run status the node asked for), not ``complete``. The worker's
resume rebuilds the run from persisted step results and skips COMPLETE steps, so
on the gate2 resume it re-entered gate1 as a brand-new suspend, created a second
gate1 approval and parked the run at gate1 again — forever.

Everything runs out of process: the fresh run and both resumes execute in a
genuine ``celery ... worker`` subprocess bound to the same Postgres + Redis as
the booted app; the test only drives the HTTP API.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_API = "/api/v1"


@pytest.fixture(scope="module")
def celery_worker(app: Any, tmp_path_factory: Any) -> Iterator[dict[str, Any]]:
    """A real out-of-process Celery worker on the workflow queues, bound to the
    SAME Postgres + Redis the booted app uses (the session ``app`` fixture
    exported ``DATABASE_URL`` / ``REDIS_URL``)."""
    # Shared helper (USR-7): the whole process group is stopped on any exit,
    # even if the worker's main process already died.
    from tests.e2e_full._wf_worker import workflow_worker

    with workflow_worker(tmp_path_factory.mktemp("twogateworker"), name="twogatee2e") as worker:
        yield worker


def _gate(step_id: str, dep: str) -> dict[str, Any]:
    return {
        "id": step_id,
        "type": "hitl",
        "depends_on": [dep],
        # Assigned to the default API caller so GET /approvals surfaces it.
        "assignee": {"strategy": "specific", "specific_user": "anonymous"},
        "actions": [{"id": "approve"}, {"id": "reject"}],
    }


async def _create_workflow(tenant_client: Any, channel: str) -> str:
    body = {
        "name": f"twogate-{uuid.uuid4().hex[:8]}",
        "description": "two approval gates with a side-effecting first step",
        "definition": {
            "name": "Two-gate WF",
            "steps": [
                {
                    "id": "pre",
                    "type": "emit_event",
                    "event_channel_out": channel,
                    "event_payload": {"from": "pre"},
                },
                _gate("gate1", "pre"),
                {"id": "mid", "type": "transform", "input": {"m": 1}, "depends_on": ["gate1"]},
                _gate("gate2", "mid"),
                {"id": "after", "type": "transform", "input": {"a": 1}, "depends_on": ["gate2"]},
            ],
        },
    }
    resp = await tenant_client.post(f"{_API}/workflows", json=body)
    assert resp.status_code == 201, f"create failed: {resp.status_code} {resp.text}"
    return str(resp.json()["id"])


async def _poll_run_status(
    tenant_client: Any, run_id: str, wanted: set[str], *, timeout: float = 60.0
) -> dict[str, Any]:
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        resp = await tenant_client.get(f"{_API}/runs/{run_id}")
        if resp.status_code == 200:
            last = resp.json()
            if str(last.get("status")) in wanted:
                return last
        await asyncio.sleep(0.5)
    raise AssertionError(f"run {run_id} did not reach {wanted} within {timeout}s; last={last!r}")


async def _pending_for_run(tenant_client: Any, run_id: str) -> list[dict[str, Any]]:
    resp = await tenant_client.get(f"{_API}/approvals", params={"per_page": 100})
    assert resp.status_code == 200, f"{resp.status_code} {resp.text}"
    return [
        item
        for item in resp.json().get("items", [])
        if item.get("run_id") == run_id and item.get("status") == "pending"
    ]


async def _wait_pending_step(
    tenant_client: Any, run_id: str, step_id: str, *, timeout: float = 30.0
) -> dict[str, Any]:
    deadline = asyncio.get_event_loop().time() + timeout
    pending: list[dict[str, Any]] = []
    while asyncio.get_event_loop().time() < deadline:
        pending = await _pending_for_run(tenant_client, run_id)
        match = [p for p in pending if p.get("step_id") == step_id]
        if match:
            return match[0]
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"no pending {step_id!r} approval for run {run_id} within {timeout}s; pending={pending!r}"
    )


async def _approve(tenant_client: Any, request_id: str) -> None:
    resp = await tenant_client.post(
        f"{_API}/approvals/{request_id}/decide", json={"action": "approve", "note": "ok"}
    )
    assert resp.status_code == 200, f"decide failed: {resp.status_code} {resp.text}"


async def test_two_gate_workflow_resumes_without_replaying_earlier_steps(
    tenant_client: Any, celery_worker: dict[str, Any]
) -> None:
    import redis.asyncio as aioredis

    channel = f"wf01-{uuid.uuid4().hex[:10]}"
    workflow_id = await _create_workflow(tenant_client, channel)

    # Count every execution of ``pre`` by its real side effect: a pub/sub message
    # on the tenant-namespaced channel (``wf_event:<tenant>:<channel>``).
    redis = aioredis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    pubsub = redis.pubsub()
    await pubsub.psubscribe(f"wf_event:*:{channel}")
    await pubsub.get_message(timeout=1.0)  # the psubscribe confirmation
    side_effects = 0

    async def _drain() -> None:
        nonlocal side_effects
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if msg is None:
                return
            if msg.get("type") == "pmessage":
                side_effects += 1

    try:
        trig = await tenant_client.post(
            f"{_API}/workflows/{workflow_id}/trigger", json={"inputs": {}, "dry_run": False}
        )
        assert trig.status_code == 202, f"trigger failed: {trig.status_code} {trig.text}"
        run_id = trig.json()["run_id"]

        # ── Paused at gate1 in the worker; pre ran once ───────────────────────
        await _poll_run_status(tenant_client, run_id, {"waiting_hitl"})
        gate1 = await _wait_pending_step(tenant_client, run_id, "gate1")
        await _drain()
        assert side_effects == 1, f"pre should have run once, saw {side_effects}"

        # ── Approve gate1 → resumes in the worker and parks at gate2 ──────────
        await _approve(tenant_client, str(gate1["request_id"]))
        gate2 = await _wait_pending_step(tenant_client, run_id, "gate2")
        await _poll_run_status(tenant_client, run_id, {"waiting_hitl"})
        await _drain()
        assert side_effects == 1, f"approving gate1 replayed pre ({side_effects} runs)"
        pending = await _pending_for_run(tenant_client, run_id)
        assert [p["step_id"] for p in pending] == ["gate2"], pending

        # ── Approve gate2 → the run finishes (old bug: re-suspended at gate1) ─
        await _approve(tenant_client, str(gate2["request_id"]))
        done = await _poll_run_status(tenant_client, run_id, {"complete", "waiting_hitl"})
        # A waiting_hitl here is the old loop; give it a moment to show itself.
        if done["status"] != "complete":
            done = await _poll_run_status(tenant_client, run_id, {"complete"}, timeout=20.0)
        assert done["status"] == "complete", done
        await asyncio.sleep(1.0)  # let any (buggy) re-suspend land before asserting
        assert await _pending_for_run(tenant_client, run_id) == [], (
            "no approval may be created after the final decision"
        )
        await _drain()
        assert side_effects == 1, f"pre replayed across resumes ({side_effects} runs)"

        # Each step executed exactly once in the durable step history.
        steps = await tenant_client.get(f"{_API}/runs/{run_id}/steps")
        assert steps.status_code == 200, f"{steps.status_code} {steps.text}"
        rows = steps.json()
        for step_id in ("pre", "mid", "after"):
            got = [r for r in rows if r["step_id"] == step_id]
            assert len(got) == 1, f"{step_id} executed {len(got)} times: {rows!r}"
            assert got[0]["status"] == "complete", got
        for gate in ("gate1", "gate2"):
            latest = [r for r in rows if r["step_id"] == gate][-1]
            assert latest["status"] == "complete", rows
            assert (latest.get("output") or {}).get("action") == "approve", rows
    finally:
        with contextlib.suppress(Exception):
            await pubsub.punsubscribe()
            await pubsub.aclose()
            await redis.aclose()

    log_text = celery_worker["log_path"].read_text()
    assert "workflow.execute_workflow_run" in log_text, "worker never received the task"
