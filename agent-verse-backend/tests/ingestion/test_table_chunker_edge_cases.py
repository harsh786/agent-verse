"""Deepened coverage for app/ingestion/chunkers/table.py (TableChunker).

TableChunker is line-based (it never parses CSV/JSON structurally — it just
treats the first non-blank line as a header and groups subsequent lines into
row batches). This file adds:
  * malformed table structure / irregular column counts
  * a genuinely empty table
  * JSON content routed through TableChunker (via the "record"/"table"
    strategies — see app/ingestion/chunkers/__init__.py), including nested
    JSON structures
  * CSV edge cases: quoted fields containing commas, and missing values
"""
from __future__ import annotations

from app.ingestion.chunkers.table import TableChunker


class TestMalformedTableStructure:
    def test_irregular_column_counts_across_rows_does_not_crash(self) -> None:
        chunker = TableChunker(rows_per_chunk=10)
        content = "a,b,c\n1,2,3\n4,5\n6,7,8,9\n,,\n10"
        chunks = chunker.chunk(content)
        assert len(chunks) == 1
        # Every original data row is preserved verbatim (chunker does not
        # validate/pad column counts — it is purely line-based).
        body = chunks[0].content
        assert "4,5" in body
        assert "6,7,8,9" in body
        assert ",," not in body.splitlines()[0]  # header line unaffected

    def test_single_column_rows(self) -> None:
        chunker = TableChunker(rows_per_chunk=5)
        content = "value\n1\n2\n3"
        chunks = chunker.chunk(content)
        assert len(chunks) == 1
        assert chunks[0].content.splitlines() == ["value", "1", "2", "3"]

    def test_ragged_rows_with_extra_whitespace_lines_are_skipped(self) -> None:
        """Blank lines inside the table are dropped (only non-blank lines count
        as header/data), so they cannot silently shift row numbering."""
        chunker = TableChunker(rows_per_chunk=10)
        content = "a,b\n1,2\n\n   \n3,4\n"
        chunks = chunker.chunk(content)
        assert len(chunks) == 1
        assert chunks[0].content.splitlines() == ["a,b", "1,2", "3,4"]
        assert chunks[0].metadata == {"row_start": 1, "row_end": 2}

    def test_header_only_no_data_rows(self) -> None:
        chunker = TableChunker()
        content = "col1,col2,col3"
        chunks = chunker.chunk(content)
        assert len(chunks) == 1
        assert chunks[0].content == "col1,col2,col3"
        assert chunks[0].metadata == {"row_start": 0}

    def test_non_csv_freeform_text_still_chunked_without_crashing(self) -> None:
        """TableChunker is deliberately naive: arbitrary non-tabular text does
        not raise, it's just treated as a 1-column "table"."""
        chunker = TableChunker(rows_per_chunk=2)
        content = "Report Title\nThis is a paragraph.\nAnother line of prose.\nFinal line."
        chunks = chunker.chunk(content)
        assert len(chunks) == 2
        assert all(c.content for c in chunks)


class TestEmptyTable:
    def test_completely_empty_string(self) -> None:
        chunker = TableChunker()
        chunks = chunker.chunk("")
        assert len(chunks) == 1
        assert chunks[0].content == ""
        assert chunks[0].chunk_index == 0

    def test_whitespace_only_content(self) -> None:
        chunker = TableChunker()
        chunks = chunker.chunk("   \n\n\t  \n")
        assert len(chunks) == 1
        assert chunks[0].content == ""

    def test_header_row_with_only_blank_data_rows(self) -> None:
        chunker = TableChunker()
        chunks = chunker.chunk("name,age\n\n   \n")
        assert len(chunks) == 1
        assert chunks[0].content == "name,age"
        assert chunks[0].metadata == {"row_start": 0}


