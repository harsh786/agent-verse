"""Regression: Excel ingestion silently truncated at 5,000 rows / 20 sheets.

Sheets past the 20th were dropped with no marker at all, and nothing in the
ingestion result said the document was only partly indexed. The caps are raised
(100,000 rows per sheet, 200 sheets), every truncation is marked in the text,
and connector ingestion records it in the result metadata.
"""

from __future__ import annotations

import io

import pytest

from app.ingestion.parsers.excel_parser import ExcelParser


def _workbook(sheets: int, rows: int) -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for s in range(sheets):
        ws = wb.create_sheet(f"S{s}")
        ws.append(["id", "value"])
        for r in range(rows):
            ws.append([r, f"v{r}"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_caps_were_raised() -> None:
    assert ExcelParser.MAX_ROWS >= 100_000
    assert ExcelParser.MAX_SHEETS >= 200


def test_truncation_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ExcelParser, "MAX_ROWS", 3)
    monkeypatch.setattr(ExcelParser, "MAX_SHEETS", 2)
    text, report = ExcelParser().parse_with_report(_workbook(3, 5), filename="big.xlsx")
    assert report["excel_truncated"] is True
    assert report["excel_sheets_total"] == 3 and report["excel_sheets_parsed"] == 2
    assert report["excel_row_truncated_sheets"] == ["S0", "S1"]
    assert "[truncated: 2 of 3 sheets parsed]" in text
    assert "S2" not in text


def test_complete_workbook_reports_no_truncation() -> None:
    text, report = ExcelParser().parse_with_report(_workbook(2, 3), filename="ok.xlsx")
    assert report == {}
    assert "Sheet: S1" in text


async def test_connector_ingestion_records_truncation_in_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ingestion.content_classifier import ContentType
    from app.ingestion.parser_registry import ParserRegistry

    monkeypatch.setattr(ExcelParser, "MAX_SHEETS", 1)
    text, meta = await ParserRegistry().parse_bytes_async(
        _workbook(2, 2), ContentType.EXCEL, filename="two.xlsx"
    )
    assert "Sheet: S0" in text
    assert meta.get("excel_truncated") is True and meta.get("excel_sheets_total") == 2
