"""Coverage tests for low-coverage ingestion parsers not covered elsewhere.

Targets:
  - app/ingestion/parsers/csv_parser.py (CSVParser + ExcelParser)
  - app/ingestion/parsers/notebook_parser.py (NotebookParser)
  - app/ingestion/parsers/markdown_parser.py (MarkdownParser + YAMLParser)
  - app/ingestion/parsers/html_parser.py (HTMLParser)
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


from app.ingestion.parsers.csv_parser import CSVParser, ExcelParser
from app.ingestion.parsers.html_parser import HTMLParser
from app.ingestion.parsers.markdown_parser import MarkdownParser, YAMLParser
from app.ingestion.parsers.notebook_parser import NotebookParser, ParquetParser


# ═══════════════════════════════════════════════════════════════════════════
# CSVParser
# ═══════════════════════════════════════════════════════════════════════════


class TestCSVParser:
    def test_basic_comma_csv(self) -> None:
        content = "name,age\nAlice,30\nBob,25\n"
        result = CSVParser().parse(content, filename="people.csv")
        assert "Table: people.csv" in result
        assert "Columns: name, age" in result
        assert "name: Alice, age: 30" in result
        assert "name: Bob, age: 25" in result

    def test_semicolon_delimiter_autodetected(self) -> None:
        content = "name;age\nAlice;30\nBob;25\n"
        result = CSVParser().parse(content)
        assert "name: Alice" in result

    def test_explicit_delimiter_overrides_sniffing(self) -> None:
        content = "name|age\nAlice|30\n"
        result = CSVParser().parse(content, delimiter="|")
        assert "name: Alice, age: 30" in result

    def test_empty_content_returns_empty_string(self) -> None:
        result = CSVParser().parse("")
        assert result == ""

    def test_empty_values_are_skipped_in_row(self) -> None:
        content = "name,age,note\nAlice,30,\n"
        result = CSVParser().parse(content)
        assert "note" not in result.split("\n")[-1]
        assert "name: Alice, age: 30" in result

    def test_truncates_after_max_rows(self) -> None:
        header = "id,val\n"
        rows = "\n".join(f"{i},{i}" for i in range(CSVParser.MAX_ROWS + 5))
        content = header + rows
        result = CSVParser().parse(content)
        assert f"[Truncated: showing first {CSVParser.MAX_ROWS} rows]" in result

    def test_no_filename_omits_table_header(self) -> None:
        content = "a,b\n1,2\n"
        result = CSVParser().parse(content)
        assert "Table:" not in result

    def test_malformed_input_falls_back_to_raw_slice(self) -> None:
        # Force an internal exception via a broken sniffer/delimiter combo
        with patch("csv.DictReader", side_effect=RuntimeError("boom")):
            result = CSVParser().parse("a,b\n1,2\n")
        assert result == "a,b\n1,2\n"

    def test_sniffer_failure_falls_back_to_comma(self) -> None:
        # Content too short/ambiguous for Sniffer to detect -> falls back to comma
        content = "x\n1\n2\n"
        result = CSVParser().parse(content)
        assert "Columns: x" in result


class TestExcelParser:
    def test_import_error_falls_back_to_csv_decode(self) -> None:
        content = b"name,age\nAlice,30\n"
        with patch.dict("sys.modules", {"openpyxl": None}):
            result = ExcelParser().parse(content, filename="data.xlsx")
        assert "name: Alice" in result

    def test_parses_workbook_with_openpyxl(self) -> None:
        mock_ws = MagicMock()
        mock_ws.iter_rows.return_value = [
            ("name", "age"),
            ("Alice", 30),
            ("Bob", None),
        ]
        mock_wb = MagicMock()
        mock_wb.sheetnames = ["Sheet1"]
        mock_wb.__getitem__.return_value = mock_ws

        fake_openpyxl = MagicMock()
        fake_openpyxl.load_workbook.return_value = mock_wb

        with patch.dict("sys.modules", {"openpyxl": fake_openpyxl}):
            result = ExcelParser().parse(b"binary-xlsx-content", filename="wb.xlsx")

        assert "Sheet: Sheet1" in result
        assert "name: Alice, age: 30" in result

    def test_empty_sheet_is_skipped(self) -> None:
        mock_ws = MagicMock()
        mock_ws.iter_rows.return_value = []
        mock_wb = MagicMock()
        mock_wb.sheetnames = ["Empty"]
        mock_wb.__getitem__.return_value = mock_ws
        fake_openpyxl = MagicMock()
        fake_openpyxl.load_workbook.return_value = mock_wb

        with patch.dict("sys.modules", {"openpyxl": fake_openpyxl}):
            result = ExcelParser().parse(b"x", filename="empty.xlsx")
        assert "Sheet: Empty" in result

    def test_generic_exception_returns_failure_message(self) -> None:
        fake_openpyxl = MagicMock()
        fake_openpyxl.load_workbook.side_effect = RuntimeError("corrupt file")
        with patch.dict("sys.modules", {"openpyxl": fake_openpyxl}):
            result = ExcelParser().parse(b"bad", filename="bad.xlsx")
        assert "Excel parse failed" in result

    def test_excel_import_error_and_bad_decode_returns_placeholder(self) -> None:
        with patch.dict("sys.modules", {"openpyxl": None}):
            with patch(
                "app.ingestion.parsers.csv_parser.CSVParser.parse",
                side_effect=RuntimeError("also broken"),
            ):
                result = ExcelParser().parse(b"\xff\xfe", filename="broken.xlsx")
        assert "install openpyxl" in result


# ═══════════════════════════════════════════════════════════════════════════
# NotebookParser
# ═══════════════════════════════════════════════════════════════════════════


class TestNotebookParser:
    def test_malformed_json_falls_back_to_raw_slice(self) -> None:
        result = NotebookParser().parse("{not valid json", filename="broken.ipynb")
        assert result == "{not valid json"

    def test_empty_notebook_no_cells(self) -> None:
        nb = {"cells": [], "metadata": {}}
        result = NotebookParser().parse(json.dumps(nb), filename="empty.ipynb")
        assert result == "Notebook: empty.ipynb"

    def test_code_cell_with_stream_output(self) -> None:
        nb = {
            "metadata": {"kernelspec": {"language": "python"}},
            "cells": [
                {
                    "cell_type": "code",
                    "source": ["print('hi')\n"],
                    "outputs": [{"output_type": "stream", "text": ["hi\n"]}],
                }
            ],
        }
        result = NotebookParser().parse(json.dumps(nb), filename="nb.ipynb")
        assert "# Cell 1 (python)" in result
        assert "print('hi')" in result
        assert "## Output:" in result
        assert "hi" in result

    def test_markdown_cell_becomes_section(self) -> None:
        nb = {
            "cells": [
                {"cell_type": "markdown", "source": ["# Title\n", "some text"]},
            ]
        }
        result = NotebookParser().parse(json.dumps(nb))
        assert "## Section" in result
        assert "# Title" in result

    def test_empty_source_cell_is_skipped(self) -> None:
        nb = {"cells": [{"cell_type": "code", "source": [""], "outputs": []}]}
        result = NotebookParser().parse(json.dumps(nb))
        assert result == ""

    def test_execute_result_output_with_data_text_plain(self) -> None:
        nb = {
            "cells": [
                {
                    "cell_type": "code",
                    "source": ["1 + 1"],
                    "outputs": [
                        {
                            "output_type": "execute_result",
                            "data": {"text/plain": ["2"]},
                        }
                    ],
                }
            ]
        }
        result = NotebookParser().parse(json.dumps(nb))
        assert "## Output:" in result
        assert "2" in result

    def test_output_text_is_truncated_to_500_chars(self) -> None:
        long_text = "y" * 1000
        nb = {
            "cells": [
                {
                    "cell_type": "code",
                    "source": ["x"],
                    "outputs": [{"output_type": "stream", "text": long_text}],
                }
            ]
        }
        result = NotebookParser().parse(json.dumps(nb))
        # only the first 500 chars of output should appear
        assert "y" * 500 in result
        assert "y" * 501 not in result

    def test_default_kernel_language_is_python(self) -> None:
        nb = {"cells": [{"cell_type": "code", "source": ["x = 1"], "outputs": []}]}
        result = NotebookParser().parse(json.dumps(nb))
        assert "(python)" in result

    def test_unrecognized_output_type_is_ignored(self) -> None:
        nb = {
            "cells": [
                {
                    "cell_type": "code",
                    "source": ["x"],
                    "outputs": [{"output_type": "error", "ename": "ValueError"}],
                }
            ]
        }
        result = NotebookParser().parse(json.dumps(nb))
        assert "## Output:" not in result


# ═══════════════════════════════════════════════════════════════════════════
# MarkdownParser
# ═══════════════════════════════════════════════════════════════════════════


class TestMarkdownParser:
    def test_strips_front_matter(self) -> None:
        content = "---\ntitle: Foo\n---\n# Heading\nBody text"
        result = MarkdownParser().parse(content)
        assert "title: Foo" not in result
        assert "Heading" in result

    def test_bold_and_italic_stripped(self) -> None:
        content = "This is **bold** and *italic* text."
        result = MarkdownParser().parse(content)
        assert result == "This is bold and italic text."

    def test_inline_code_stripped(self) -> None:
        content = "Use `print()` to output."
        result = MarkdownParser().parse(content)
        assert result == "Use print() to output."

    def test_images_replaced_with_alt_text(self) -> None:
        content = "![a cat](http://example.com/cat.png)"
        result = MarkdownParser().parse(content)
        assert result == "[Image: a cat]"

    def test_links_replaced_with_text(self) -> None:
        content = "See [the docs](http://example.com/docs) for more."
        result = MarkdownParser().parse(content)
        assert result == "See the docs for more."

    def test_excess_blank_lines_collapsed(self) -> None:
        content = "Para1\n\n\n\n\nPara2"
        result = MarkdownParser().parse(content)
        assert "\n\n\n" not in result

    def test_parse_sections_splits_by_heading(self) -> None:
        content = "# Title\nIntro text.\n## Sub\nSub text."
        sections = MarkdownParser().parse_sections(content)
        assert len(sections) == 2
        assert sections[0]["heading"] == "Title"
        assert sections[0]["level"] == 1
        assert "Intro text." in sections[0]["body"]
        assert sections[1]["heading"] == "Sub"
        assert sections[1]["level"] == 2

    def test_parse_sections_with_no_headings_returns_single_section(self) -> None:
        content = "Just plain text, no headings."
        sections = MarkdownParser().parse_sections(content)
        assert len(sections) == 1
        assert sections[0]["heading"] == ""
        assert "Just plain text" in sections[0]["body"]

    def test_parse_sections_empty_content(self) -> None:
        sections = MarkdownParser().parse_sections("")
        assert sections == []

    # ── Edge cases: malformed front matter, code fences, lists, tables, HTML ──

    def test_malformed_front_matter_missing_closing_marker_is_left_intact(self) -> None:
        # No closing `---` — the front-matter regex requires a matching close,
        # so this should NOT be stripped (left as ordinary body text).
        content = "---\ntitle: Foo\nno closing marker here\n# Heading\nBody"
        result = MarkdownParser().parse(content)
        assert "title: Foo" in result
        assert "Heading" in result

    def test_code_fence_content_is_preserved_verbatim(self) -> None:
        # Regression test: formatting-stripping regexes must not rewrite the
        # contents of a fenced code block (e.g. a snippet demonstrating
        # markdown syntax, or Python using ** for exponentiation).
        content = (
            "Intro text.\n\n"
            "```python\n"
            "# a comment\n"
            "x = 2 ** 10\n"
            'return "**not bold**"\n'
            "```\n\n"
            "More text with **real bold** after."
        )
        result = MarkdownParser().parse(content)
        assert "```python" in result
        assert "x = 2 ** 10" in result
        assert '"**not bold**"' in result
        # Formatting outside the fence is still processed normally.
        assert "real bold" in result
        assert "**real bold**" not in result

    def test_nested_triple_backtick_inside_four_backtick_fence(self) -> None:
        content = "````markdown\nExample:\n```\ncode inside\n```\n````"
        result = MarkdownParser().parse(content)
        assert "code inside" in result

    def test_deeply_nested_lists_preserve_bullet_markers(self) -> None:
        content = (
            "- level 1\n"
            "  - level 2\n"
            "    - level 3\n"
            "      - level 4 with *emphasis* text\n"
            "        - level 5\n"
        )
        result = MarkdownParser().parse(content)
        assert "- level 1" in result
        assert "- level 2" in result
        assert "- level 5" in result
        assert "emphasis" in result

    def test_bullet_marker_survives_when_line_also_has_italic(self) -> None:
        # Regression test: a `* ` list marker must not be consumed as the
        # opening delimiter of a same-line italic run.
        content = "* item one\n* item two with *emphasis* inline\n* item three"
        result = MarkdownParser().parse(content)
        assert result.count("* item") == 3
        assert "emphasis" in result

    def test_table_syntax_passes_through_unchanged(self) -> None:
        content = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = MarkdownParser().parse(content)
        assert result == content

    def test_mixed_html_in_markdown_left_as_is(self) -> None:
        content = 'Some <div class="x">raw html</div> and **bold** text.'
        result = MarkdownParser().parse(content)
        assert "<div class=\"x\">raw html</div>" in result
        assert "bold" in result
        assert "**bold**" not in result


class TestYAMLParser:
    def test_parses_yaml_content(self) -> None:
        content = "key: value\nnested:\n  a: 1\n"
        result = YAMLParser().parse(content, file_ext=".yaml")
        assert "key" in result or result == content[:10000]

    def test_yaml_extension_yml(self) -> None:
        content = "a: 1"
        result = YAMLParser().parse(content, file_ext=".yml")
        assert result

    def test_toml_parsing(self) -> None:
        content = 'name = "test"\nversion = "1.0"\n'
        result = YAMLParser().parse(content, file_ext=".toml")
        assert result

    def test_hcl_generic_strips_comments(self) -> None:
        content = "# a comment\nresource \"foo\" {\n  bar = 1\n}\n// another comment"
        result = YAMLParser().parse(content, file_ext=".tf")
        assert "# a comment" not in result
        assert "// another comment" not in result
        assert 'resource "foo" {' in result

    def test_yaml_parse_error_falls_back_to_raw(self) -> None:
        with patch.dict("sys.modules", {"yaml": None}):
            result = YAMLParser().parse("key: value", file_ext=".yaml")
        assert result == "key: value"

    def test_empty_yaml_falls_back_to_raw(self) -> None:
        result = YAMLParser()._parse_yaml("")
        assert result == ""


# ═══════════════════════════════════════════════════════════════════════════
# HTMLParser
# ═══════════════════════════════════════════════════════════════════════════


class TestHTMLParser:
    def test_trafilatura_extraction_used_when_available(self) -> None:
        fake_trafilatura = MagicMock()
        fake_trafilatura.extract.return_value = (
            "Clean article text that is long enough here to pass the length threshold."
        )
        with patch.dict("sys.modules", {"trafilatura": fake_trafilatura}):
            result = HTMLParser().parse("<html><body>ignored</body></html>")
        assert result == (
            "Clean article text that is long enough here to pass the length threshold."
        )

    def test_trafilatura_short_result_falls_through_to_bs4(self) -> None:
        fake_trafilatura = MagicMock()
        fake_trafilatura.extract.return_value = "short"
        html = "<html><body><p>Real content here that bs4 should extract cleanly.</p></body></html>"
        with patch.dict("sys.modules", {"trafilatura": fake_trafilatura}):
            result = HTMLParser().parse(html)
        assert "Real content here" in result

    def test_trafilatura_import_error_falls_back_to_bs4(self) -> None:
        html = "<html><body><script>bad()</script><p>Hello world</p></body></html>"
        with patch.dict("sys.modules", {"trafilatura": None}):
            result = HTMLParser().parse(html)
        assert "Hello world" in result
        assert "bad()" not in result

    def test_bs4_removes_script_style_nav(self) -> None:
        html = (
            "<html><body><nav>Menu</nav><style>.a{}</style>"
            "<script>evil()</script><header>Head</header>"
            "<p>Actual content</p><footer>Foot</footer></body></html>"
        )
        with patch.dict("sys.modules", {"trafilatura": None}):
            result = HTMLParser().parse(html)
        assert "Actual content" in result
        assert "Menu" not in result
        assert "evil()" not in result

    def test_full_fallback_regex_strip_when_bs4_and_trafilatura_missing(self) -> None:
        html = "<div><p>Fallback <b>text</b></p></div>"
        with patch.dict("sys.modules", {"trafilatura": None, "bs4": None}):
            result = HTMLParser().parse(html)
        assert result == "Fallback text"

    def test_result_truncated_to_50000_chars_in_bs4_path(self) -> None:
        html = "<p>" + ("z" * 60000) + "</p>"
        with patch.dict("sys.modules", {"trafilatura": None}):
            result = HTMLParser().parse(html)
        assert len(result) <= 50000

    def test_trafilatura_exception_falls_back(self) -> None:
        fake_trafilatura = MagicMock()
        fake_trafilatura.extract.side_effect = RuntimeError("parse error")
        html = "<html><body><p>Fallback content here</p></body></html>"
        with patch.dict("sys.modules", {"trafilatura": fake_trafilatura}):
            result = HTMLParser().parse(html)
        assert "Fallback content" in result

    # ── Edge cases: malformed HTML, deep nesting, script/style, encoding ──────

    def test_bs4_generic_exception_falls_back_to_regex(self) -> None:
        fake_bs4 = MagicMock()
        fake_bs4.BeautifulSoup.side_effect = RuntimeError("bs4 internal error")
        html = "<p>Regex fallback content</p>"
        with patch.dict("sys.modules", {"trafilatura": None, "bs4": fake_bs4}):
            result = HTMLParser().parse(html)
        assert "Regex fallback content" in result

    def test_malformed_html_unclosed_tags_does_not_raise(self) -> None:
        html = "<html><body><div><p>Unclosed paragraph<div>Another block</body>"
        with patch.dict("sys.modules", {"trafilatura": None}):
            result = HTMLParser().parse(html)
        assert "Unclosed paragraph" in result
        assert "Another block" in result

    def test_malformed_html_full_fallback_no_bs4_no_trafilatura(self) -> None:
        html = "<div><p>Broken <b>markup missing closes"
        with patch.dict("sys.modules", {"trafilatura": None, "bs4": None}):
            result = HTMLParser().parse(html)
        assert "Broken" in result
        assert "markup missing closes" in result
        assert "<" not in result

    def test_extremely_deep_dom_nesting_does_not_raise(self) -> None:
        depth = 500
        html = "<div>" * depth + "deeply nested content" + "</div>" * depth
        with patch.dict("sys.modules", {"trafilatura": None}):
            result = HTMLParser().parse(html)
        assert "deeply nested content" in result

    def test_script_tag_content_stripped_in_bs4_path(self) -> None:
        html = (
            "<html><body><script>var secret = 'do-not-index';</script>"
            "<p>Visible article content.</p></body></html>"
        )
        with patch.dict("sys.modules", {"trafilatura": None}):
            result = HTMLParser().parse(html)
        assert "Visible article content" in result
        assert "secret" not in result
        assert "do-not-index" not in result

    def test_style_tag_content_stripped_in_bs4_path(self) -> None:
        html = (
            "<html><head><style>body { color: red; }</style></head>"
            "<body><p>Visible text.</p></body></html>"
        )
        with patch.dict("sys.modules", {"trafilatura": None}):
            result = HTMLParser().parse(html)
        assert "Visible text" in result
        assert "color: red" not in result

    def test_script_and_style_content_stripped_in_ultimate_regex_fallback(self) -> None:
        # Regression test: when BOTH trafilatura and bs4 are unavailable, the
        # last-resort regex fallback must strip script/style *content* too —
        # not just their surrounding tags — or raw JS/CSS source leaks into
        # the extracted (and later indexed) document text.
        html = (
            "<html><body><script>var evil_token = 12345; doBadThing();</script>"
            "<style>.a { color: blue; }</style>"
            "<p>Real content here.</p></body></html>"
        )
        with patch.dict("sys.modules", {"trafilatura": None, "bs4": None}):
            result = HTMLParser().parse(html)
        assert "Real content here" in result
        assert "evil_token" not in result
        assert "doBadThing" not in result
        assert "color: blue" not in result

    def test_non_ascii_encoding_characters_preserved(self) -> None:
        html = "<html><body><p>Café résumé — naïve façade 日本語 emoji 🎉</p></body></html>"
        with patch.dict("sys.modules", {"trafilatura": None}):
            result = HTMLParser().parse(html)
        assert "Café" in result
        assert "日本語" in result
        assert "🎉" in result

    def test_html_entities_in_ultimate_fallback_left_as_is(self) -> None:
        # The ultimate regex fallback does not decode entities — documents
        # current (best-effort) behaviour rather than asserting decoding.
        html = "<p>Fish &amp; Chips &lt;tag&gt;</p>"
        with patch.dict("sys.modules", {"trafilatura": None, "bs4": None}):
            result = HTMLParser().parse(html)
        assert "Fish &amp; Chips &lt;tag&gt;" in result


# ═══════════════════════════════════════════════════════════════════════════
# ParquetParser
# ═══════════════════════════════════════════════════════════════════════════


class TestParquetParser:
    def _make_parquet_bytes(self) -> bytes:
        import io

        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.table({"id": [1, 2, 3], "name": ["alice", "bob", "carol"]})
        buf = io.BytesIO()
        pq.write_table(table, buf)
        return buf.getvalue()

    def test_parses_schema_and_sample_rows(self) -> None:
        content = self._make_parquet_bytes()
        result = ParquetParser().parse(content, filename="people.parquet")
        assert "File: people.parquet" in result
        assert "id (int64)" in result
        assert "name" in result
        assert "Rows: 3, Columns: 2" in result
        assert "id: 1" in result
        assert "name: alice" in result

    def test_parses_without_filename(self) -> None:
        content = self._make_parquet_bytes()
        result = ParquetParser().parse(content)
        assert "File:" not in result
        assert "Schema:" in result

    def test_column_truncation_beyond_max_cols(self) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq
        import io

        columns = {f"col{i}": [1, 2] for i in range(60)}
        table = pa.table(columns)
        buf = io.BytesIO()
        pq.write_table(table, buf)
        result = ParquetParser().parse(buf.getvalue())
        assert "more columns" in result

    def test_import_error_falls_back_to_placeholder(self) -> None:
        with patch.dict("sys.modules", {"pyarrow": None, "pyarrow.parquet": None}):
            result = ParquetParser().parse(b"irrelevant", filename="data.parquet")
        assert result == "[Parquet file: data.parquet — install pyarrow to extract]"

    def test_malformed_bytes_falls_back_to_error_message(self) -> None:
        result = ParquetParser().parse(b"not a real parquet file", filename="bad.parquet")
        assert result.startswith("[Parquet parse failed:")