class TestJSONContentViaTableChunker:
    """JSON is routed to TableChunker through the 'record' strategy (see
    app/ingestion/chunkers/__init__.py's STRATEGY_MAP). It has no dedicated
    parsing — pretty-printed JSON is chunked purely by line count, which
    these tests pin down explicitly."""

    def test_nested_json_object_is_chunked_by_lines_without_crashing(self) -> None:
        import json

        chunker = TableChunker(rows_per_chunk=3)
        data = {
            "user": {"id": 1, "name": "Alice", "roles": ["admin", "editor"]},
            "meta": {"created": "2026-01-01", "nested": {"deep": {"value": 42}}},
        }
        content = json.dumps(data, indent=2)
        chunks = chunker.chunk(content)
        assert len(chunks) >= 1
        # No content is lost: every chunk's body, concatenated, reproduces the
        # non-header lines (TableChunker repeats the "header" — here just the
        # first line, "{" — into every chunk).
        assert all(c.content for c in chunks)
        reconstructed_data_lines = []
        for c in chunks:
            body_lines = c.content.splitlines()[1:]  # drop repeated header line
            reconstructed_data_lines.extend(body_lines)
        original_data_lines = content.splitlines()[1:]
        assert reconstructed_data_lines == original_data_lines

    def test_json_array_of_records_chunks_by_rows_per_chunk(self) -> None:
        import json

        records = [{"id": i, "value": f"item-{i}"} for i in range(5)]
        content = "\n".join(json.dumps(r) for r in records)  # JSON-lines style
        chunker = TableChunker(rows_per_chunk=2)
        chunks = chunker.chunk(content)
        # header = first record line; 4 remaining data rows / 2 per chunk = 2 chunks
        assert len(chunks) == 2
        assert chunks[0].metadata == {"row_start": 1, "row_end": 2}
        assert chunks[1].metadata == {"row_start": 3, "row_end": 4}

    def test_empty_json_object_treated_as_empty_table(self) -> None:
        chunker = TableChunker()
        chunks = chunker.chunk("{}")
        assert len(chunks) == 1
        assert chunks[0].content == "{}"
        assert chunks[0].metadata == {"row_start": 0}


class TestCSVEdgeCases:
    def test_quoted_field_containing_commas_is_not_split(self) -> None:
        """TableChunker groups by LINE, never by comma — a quoted field with
        embedded commas cannot desynchronise row grouping, since no comma
        parsing happens at all."""
        chunker = TableChunker(rows_per_chunk=10)
        content = 'name,note\n"Smith, John",ok\n"Doe, Jane, PhD",also ok'
        chunks = chunker.chunk(content)
        assert len(chunks) == 1
        lines = chunks[0].content.splitlines()
        assert lines[0] == "name,note"
        assert lines[1] == '"Smith, John",ok'
        assert lines[2] == '"Doe, Jane, PhD",also ok'
        assert chunks[0].metadata == {"row_start": 1, "row_end": 2}

    def test_missing_trailing_values_preserved_verbatim(self) -> None:
        chunker = TableChunker(rows_per_chunk=10)
        content = "a,b,c\n1,2,3\n4,,\n,,6"
        chunks = chunker.chunk(content)
        assert len(chunks) == 1
        lines = chunks[0].content.splitlines()
        assert "4,,\n,,6".splitlines() == lines[2:]

    def test_csv_row_spanning_batches_gets_correct_row_metadata(self) -> None:
        chunker = TableChunker(rows_per_chunk=3)
        content = "name,age\n" + "\n".join(f"p{i},{20 + i}" for i in range(7))
        chunks = chunker.chunk(content)
        assert len(chunks) == 3  # 7 rows / 3 per chunk -> ceil(7/3) = 3
        assert chunks[0].metadata == {"row_start": 1, "row_end": 3}
        assert chunks[1].metadata == {"row_start": 4, "row_end": 6}
        assert chunks[2].metadata == {"row_start": 7, "row_end": 7}
        # Every chunk repeats the header for standalone readability.
        assert all(c.content.splitlines()[0] == "name,age" for c in chunks)

    def test_single_trailing_comma_row_not_crash(self) -> None:
        """A row that is just a trailing comma (empty last field) parses as a
        normal line — no IndexError or crash."""
        chunker = TableChunker(rows_per_chunk=10)
        content = "a,b\n1,\n,2\n,"
        chunks = chunker.chunk(content)
        assert len(chunks) == 1
        assert chunks[0].content.splitlines()[-1] == ","
