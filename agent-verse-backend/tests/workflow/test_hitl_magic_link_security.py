"""Regression: workflow HITL magic links fail closed.

Old bug: without Redis ``consume_magic_link`` returned ``{"token": t, "valid":
True}`` for ANY string, and the router then decided the approval named by the
(missing) payload with whatever ``?action=`` the caller put in the URL.
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.workflow.hitl_extension import HITLWorkflowGateway, WorkflowHITLRequest


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.data[key] = value

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def getdel(self, key: str) -> str | None:
        return self.data.pop(key, None)

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


def _req() -> WorkflowHITLRequest:
    return WorkflowHITLRequest(run_id="run-1", tenant_id="tenant-1", step_id="review")


async def test_consume_without_redis_fails_closed() -> None:
    gw = HITLWorkflowGateway()
    await gw.create_request(_req())
    assert await gw.consume_magic_link("anything-at-all") is None
    assert await gw.consume_magic_link("") is None


async def test_generate_without_redis_refuses() -> None:
    gw = HITLWorkflowGateway()
    req = await gw.create_request(_req())
    with pytest.raises(RuntimeError):
        await gw.generate_magic_link(req.request_id, "approved")


async def test_unknown_token_rejected_and_real_token_single_use() -> None:
    redis = _FakeRedis()
    gw = HITLWorkflowGateway(redis_client=redis)
    req = await gw.create_request(_req())
    link = await gw.generate_magic_link(req.request_id, "approved", base_url="https://x.test")
    assert link.startswith("https://x.test/api/v1/approvals/magic/")
    assert await gw.consume_magic_link("not-a-real-token") is None
    token = link.split("/magic/", 1)[1].split("?", 1)[0]
    stored = await gw.get_request(req.request_id)
    assert stored is not None and stored.magic_link_token == token
    payload = await gw.consume_magic_link(token)
    assert payload is not None
    assert payload["request_id"] == req.request_id
    assert payload["action"] == "approved"
    assert payload["tenant_id"] == "tenant-1"
    assert await gw.consume_magic_link(token) is None  # single use


async def test_expired_payload_rejected() -> None:
    redis = _FakeRedis()
    gw = HITLWorkflowGateway(redis_client=redis)
    redis.data["hitl:magic:tok"] = json.dumps(
        {"jti": "tok", "request_id": "r", "action": "approved", "exp": int(time.time()) - 1}
    )
    assert await gw.consume_magic_link("tok") is None


def _client(gw: Any) -> Any:
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from app.workflow.router_hitl import router

    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Request, call_next: Any) -> Any:
        request.app.state.hitl_workflow_gateway = gw
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


def test_router_uses_token_action_and_tenant_not_query() -> None:
    gw = AsyncMock()
    gw.consume_magic_link.return_value = {
        "request_id": "req-1", "action": "approved", "tenant_id": "tenant-1",
    }
    decided = WorkflowHITLRequest(run_id="r", tenant_id="tenant-1", step_id="s")
    decided.request_id = "req-1"
    decided.status = "approved"
    gw.decide.return_value = decided
    client = _client(gw)

    # Tampering with ?action= must not flip the decision.
    bad = client.get("/api/v1/approvals/magic/tok?action=rejected")
    assert bad.status_code == 400
    gw.decide.assert_not_awaited()

    ok = client.get("/api/v1/approvals/magic/tok?action=approved")
    assert ok.status_code == 200
    kw = gw.decide.await_args.kwargs
    assert kw["action"] == "approved" and kw["tenant_id"] == "tenant-1"


def test_router_rejects_token_without_bound_request() -> None:
    gw = AsyncMock()
    gw.consume_magic_link.return_value = {"token": "x", "valid": True}  # old fake shape
    client = _client(gw)
    assert client.get("/api/v1/approvals/magic/x?action=approved").status_code == 410
    gw.decide.assert_not_awaited()


async def test_notification_carries_working_single_use_links_when_redis_wired() -> None:
    redis = _FakeRedis()
    notify = AsyncMock()
    gw = HITLWorkflowGateway(redis_client=redis, notification_service=notify)
    await gw.create_request(_req())
    body = notify.send.await_args.kwargs["body"]
    approve = body.split("Approve: ", 1)[1].split("\n", 1)[0]
    token = approve.split("/magic/", 1)[1].split("?", 1)[0]
    payload = await gw.consume_magic_link(token)
    assert payload is not None and payload["action"] == "approved"


async def test_notification_without_redis_has_no_links() -> None:
    notify = AsyncMock()
    gw = HITLWorkflowGateway(notification_service=notify)
    await gw.create_request(_req())
    assert "/magic/" not in notify.send.await_args.kwargs["body"]
