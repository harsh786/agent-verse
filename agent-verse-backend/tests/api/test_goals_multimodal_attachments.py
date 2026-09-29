"""D-23: goal attachments must be run through MultimodalPipeline so their
extracted content lands in execution_context["multimodal_context"] -- the
key app.agent.nodes.planner_mixin._node_plan reads to inject planner
context. Before this wiring, `attachments` was pure inert metadata: nothing
downstream ever read the bytes.
"""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.goals import router as goals_router
from app.multimodal.pipeline import MultimodalPipeline
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-mm-goals", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "ak_test_mm_goals"


def _make_app(fake_service: Any, *, with_pipeline: bool = True) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(goals_router)
    app.state.goal_service = fake_service
    if with_pipeline:
        app.state.multimodal_pipeline = MultimodalPipeline()
    return app


def _text_pdf(text: str) -> bytes:
    """A minimal one-page PDF whose content stream draws ``text`` in Helvetica."""
    content = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    return (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R"
        b"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj\n"
        b"4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"5 0 obj<</Length " + str(len(content)).encode() + b">>stream\n"
        + content
        + b"\nendstream endobj\n"
        b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n0\n%%EOF"
    )


def test_pdf_attachment_content_reaches_execution_context() -> None:
    svc = AsyncMock()
    svc.submit_goal.return_value = {"id": "gid-1", "status": "planning", "goal": "summarize"}
    client = TestClient(_make_app(svc), raise_server_exceptions=False)

    pdf_b64 = base64.b64encode(_text_pdf("Quarterly revenue grew twelve percent")).decode()

    resp = client.post(
        "/goals",
        json={
            "goal": "summarize the attached report",
            "attachments": [{"type": "pdf_base64", "data": pdf_b64}],
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202

    _, kwargs = svc.submit_goal.call_args
    exec_ctx = kwargs["execution_context"]
    assert exec_ctx["attachments"] == [{"type": "pdf_base64", "data": pdf_b64}]
    assert "Quarterly revenue grew twelve percent" in exec_ctx["multimodal_context"]


def test_text_free_pdf_attachment_injects_no_placeholder_into_planner_context() -> None:
    """A PDF with no extractable text used to inject the placeholder
    "[PDF: no extractable text found]" into the planner context as if it were
    the document's content. Its failed extraction must contribute nothing,
    and must not block goal submission."""
    svc = AsyncMock()
    svc.submit_goal.return_value = {"id": "gid-1b", "status": "planning", "goal": "summarize"}
    client = TestClient(_make_app(svc), raise_server_exceptions=False)

    textless_pdf = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
        b"trailer<</Size 4/Root 1 0 R>>"
    )
    resp = client.post(
        "/goals",
        json={
            "goal": "summarize the attached report",
            "attachments": [
                {"type": "pdf_base64", "data": base64.b64encode(textless_pdf).decode()}
            ],
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202

    _, kwargs = svc.submit_goal.call_args
    exec_ctx = kwargs["execution_context"]
    assert "[PDF" not in exec_ctx.get("multimodal_context", "")


def test_code_attachment_content_reaches_execution_context() -> None:
    svc = AsyncMock()
    svc.submit_goal.return_value = {"id": "gid-2", "status": "planning", "goal": "review"}
    client = TestClient(_make_app(svc), raise_server_exceptions=False)

    resp = client.post(
        "/goals",
        json={
            "goal": "review the attached function",
            "attachments": [
                {"type": "code", "data": "def add(a, b):\n    return a + b\n", "language": "python"}
            ],
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202

    _, kwargs = svc.submit_goal.call_args
    exec_ctx = kwargs["execution_context"]
    assert "def add" in exec_ctx["multimodal_context"]


def test_image_url_attachment_is_not_fetched_server_side() -> None:
    """URL-based attachments are intentionally not processed (SSRF avoidance)."""
    svc = AsyncMock()
    svc.submit_goal.return_value = {"id": "gid-3", "status": "planning", "goal": "look"}
    client = TestClient(_make_app(svc), raise_server_exceptions=False)

    resp = client.post(
        "/goals",
        json={
            "goal": "look at this chart",
            "attachments": [{"type": "image_url", "url": "https://example.com/chart.png"}],
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    _, kwargs = svc.submit_goal.call_args
    exec_ctx = kwargs["execution_context"]
    assert "multimodal_context" not in exec_ctx
    assert exec_ctx["attachments"] == [{"type": "image_url", "url": "https://example.com/chart.png"}]


def test_missing_pipeline_on_app_state_does_not_break_goal_submission() -> None:
    """Older/minimal app builds without app.state.multimodal_pipeline must
    still accept goals with attachments -- degrade gracefully."""
    svc = AsyncMock()
    svc.submit_goal.return_value = {"id": "gid-4", "status": "planning", "goal": "x"}
    client = TestClient(_make_app(svc, with_pipeline=False), raise_server_exceptions=False)

    resp = client.post(
        "/goals",
        json={
            "goal": "x",
            "attachments": [{"type": "text", "data": "hello"}],
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202


def test_no_attachments_means_no_multimodal_context_key() -> None:
    svc = AsyncMock()
    svc.submit_goal.return_value = {"id": "gid-5", "status": "planning", "goal": "plain"}
    client = TestClient(_make_app(svc), raise_server_exceptions=False)

    resp = client.post(
        "/goals",
        json={"goal": "plain goal, no attachments"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 202
    _, kwargs = svc.submit_goal.call_args
    assert "multimodal_context" not in kwargs["execution_context"]
