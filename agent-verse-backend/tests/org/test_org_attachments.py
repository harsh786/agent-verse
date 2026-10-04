"""a08-F177-01: mission attachments are durable, tenant-scoped and readable by agents.

The upload used to write bytes to ``ORG_ATTACHMENTS_DIR`` (or the API host's temp
dir) and hand back that absolute host path. Another replica or a Celery worker
could not read it, nothing ever deleted it, and the agent-callable
``extract_document`` refuses ``file_path`` anyway. Attachments now live in
Postgres (``org_attachments``, RLS + retention) and the agent reads one by
``attachment_id`` through the tenant-bound utility tool.
"""

from __future__ import annotations

import base64
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.org.router import router as org_router
from app.org.service import OrgService

TENANT_ID = "00000000-0000-0000-0000-000000000001"
ORG_ID = str(uuid.uuid4())


@pytest.fixture
def mock_service() -> MagicMock:
    svc = MagicMock(spec=OrgService)
    svc._tenant_id = TENANT_ID
    svc._session = MagicMock()
    org = MagicMock()
    org.id = uuid.UUID(ORG_ID)
    svc.get_organization = AsyncMock(return_value=org)
    return svc


@pytest.fixture
async def client(mock_service: MagicMock) -> Any:
    from app.org.router import get_org_service

    app = FastAPI()

    @app.middleware("http")
    async def fake_tenant(request, call_next):  # type: ignore[no-untyped-def]
        from app.tenancy.context import PlanTier, TenantContext

        request.state.tenant = TenantContext(
            tenant_id=TENANT_ID,
            plan=PlanTier.PROFESSIONAL,
            api_key_id="k",
            roles=("admin",),
        )
        return await call_next(request)

    app.include_router(org_router)

    async def _override():  # type: ignore[no-untyped-def]
        yield mock_service

    app.dependency_overrides[get_org_service] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def test_upload_persists_through_service_and_returns_no_host_path(
    client: AsyncClient, mock_service: MagicMock, tmp_path: Any
) -> None:
    att_id = uuid.uuid4().hex
    mock_service.add_attachment = AsyncMock(
        return_value={
            "attachment_id": att_id,
            "filename": "notes.txt",
            "content_type": "text/plain",
            "size": 11,
            "expires_at": "2026-11-04T00:00:00+00:00",
        }
    )
    with patch.dict("os.environ", {"ORG_ATTACHMENTS_DIR": str(tmp_path)}):
        r = await client.post(
            f"/v1/org/{ORG_ID}/attachments",
            files={"file": ("notes.txt", b"hello world", "text/plain")},
        )
    assert r.status_code == 201, r.text
    body = r.json()
    # Durable write through the RLS-scoped service session, nothing on the host FS.
    mock_service.add_attachment.assert_awaited_once()
    kwargs = mock_service.add_attachment.await_args.kwargs
    assert kwargs["content"] == b"hello world"
    assert kwargs["content_type"] == "text/plain"
    assert str(kwargs["org_id"]) == ORG_ID
    assert list(tmp_path.iterdir()) == []
    assert "path" not in body
    assert body["attachment_id"] == att_id
    assert body["ref"] == f"org-attachment:{att_id}"


async def test_upload_store_failure_is_an_error_not_a_fake_success(
    client: AsyncClient, mock_service: MagicMock
) -> None:
    mock_service.add_attachment = AsyncMock(side_effect=RuntimeError("db down"))
    with pytest.raises(RuntimeError):
        await client.post(
            f"/v1/org/{ORG_ID}/attachments",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )


# ── agent read path: extract_document(attachment_id=...) ──────────────────────


class _Ctx:
    tenant_id = TENANT_ID


async def test_extract_document_reads_attachment_for_calling_tenant() -> None:
    from app.mcp.servers import utility_server
    from app.org.attachments import AttachmentBlob

    seen: dict[str, Any] = {}

    class _Tool:
        async def execute(self, **kw: Any) -> dict[str, Any]:
            seen.update(kw)
            return {"raw_text": "ok"}

    blob = AttachmentBlob(
        attachment_id="a1", filename="scan.pdf", content_type="application/pdf", content=b"%PDF"
    )
    loader = AsyncMock(return_value=blob)
    utility_server.set_tools({"extract_document": _Tool()})
    try:
        with patch("app.org.attachments.load_attachment", loader):
            out = await utility_server.call_tool(
                "extract_document", {"attachment_id": "a1"}, tenant_ctx=_Ctx()
            )
    finally:
        utility_server.set_tools(None)
    assert out == {"raw_text": "ok"}
    loader.assert_awaited_once_with(TENANT_ID, "a1")
    assert base64.b64decode(seen["document_base64"]) == b"%PDF"
    assert seen["content_type"] == "application/pdf"
    assert seen["filename"] == "scan.pdf"
    assert "attachment_id" not in seen


async def test_extract_document_attachment_requires_tenant() -> None:
    from app.mcp.servers import utility_server

    out = await utility_server.call_tool("extract_document", {"attachment_id": "a1"})
    assert "error" in out


async def test_extract_document_unknown_attachment_is_an_error() -> None:
    from app.mcp.servers import utility_server

    with patch("app.org.attachments.load_attachment", AsyncMock(return_value=None)):
        out = await utility_server.call_tool(
            "extract_document", {"attachment_id": "nope"}, tenant_ctx=_Ctx()
        )
    assert "not found" in out["error"]
