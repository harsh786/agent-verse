"""Parquet parser — column schema + row sampling for analytics tables."""
from __future__ import annotations

import io
import logging

_log = logging.getLogger(__name__)


class ParquetParser:
    """Parse Parquet files into schema description + sampled rows.

    Output format:
      Table: {filename}
      Schema: col1 (int64), col2 (string), ...
      Row 0: col1=1, col2=foo, ...
      ...
    """

    SAMPLE_ROWS = 100

    def parse(self, content: bytes, *, filename: str = "") -> str:
        try:
            import pyarrow.parquet as pq  # type: ignore[import-not-found]
            import pyarrow as pa  # type: ignore[import-not-found]
        except ImportError:
            _log.warning("pyarrow not installed — cannot parse Parquet. pip install pyarrow")
            return ""

        try:
            buf = pa.BufferReader(content)
            table = pq.read_table(buf)
        except Exception as exc:
            _log.warning("failed to read Parquet file '%s': %s", filename, exc)
            return ""

        schema_parts = [f"{field.name} ({field.type})" for field in table.schema]
        header = f"Table: {filename}\n" if filename else ""
        schema_line = "Schema: " + ", ".join(schema_parts)

        # Sample rows
        df = table.slice(0, self.SAMPLE_ROWS).to_pydict()
        col_names = list(df.keys())
        n_rows = len(next(iter(df.values()), []))

        row_lines: list[str] = []
        for i in range(n_rows):
            parts = [f"{col}={df[col][i]}" for col in col_names if df[col][i] is not None]
            row_lines.append(f"Row {i}: " + ", ".join(parts))

        return header + schema_line + "\n" + "\n".join(row_lines)
