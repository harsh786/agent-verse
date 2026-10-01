"""e2e_full (APPROVALS-STREAM-404): the workflow approval inbox SSE stream.

``GET /api/v1/approvals/stream`` was declared after ``GET /approvals/{id}``,
so it matched the detail route as ``request_id="stream"`` and every connection
got 404. Against the real app + Postgres + Redis this proves the stream:

* authenticates with a short-lived ``?token=`` stream token (EventSource sends
  no headers) and refuses a connection with no credentials;
* announces a real pending approval assigned to the caller — created by an
  approval-gated workflow run suspended at its gate.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_API = "/api/v1"


@pytest.fixture
def _inprocess_hitl(app: Any) -> Iterator[None]:
    """Run the HITL workflow inline in the API process (where the gateway and
    checkpointer live) — same harness as ``test_workflow_hitl_e2e``."""
    from langgraph.checkpoint.memory import MemorySaver

    runner = app.state.workflow_runner
    compiler = runner._compiler
    prev_celery, prev_ckpt = runner._celery, compiler._checkpointer
    prev_cache = dict(compiler._cache)
    runner._celery = None
    compiler._checkpointer = MemorySaver()
    compiler._cache.clear()
    try:
        yield
    finally:
        runner._celery = prev_celery
        compiler._checkpointer = prev_ckpt
        compiler._cache.clear()
        compiler._cache.update(prev_cache)


@pytest.fixture
def _fast_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.workflow import router_hitl

    monkeypatch.setattr(router_hitl, "_STREAM_POLL_SECONDS", 0.05)
    monkeypatch.setattr(router_hitl, "_STREAM_MAX_POLLS", 3)


async def _caller_key_id(tenant_client: Any) -> str:
    resp = await tenant_client.get("/tenants/me/keys")
    assert resp.status_code == 200, resp.text
    keys = resp.json()
    keys = keys.get("keys", keys) if isinstance(keys, dict) else keys
    assert len(keys) == 1, keys
    return str(keys[0]["key_id"])


async def test_stream_requires_credentials(app: Any, _fast_stream: None) -> None:
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e-full") as anon:
        resp = await anon.get(f"{_API}/approvals/stream")
    assert resp.status_code == 401, resp.text


async def test_stream_token_streams_pending_approval(
    app: Any, tenant_client: Any, _inprocess_hitl: None, _fast_stream: None
) -> None:
    from httpx import ASGITransport, AsyncClient

    key_id = await _caller_key_id(tenant_client)
    created = await tenant_client.post(
        f"{_API}/workflows",
        json={
            "name": f"stream-hitl-{uuid.uuid4().hex[:8]}",
            "definition": {
                "name": "Stream HITL WF",
                "steps": [
                    {
                        "id": "gate",
                        "type": "hitl",
                        "assignee": {"strategy": "specific", "specific_user": key_id},
                        "actions": [{"id": "approve"}, {"id": "reject"}],
                    }
                ],
            },
        },
    )
    assert created.status_code == 201, created.text
    trig = await tenant_client.post(
        f"{_API}/workflows/{created.json()['id']}/trigger",
        json={"inputs": {}, "dry_run": False},
    )
    assert trig.status_code == 202, trig.text
    run_id = trig.json()["run_id"]
    gateway = app.state.hitl_workflow_gateway
    pending = [r for r in gateway._store.values() if r.run_id == run_id]
    assert pending and pending[0].status == "pending", pending
    request_id = pending[0].request_id

    tok = await tenant_client.get("/tenants/stream-token")
    assert tok.status_code == 200, tok.text
    token = tok.json()["token"]

    events: list[Any] = []
    async with AsyncClient(  # no X-API-Key: EventSource-style auth only
        transport=ASGITransport(app=app), base_url="http://e2e-full"
    ) as es:
        async with es.stream("GET", f"{_API}/approvals/stream", params={"token": token}) as resp:
            assert resp.status_code == 200, await resp.aread()
            assert resp.headers["content-type"].startswith("text/event-stream")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if payload == "[DONE]":
                    break
                events.append(json.loads(payload))

    mine = [e for e in events if e.get("request_id") == request_id]
    assert mine and mine[0]["event"] == "new_request" and mine[0]["priority"] == "medium", events
    # Announced once, not once per poll.
    assert sum(1 for e in events if e.get("request_id") == request_id) == 1
