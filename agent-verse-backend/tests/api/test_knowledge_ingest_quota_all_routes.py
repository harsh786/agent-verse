"""KB-37: the ingestion document quota guards EVERY knowledge ingest route.

Only 4 of the ~15 direct ingest routes checked it, so a tenant past its plan's
document limit kept ingesting through the others. Each route now refuses with
429 before parsing, fetching or embedding anything.
"""

from __future__ import annotations

import io
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api.knowledge import router as knowledge_router
from app.ingestion.quota import IngestionQuotaExceededError
from app.rag.store import KnowledgeStore
from tests.api.test_knowledge_extra4 import H, _make_app

_JSON = {"collection_id": "c", "content": "x"}
_FILE = {"files": {"file": ("a.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")}}

# (method, path, request kwargs) — every POST that adds documents to a collection.
INGEST_ROUTES: list[tuple[str, dict[str, Any]]] = [
    ("/knowledge/ingest", {"json": _JSON}),
    ("/knowledge/ingest/file", {**_FILE, "data": {"collection_id": "c"}}),
    ("/knowledge/ingest/repo", {"json": {"collection_id": "c", "repo_url": "https://x/y"}}),
    ("/knowledge/ingest/openapi", {"json": {"collection_id": "c", "spec": {}}}),
    ("/knowledge/ingest/url", {"json": {"collection_id": "c", "url": "https://x"}}),
    ("/knowledge/ingest/pdf", {**_FILE, "data": {"collection_id": "c"}}),
    ("/knowledge/ingest/docx", {**_FILE, "data": {"collection_id": "c"}}),
    ("/knowledge/ingest/github", {"json": {"collection_id": "c", "repo": "a/b"}}),
    ("/knowledge/ingest/confluence", {"json": {"collection_id": "c"}}),
    ("/knowledge/ingest/jira", {"json": {"collection_id": "c"}}),
    ("/knowledge/ingest/slack", {"json": {"collection_id": "c"}}),
    ("/knowledge/ingest/rpa-url", {"json": {"collection_id": "c", "urls": ["https://x"]}}),
    ("/knowledge/ingest/email", {"json": {"collection_id": "c", "raw_email": "x"}}),
    ("/knowledge/ingest/notion", {"json": {"collection_id": "c", "api_key": "k"}}),
    ("/knowledge/ingest/gdrive-folder", {"json": {"collection_id": "c", "folder_id": "f"}}),
    ("/knowledge/collections/c/documents", {"json": {"content": "x"}}),
]


class _OverQuota:
    def __init__(self) -> None:
        self.calls = 0

    async def check_doc_quota(self, tenant_id: str) -> None:
        self.calls += 1
        raise IngestionQuotaExceededError("documents", 100, 100, "free")


class _Embedder:
    embed = AsyncMock(side_effect=AssertionError("embedding must not run past the quota"))


@pytest.mark.parametrize(("path", "kwargs"), INGEST_ROUTES, ids=[p for p, _ in INGEST_ROUTES])
def test_every_ingest_route_refuses_past_the_document_quota(
    path: str, kwargs: dict[str, Any]
) -> None:
    quota = _OverQuota()
    app = _make_app(knowledge_store=KnowledgeStore(), embedder=_Embedder())
    app.state.ingestion_quota = quota
    resp = TestClient(app, raise_server_exceptions=False).post(path, headers=H, **kwargs)
    assert resp.status_code == 429, f"{path}: {resp.status_code} {resp.text[:200]}"
    assert quota.calls == 1


def test_the_list_covers_every_ingest_route() -> None:
    """A new ingest route must be added here (and get the guard)."""
    listed = {p.removeprefix("/knowledge") for p, _ in INGEST_ROUTES}
    ingest_like = {
        r.path.removeprefix("/knowledge")
        for r in knowledge_router.routes
        if isinstance(r, APIRoute)
        and "POST" in r.methods
        and (r.path.startswith("/knowledge/ingest") or r.path.endswith("/documents"))
    }
    assert ingest_like - {p.replace("/c/", "/{collection_id}/") for p in listed} == set()
