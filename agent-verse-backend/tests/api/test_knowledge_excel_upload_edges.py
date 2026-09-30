"""KB-09: Excel upload edge cases are reported honestly.

Without openpyxl an upload was refused 422 "not a readable workbook" (a server
problem reported as the user's file), and /ingest/file returned 201 with no
flag when a workbook was truncated.
"""

from __future__ import annotations

import io
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.api.test_knowledge_extra4 import H, _create_collection, _make_app
from tests.api.test_knowledge_upload_embedding_limits import _CountingEmbedder


def _xlsx(rows: int) -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["customer", "region"])
    for i in range(rows):
        ws.append([f"Customer number {i}", "Northern Europe"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _upload(client: TestClient, cid: str, data: bytes) -> object:
    return client.post(
        "/knowledge/ingest/file",
        files={"file": ("sheet.xlsx", io.BytesIO(data), "application/octet-stream")},
        data={"collection_id": cid},
        headers=H,
    )


def test_missing_openpyxl_is_503_not_422() -> None:
    client = TestClient(_make_app(embedder=_CountingEmbedder()), raise_server_exceptions=False)
    cid = _create_collection(client)
    data = _xlsx(3)
    with patch.dict(sys.modules, {"openpyxl": None}):
        resp = _upload(client, cid, data)
    assert resp.status_code == 503, resp.text  # type: ignore[attr-defined]


def test_a_truncated_workbook_is_flagged_in_the_response() -> None:
    client = TestClient(_make_app(embedder=_CountingEmbedder()), raise_server_exceptions=False)
    cid = _create_collection(client)
    with patch("app.ingestion.parsers.excel_parser.ExcelParser.MAX_ROWS", 5):
        resp = _upload(client, cid, _xlsx(20))
    assert resp.status_code == 201, resp.text  # type: ignore[attr-defined]
    body = resp.json()  # type: ignore[attr-defined]
    assert body["truncated"] is True
    assert body["truncated_sheets"]


def test_a_complete_workbook_is_not_flagged() -> None:
    client = TestClient(_make_app(embedder=_CountingEmbedder()), raise_server_exceptions=False)
    cid = _create_collection(client)
    resp = _upload(client, cid, _xlsx(3))
    assert resp.status_code == 201, resp.text  # type: ignore[attr-defined]
    assert resp.json()["truncated"] is False  # type: ignore[attr-defined]
