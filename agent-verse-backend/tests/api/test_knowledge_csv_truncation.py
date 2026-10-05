"""Large CSV uploads are indexed in full, and a cut is reported (P1a-11).

Live P1a (KB-UPLOAD-HARD[csv-large]): CSVParser stopped at 10,000 rows with only
a marker line in the text; the upload answered 201 ``truncated: false``, so the
last 10,000 rows of a 20,000-row gate log (with the asked-about transaction)
were silently missing. The cap is now the workbook cap (100,000 rows) and a
truncated CSV is reported like a truncated workbook.
"""

from __future__ import annotations

import io
from unittest.mock import patch

from app.ingestion.document_text import extract_upload_text
from app.ingestion.parsers.csv_parser import CSVParser
from tests.api.test_knowledge_upload_replace import _H, _chunks, _client


def _csv(rows: int) -> bytes:
    lines = ["txn_id,gate,remarks"] + [f"GT-{i:07d},Gate {i % 4},\"ok, cleared\""
                                       for i in range(rows)]
    return ("\n".join(lines) + "\n").encode()


def test_twenty_thousand_rows_are_all_extracted() -> None:
    text = extract_upload_text(_csv(20_000), ext="csv", filename="gate.csv", report={})
    assert "txn_id: GT-0019999" in text
    assert "Truncated" not in text
    assert CSVParser.MAX_ROWS >= 100_000


def test_a_truncated_csv_is_reported_by_the_upload() -> None:
    app, client, cid = _client()
    with patch.object(CSVParser, "MAX_ROWS", 50):
        r = client.post("/knowledge/ingest/file", headers=_H, data={"collection_id": cid},
                        files={"file": ("gate.csv", io.BytesIO(_csv(80)), "text/csv")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["truncated"] is True
    assert "50" in " ".join(body["warnings"]) and "gate.csv" in " ".join(body["warnings"])
    assert any("GT-0000049" in c.content for c in _chunks(app))
    assert not any("GT-0000050" in c.content for c in _chunks(app))
