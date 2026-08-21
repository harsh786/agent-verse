"""CSV and Excel parser — schema-aware extraction for structured data."""

from __future__ import annotations

import io
import logging

_log = logging.getLogger(__name__)


class CSVParser:
    """Parse CSV/TSV files into readable text chunks.

    Each row becomes: "Table: {filename}\nField1: val1, Field2: val2"
    Header row defines field names.
    """

    MAX_ROWS = 10_000

    def parse(self, content: str, *, filename: str = "", delimiter: str = "") -> str:
        try:
            import csv

            # Auto-detect delimiter if not specified
            if not delimiter:
                sample = content[:4096]
                try:
                    dialect = csv.Sniffer().sniff(sample)
                    delimiter = dialect.delimiter
                except Exception:
                    delimiter = ","

            reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
            lines: list[str] = []
            header_line = ""

            for i, row in enumerate(reader):
                if i >= self.MAX_ROWS:
                    lines.append(f"[Truncated: showing first {self.MAX_ROWS} rows]")
                    break
                if i == 0 and reader.fieldnames:
                    header_line = "Columns: " + ", ".join(str(f) for f in reader.fieldnames)
                    if filename:
                        lines.append(f"Table: {filename}")
                    lines.append(header_line)

                parts = [f"{k}: {v}" for k, v in row.items() if v and str(v).strip()]
                if parts:
                    lines.append(", ".join(parts))

            return "\n".join(lines)
        except Exception as exc:
            _log.warning("csv_parse_error: %s", exc)
            return content[:5000]


class ExcelParser:
    """Parse XLS/XLSX files into readable text, one sheet per section."""

    def parse(self, content: bytes, *, filename: str = "") -> str:
        try:
            import openpyxl  # type: ignore[import-not-found]

            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            parts: list[str] = []
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                parts.append(f"Sheet: {sheet_name}")
                rows = list(ws.iter_rows(values_only=True))
                if not rows:
                    continue
                headers = [str(h) if h is not None else "" for h in rows[0]]
                for row in rows[1:1001]:
                    items = [
                        f"{h}: {v}"
                        for h, v in zip(headers, row, strict=False)
                        if v is not None and str(v).strip()
                    ]
                    if items:
                        parts.append(", ".join(items))
            return "\n".join(parts)
        except ImportError:
            _log.debug("openpyxl not installed — falling back to CSV attempt")
            try:
                text = content.decode("utf-8", errors="replace")
                return CSVParser().parse(text, filename=filename)
            except Exception:
                return f"[Excel file: {filename} — install openpyxl to extract]"
        except Exception as exc:
            _log.warning("excel_parse_error: %s", exc)
            return f"[Excel parse failed: {exc}]"
