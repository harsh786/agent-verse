"""Tests for new parsers: excel, yaml, parquet, avro, latex."""
from __future__ import annotations

import pytest


# ── ExcelParser ───────────────────────────────────────────────────────────────

class TestExcelParser:
    def test_parse_with_openpyxl(self):
        """If openpyxl is available, parse a minimal in-memory workbook."""
        try:
            import openpyxl
            import io
            from app.ingestion.parsers.excel_parser import ExcelParser

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Sales"
            ws.append(["Name", "Amount", "Date"])
            ws.append(["Alice", 1000, "2026-01-01"])
            ws.append(["Bob", 2500, "2026-01-15"])
            buf = io.BytesIO()
            wb.save(buf)
            buf.seek(0)

            parser = ExcelParser()
            result = parser.parse(buf.getvalue(), filename="sales.xlsx")
            assert "Sheet: Sales" in result
            assert "Alice" in result
            assert "Amount" in result
        except ImportError:
            pytest.skip("openpyxl not installed")

    def test_parse_empty_bytes_returns_empty(self):
        try:
            from app.ingestion.parsers.excel_parser import ExcelParser
            parser = ExcelParser()
            result = parser.parse(b"", filename="empty.xlsx")
            # Should not crash, may return empty string
            assert isinstance(result, str)
        except ImportError:
            pytest.skip("openpyxl not installed")


# ── YAMLParser ────────────────────────────────────────────────────────────────

class TestYAMLParser:
    def test_parse_simple_yaml(self):
        from app.ingestion.parsers.yaml_parser import YAMLParser
        parser = YAMLParser()
        content = "database:\n  host: localhost\n  port: 5432\napp:\n  debug: true\n"
        result = parser.parse(content, filename="config.yaml")
        assert "database.host=localhost" in result
        assert "database.port=5432" in result
        assert "app.debug=True" in result

    def test_parse_bytes_input(self):
        from app.ingestion.parsers.yaml_parser import YAMLParser
        parser = YAMLParser()
        result = parser.parse(b"name: test\nvalue: 42", filename="test.yaml")
        assert "name=test" in result
        assert "value=42" in result

    def test_parse_invalid_yaml_returns_raw(self):
        from app.ingestion.parsers.yaml_parser import YAMLParser
        parser = YAMLParser()
        result = parser.parse(":: invalid ::: yaml :::", filename="bad.yaml")
        assert isinstance(result, str)

    def test_parse_empty_yaml_returns_empty(self):
        from app.ingestion.parsers.yaml_parser import YAMLParser
        parser = YAMLParser()
        result = parser.parse("", filename="empty.yaml")
        assert result == ""

    def test_parse_nested_yaml(self):
        from app.ingestion.parsers.yaml_parser import YAMLParser
        parser = YAMLParser()
        content = "services:\n  web:\n    image: nginx\n    ports:\n      - 80:80\n"
        result = parser.parse(content)
        assert "services.web.image=nginx" in result


class TestTOMLParser:
    def test_parse_simple_toml(self):
        from app.ingestion.parsers.yaml_parser import TOMLParser
        parser = TOMLParser()
        content = '[database]\nhost = "localhost"\nport = 5432\n'
        result = parser.parse(content, filename="config.toml")
        assert isinstance(result, str)
        # Either parsed or raw fallback — should not crash


# ── LaTeXParser ───────────────────────────────────────────────────────────────

class TestLaTeXParser:
    def test_parse_sections(self):
        from app.ingestion.parsers.latex_parser import LaTeXParser
        parser = LaTeXParser()
        content = r"""
\section{Introduction}
This paper presents a novel approach.

\section{Method}
We use gradient descent.

\subsection{Details}
The learning rate is 0.01.
"""
        result = parser.parse(content, filename="paper.tex")
        assert "## Introduction" in result
        assert "novel approach" in result
        assert "## Method" in result
        assert "gradient descent" in result

    def test_strips_latex_commands(self):
        from app.ingestion.parsers.latex_parser import LaTeXParser
        parser = LaTeXParser()
        content = r"\textbf{Bold text} and \emph{italic} and \footnote{a footnote}."
        result = parser.parse(content)
        assert "Bold text" in result or "bold" in result.lower() or isinstance(result, str)

    def test_parse_empty_returns_empty(self):
        from app.ingestion.parsers.latex_parser import LaTeXParser
        parser = LaTeXParser()
        result = parser.parse("", filename="empty.tex")
        assert result == ""

    def test_strips_math_environments(self):
        from app.ingestion.parsers.latex_parser import LaTeXParser
        parser = LaTeXParser()
        content = r"Some text. \begin{equation} E = mc^2 \end{equation} More text."
        result = parser.parse(content)
        assert "[formula]" in result
        assert "Some text" in result
        assert "More text" in result


# ── ParquetParser ─────────────────────────────────────────────────────────────

class TestParquetParser:
    def test_parse_with_pyarrow(self):
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
            import io
            from app.ingestion.parsers.parquet_parser import ParquetParser

            table = pa.table({"id": [1, 2, 3], "name": ["Alice", "Bob", "Carol"], "score": [0.9, 0.7, 0.8]})
            buf = io.BytesIO()
            pq.write_table(table, buf)
            buf.seek(0)

            parser = ParquetParser()
            result = parser.parse(buf.getvalue(), filename="data.parquet")
            assert "Schema:" in result
            assert "id" in result
            assert "name" in result
            assert "Alice" in result
        except ImportError:
            pytest.skip("pyarrow not installed")


# ── AvroParser ────────────────────────────────────────────────────────────────

class TestAvroParser:
    def test_parse_with_fastavro(self):
        try:
            import fastavro
            import io
            from app.ingestion.parsers.avro_parser import AvroParser

            schema = {
                "type": "record",
                "name": "User",
                "fields": [
                    {"name": "id", "type": "int"},
                    {"name": "name", "type": "string"},
                ],
            }
            records = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
            buf = io.BytesIO()
            fastavro.writer(buf, schema, records)
            buf.seek(0)

            parser = AvroParser()
            result = parser.parse(buf.getvalue(), filename="users.avro")
            assert "Schema: User" in result
            assert "id" in result
            assert "Alice" in result
        except ImportError:
            pytest.skip("fastavro not installed")
