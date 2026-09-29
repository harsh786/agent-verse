"""Excel parser — XLS/XLSX/ODS extraction with sheet-aware text output."""

from __future__ import annotations

import io
import logging
from typing import Any

_log = logging.getLogger(__name__)


class ExcelParser:
    """Parse Excel workbooks (XLS/XLSX/ODS) into readable text.

    Each sheet becomes: "Sheet: {name}\nRow: col1=val1, col2=val2\n..."
    Skips empty rows. Limits to MAX_ROWS per sheet and MAX_SHEETS per workbook.

    The limits used to be 5,000 rows / 20 sheets, and sheets past the 20th were
    dropped with no marker at all, so large workbooks were silently indexed in
    part. Every truncation is now marked in the text and reported by
    :meth:`parse_with_report` (connector ingestion records it in the result
    metadata).
    """

    MAX_ROWS = 100_000
    MAX_SHEETS = 200

    def parse(self, content: bytes, *, filename: str = "") -> str:
        return self.parse_with_report(content, filename=filename)[0]

    def parse_with_report(
        self, content: bytes, *, filename: str = ""
    ) -> tuple[str, dict[str, Any]]:
        """``(text, report)``; ``report`` is empty unless the workbook was truncated."""
        try:
            import openpyxl  # type: ignore[import-not-found]
        except ImportError:
            _log.warning("openpyxl not installed — cannot parse Excel. pip install openpyxl")
            return "", {}

        try:
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as exc:
            _log.warning("failed to open Excel file '%s': %s", filename, exc)
            return "", {}

        parts: list[str] = []
        row_truncated: list[str] = []
        sheet_names = list(wb.sheetnames)
        for sheet_name in sheet_names[: self.MAX_SHEETS]:
            ws = wb[sheet_name]
            sheet_parts = [f"Sheet: {sheet_name}"]
            headers: list[str] = []
            row_count = 0

            for row in ws.iter_rows(values_only=True):
                # First non-empty row → headers
                if not headers:
                    headers = [str(c) if c is not None else f"col{i}" for i, c in enumerate(row)]
                    continue
                if all(c is None for c in row):
                    continue  # skip blank rows
                if row_count >= self.MAX_ROWS:
                    sheet_parts.append(f"[truncated at {self.MAX_ROWS} rows]")
                    row_truncated.append(str(sheet_name))
                    break
                row_parts = [
                    f"{headers[i] if i < len(headers) else f'col{i}'}={v}"
                    for i, v in enumerate(row)
                    if v is not None
                ]
                if row_parts:
                    sheet_parts.append("Row: " + ", ".join(row_parts))
                row_count += 1

            parts.append("\n".join(sheet_parts))

        wb.close()
        sheets_parsed = min(len(sheet_names), self.MAX_SHEETS)
        if len(sheet_names) > sheets_parsed:
            parts.append(f"[truncated: {sheets_parsed} of {len(sheet_names)} sheets parsed]")
        report: dict[str, Any] = {}
        if row_truncated or len(sheet_names) > sheets_parsed:
            report = {
                "excel_truncated": True,
                "excel_sheets_total": len(sheet_names),
                "excel_sheets_parsed": sheets_parsed,
                "excel_row_truncated_sheets": row_truncated,
                "excel_max_rows_per_sheet": self.MAX_ROWS,
            }
            _log.warning("excel_truncated file=%s report=%s", filename, report)
        return "\n\n".join(parts), report
