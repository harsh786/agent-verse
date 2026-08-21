"""Excel parser — XLS/XLSX/ODS extraction with sheet-aware text output."""

from __future__ import annotations

import io
import logging

_log = logging.getLogger(__name__)


class ExcelParser:
    """Parse Excel workbooks (XLS/XLSX/ODS) into readable text.

    Each sheet becomes: "Sheet: {name}\nRow: col1=val1, col2=val2\n..."
    Skips empty rows. Limits to MAX_ROWS per sheet and MAX_SHEETS per workbook.
    """

    MAX_ROWS = 5_000
    MAX_SHEETS = 20

    def parse(self, content: bytes, *, filename: str = "") -> str:
        try:
            import openpyxl  # type: ignore[import-not-found]
        except ImportError:
            _log.warning("openpyxl not installed — cannot parse Excel. pip install openpyxl")
            return ""

        try:
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as exc:
            _log.warning("failed to open Excel file '%s': %s", filename, exc)
            return ""

        parts: list[str] = []
        for sheet_name in wb.sheetnames[: self.MAX_SHEETS]:
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
                row_parts = [
                    f"{headers[i] if i < len(headers) else f'col{i}'}={v}"
                    for i, v in enumerate(row)
                    if v is not None
                ]
                if row_parts:
                    sheet_parts.append("Row: " + ", ".join(row_parts))
                row_count += 1
                if row_count >= self.MAX_ROWS:
                    sheet_parts.append(f"[truncated at {self.MAX_ROWS} rows]")
                    break

            parts.append("\n".join(sheet_parts))

        wb.close()
        return "\n\n".join(parts)
