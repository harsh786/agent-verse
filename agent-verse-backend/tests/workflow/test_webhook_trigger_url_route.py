"""The settings UI shows the workflow's REAL webhook URL.

Old bug: WorkflowSettingsPage displayed ``/api/v1/webhooks/workflows/{id}``, a
route that does not exist; the real endpoint is ``POST /wf-hooks/{token}`` and
the token was only ever returned once, by ``publish``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from tests.workflow.test_router import make_app

from app.workflow.webhook_tokens import verify_webhook_token


def test_published_workflow_returns_verifiable_wf_hooks_url() -> None:
    svc = AsyncMock()
    svc.get.return_value = {"id": "wf-1", "status": "published"}
    client = make_app(svc)
    resp = client.get("/api/v1/workflows/wf-1/webhook")
    assert resp.status_code == 200
    body = resp.json()
    assert body["published"] is True
    assert body["webhook_path"].startswith("/wf-hooks/")
    token = body["webhook_path"].rsplit("/", 1)[1]
    assert verify_webhook_token(token) == ("test-tenant", "wf-1")
    assert body["callback_signing_secret"]


def test_unpublished_workflow_withholds_token() -> None:
    svc = AsyncMock()
    svc.get.return_value = {"id": "wf-1", "status": "draft"}
    body = make_app(svc).get("/api/v1/workflows/wf-1/webhook").json()
    assert body == {"workflow_id": "wf-1", "published": False}


def test_unknown_workflow_404() -> None:
    svc = AsyncMock()
    svc.get.return_value = None
    assert make_app(svc).get("/api/v1/workflows/nope/webhook").status_code == 404
