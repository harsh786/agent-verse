"""Excel parser — XLS/XLSX/ODS extraction with sheet-aware text output."""

from __future__ import annotations

import io
import logging
import re
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
        except ImportError as exc:
            # A server-side gap, not an unreadable file: it used to return "" and
            # the upload was refused 422 "not a readable workbook".
            from app.ingestion.document_text import ParserUnavailableError

            raise ParserUnavailableError(
                "Excel parsing is unavailable on this server (openpyxl is not installed)"
            ) from exc

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
            merged = _merged_ranges(wb, ws)
            merges = _Merges(merged)
            sheet_parts = [f"Sheet: {sheet_name}"]
            headers: list[str] = []
            pending_titles: list[str] = []
            row_count = 0

            for r, raw in enumerate(ws.iter_rows(min_row=1, min_col=1, values_only=True), 1):
                row = merges.apply(list(raw), r)
                if all(c is None for c in row):
                    continue  # skip blank rows
                if not headers:
                    distinct = {str(c).strip() for c in row if c is not None and str(c).strip()}
                    if (
                        len(distinct) == 1
                        and _title_like(row, ws, merged, r)
                        and len(pending_titles) < 5
                    ):
                        # A title / caption row (one label, often merged across the
                        # sheet) above the real header row.
                        pending_titles.append(next(iter(distinct)))
                        continue
                    headers = _header_names(row)
                    sheet_parts.extend(f"Title: {t}" for t in pending_titles)
                    continue
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
            if not headers and pending_titles:
                # Only one-label rows: a single-column sheet. The first is its header.
                headers = [pending_titles[0]]
                sheet_parts.extend(f"Row: {headers[0]}={t}" for t in pending_titles[1:])

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


_MERGE_RE = re.compile(rb'<(?:\w+:)?mergeCell\s+ref="([A-Z]{1,3}\d+)(?::([A-Z]{1,3}\d+))?"')


def _merged_ranges(wb: Any, ws: Any) -> list[tuple[int, int, int, int]]:
    """Merged ranges ``(min_row, min_col, max_row, max_col)`` of a sheet.

    Read-only worksheets (used so large workbooks stream) do not expose
    ``merged_cells``, so the sheet XML is scanned for ``<mergeCell>`` in blocks.
    """
    ranges = getattr(ws, "merged_cells", None)
    if ranges is not None:
        return [(m.min_row, m.min_col, m.max_row, m.max_col) for m in ranges.ranges]
    archive = getattr(wb, "_archive", None)
    path = getattr(ws, "_worksheet_path", None)
    if archive is None or not path:
        return []
    from openpyxl.utils.cell import coordinate_to_tuple

    found: list[tuple[int, int, int, int]] = []
    try:
        with archive.open(path) as fh:
            tail = b""
            while block := fh.read(1024 * 1024):
                data = tail + block
                for m in _MERGE_RE.finditer(data):
                    r1, c1 = coordinate_to_tuple(m.group(1).decode())
                    r2, c2 = coordinate_to_tuple((m.group(2) or m.group(1)).decode())
                    found.append((r1, c1, r2, c2))
                tail = data[-200:]
    except Exception as exc:
        _log.debug("merged_cells_scan_failed sheet=%s: %s", path, exc)
        return []
    return sorted(set(found))


class _Merges:
    """Applies merged ranges row by row: a sweep over the ranges active at the
    current row (sorted by start), so a sheet with many merges stays linear."""

    def __init__(self, ranges: list[tuple[int, int, int, int]]) -> None:
        self._pending = sorted(ranges)
        self._next = 0
        self._active: list[tuple[int, int, int, Any]] = []  # (max_row, min_col, max_col, v)

    def apply(self, row: list[Any], r: int) -> list[Any]:
        """``row`` (1-based row ``r``) with every merged cell set to its range's value."""
        while self._next < len(self._pending) and self._pending[self._next][0] <= r:
            r1, c1, r2, c2 = self._pending[self._next]
            self._next += 1
            value = row[c1 - 1] if r1 == r and c1 - 1 < len(row) else None
            self._active.append((r2, c1, c2, value))
        self._active = [a for a in self._active if a[0] >= r]
        for _r2, c1, c2, value in self._active:
            if value is None:
                continue
            if len(row) < c2:
                row.extend([None] * (c2 - len(row)))
            for cc in range(c1, c2 + 1):
                if row[cc - 1] is None:
                    row[cc - 1] = value
        return row


def _title_like(
    row: list[Any], ws: Any, merged: list[tuple[int, int, int, int]], r: int
) -> bool:
    """A one-label row is a title / caption when it is merged across columns, or
    its label sits in column A of a sheet with more than one column. A header
    with a blank first cell (``[None, "B"]``) stays a header."""
    if any(r1 == r and c2 > c1 for r1, c1, _r2, c2 in merged):
        return True
    if not row or row[0] is None or not str(row[0]).strip():
        return False
    try:
        return int(ws.max_column or 1) > 1
    except Exception:
        return True


def _header_names(row: list[Any]) -> list[str]:
    """Header labels; empty cells become colN, repeated labels "label (2)"."""
    names: list[str] = []
    seen: dict[str, int] = {}
    for i, cell in enumerate(row):
        label = str(cell).strip() if cell is not None and str(cell).strip() else f"col{i}"
        seen[label] = seen.get(label, 0) + 1
        names.append(label if seen[label] == 1 else f"{label} ({seen[label]})")
    return names
