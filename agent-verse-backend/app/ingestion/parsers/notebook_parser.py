"""Jupyter Notebook parser — cell-pair extraction (code + output)."""
from __future__ import annotations

import json
import logging

_log = logging.getLogger(__name__)


class NotebookParser:
    """Parse .ipynb Jupyter notebooks into cell-pair text chunks.

    Each code cell + its output becomes one text unit:
    "# Cell N (python)\n<code>\n## Output:\n<output>"
    Markdown cells are extracted as prose sections.
    """

    def parse(self, content: str, *, filename: str = "") -> str:
        try:
            nb = json.loads(content)
        except json.JSONDecodeError as exc:
            _log.warning("notebook_parse_error filename=%s: %s", filename, exc)
            return content[:5000]

        cells = nb.get("cells", [])
        kernel = nb.get("metadata", {}).get("kernelspec", {}).get("language", "python")
        parts: list[str] = []

        if filename:
            parts.append(f"Notebook: {filename}")

        for i, cell in enumerate(cells):
            cell_type = cell.get("cell_type", "")
            source_lines = cell.get("source", [])
            source = "".join(source_lines).strip()

            if not source:
                continue

            if cell_type == "markdown":
                parts.append(f"## Section\n{source}")

            elif cell_type == "code":
                outputs = cell.get("outputs", [])
                output_texts: list[str] = []
                for out in outputs:
                    if out.get("output_type") in ("stream", "execute_result", "display_data"):
                        text_data = out.get("text", out.get("data", {}).get("text/plain", []))
                        if isinstance(text_data, list):
                            text_data = "".join(text_data)
                        if text_data:
                            output_texts.append(text_data.strip()[:500])

                cell_text = f"# Cell {i + 1} ({kernel})\n{source}"
                if output_texts:
                    cell_text += "\n## Output:\n" + "\n".join(output_texts)
                parts.append(cell_text)

        return "\n\n".join(parts)


class ParquetParser:
    """Parse Parquet files — sample rows and schema as text."""

    MAX_ROWS = 100
    MAX_COLS = 50

    def parse(self, content: bytes, *, filename: str = "") -> str:
        try:
            import io

            import pyarrow.parquet as pq  # type: ignore[import-not-found]
            table = pq.read_table(io.BytesIO(content))
            schema = table.schema

            parts: list[str] = []
            if filename:
                parts.append(f"File: {filename}")

            # Schema description
            col_info = []
            for i, field in enumerate(schema):
                if i >= self.MAX_COLS:
                    col_info.append(f"...{len(schema) - self.MAX_COLS} more columns")
                    break
                col_info.append(f"{field.name} ({field.type})")
            parts.append("Schema: " + ", ".join(col_info))
            parts.append(f"Rows: {table.num_rows}, Columns: {table.num_columns}")

            # Sample rows
            df = table.slice(0, self.MAX_ROWS).to_pydict()
            col_names = list(df.keys())[:self.MAX_COLS]
            for row_idx in range(min(self.MAX_ROWS, table.num_rows)):
                row_parts = []
                for col in col_names:
                    val = df[col][row_idx] if row_idx < len(df[col]) else None
                    if val is not None:
                        row_parts.append(f"{col}: {str(val)[:100]}")
                if row_parts:
                    parts.append(", ".join(row_parts))

            return "\n".join(parts)

        except ImportError:
            _log.debug("pyarrow not installed")
            return f"[Parquet file: {filename} — install pyarrow to extract]"
        except Exception as exc:
            _log.warning("parquet_parse_error filename=%s: %s", filename, exc)
            return f"[Parquet parse failed: {exc}]"
