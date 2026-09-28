"""Knowledge file uploads are size-capped (413) instead of read whole into memory."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from tests.api.test_knowledge_rpa import _make_client


@pytest.fixture
def _small_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "knowledge_max_upload_bytes", 1024)


@pytest.mark.usefixtures("_small_cap")
@pytest.mark.parametrize(
    ("path", "filename"),
    [
        ("/knowledge/ingest/file", "big.txt"),
        ("/knowledge/ingest/pdf", "big.pdf"),
        ("/knowledge/ingest/docx", "big.docx"),
    ],
)
def test_oversized_upload_is_rejected(path: str, filename: str) -> None:
    client: TestClient = _make_client()
    resp = client.post(
        path,
        files={"file": (filename, b"x" * 4096, "application/octet-stream")},
        data={"collection_id": "col-1"},
    )
    assert resp.status_code == 413
    assert "limit" in resp.json()["detail"]


@pytest.mark.usefixtures("_small_cap")
def test_upload_under_cap_is_accepted() -> None:
    client: TestClient = _make_client()
    resp = client.post(
        "/knowledge/ingest/file",
        files={"file": ("small.txt", b"hello world " * 10, "text/plain")},
        data={"collection_id": "col-1"},
    )
    assert resp.status_code != 413
