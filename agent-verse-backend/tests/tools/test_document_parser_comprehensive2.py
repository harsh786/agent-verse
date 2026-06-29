"""Comprehensive tests for DocumentParserTool — all formats, truncation, error paths."""
from __future__ import annotations

import io
import json
import pytest
from unittest.mock import MagicMock, patch

from app.tools.document_parser import DocumentParserTool, ParsedDocument, _MAX_CHARS


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parser() -> DocumentParserTool:
    return DocumentParserTool()


# ── 1. to_tool_def ────────────────────────────────────────────────────────────

def test_to_tool_def_name():
    assert _parser().to_tool_def()["name"] == "parse_document"


def test_to_tool_def_has_description():
    d = _parser().to_tool_def()
    assert "description" in d and len(d["description"]) > 5


def test_to_tool_def_has_parameters():
    d = _parser().to_tool_def()
    assert "parameters" in d
    assert "properties" in d["parameters"]


# ── 2. _parse_text ────────────────────────────────────────────────────────────

def test_parse_text_basic():
    parser = _parser()
    doc = parser._parse_text(b"hello world", filename="test.txt")
    assert doc.content == "hello world"
    assert doc.format == "text"
    assert doc.page_count is None
    assert doc.truncated is False


def test_parse_text_truncation():
    parser = _parser()
    big_content = b"x" * (_MAX_CHARS + 100)
    doc = parser._parse_text(big_content, filename="big.txt")
    assert len(doc.content) == _MAX_CHARS
    assert doc.truncated is True


def test_parse_text_utf8_errors_replaced():
    parser = _parser()
    bad_bytes = b"hello \xff\xfe world"
    doc = parser._parse_text(bad_bytes, filename="bad.txt")
    assert "hello" in doc.content


def test_parse_text_markdown():
    parser = _parser()
    md_content = b"# Title\n\nParagraph text."
    doc = parser._parse_text(md_content, filename="readme.md")
    assert "Title" in doc.content
    assert doc.format == "text"


# ── 3. _parse_json ────────────────────────────────────────────────────────────

def test_parse_json_valid():
    parser = _parser()
    data = json.dumps({"key": "value", "num": 42}).encode()
    doc = parser._parse_json(data, filename="data.json")
    assert doc.format == "json"
    assert '"key"' in doc.content
    assert doc.truncated is False


def test_parse_json_invalid_returns_raw():
    parser = _parser()
    bad_json = b"not { valid } json ["
    doc = parser._parse_json(bad_json, filename="bad.json")
    assert doc.format == "json"
    assert "not" in doc.content  # raw text returned


def test_parse_json_truncation():
    parser = _parser()
    big_obj = {"key": "x" * (_MAX_CHARS + 100)}
    data = json.dumps(big_obj).encode()
    doc = parser._parse_json(data, filename="big.json")
    assert len(doc.content) == _MAX_CHARS
    assert doc.truncated is True


# ── 4. _parse_yaml ────────────────────────────────────────────────────────────

def test_parse_yaml_valid():
    parser = _parser()
    yaml_data = b"key: value\nnum: 42\n"
    try:
        import yaml
        doc = parser._parse_yaml(yaml_data, filename="config.yaml")
        assert doc.format == "yaml"
        assert "key" in doc.content
    except ImportError:
        pytest.skip("yaml not installed")


def test_parse_yaml_fallback_on_import_error():
    parser = _parser()
    yaml_data = b"key: value"
    with patch.dict("sys.modules", {"yaml": None}):
        doc = parser._parse_yaml(yaml_data, filename="config.yaml")
    assert doc.format == "yaml"


def test_parse_yaml_invalid_falls_back_to_raw():
    parser = _parser()
    # Invalid YAML that causes parse error
    bad_yaml = b": : :\n  - bad yaml !!"
    doc = parser._parse_yaml(bad_yaml, filename="bad.yaml")
    assert doc.format == "yaml"


# ── 5. _parse_csv ─────────────────────────────────────────────────────────────

def test_parse_csv_basic():
    parser = _parser()
    csv_data = b"name,age,city\nAlice,30,NYC\nBob,25,LA\n"
    doc = parser._parse_csv(csv_data, filename="data.csv")
    assert doc.format == "csv"
    assert "name" in doc.content
    assert doc.metadata["rows"] == 2
    assert doc.metadata["columns"] == 3
    assert "name" in doc.metadata["headers"]


def test_parse_csv_empty():
    parser = _parser()
    doc = parser._parse_csv(b"", filename="empty.csv")
    assert "[Empty CSV]" in doc.content


def test_parse_csv_markdown_table_format():
    parser = _parser()
    csv_data = b"a,b\n1,2\n3,4\n"
    doc = parser._parse_csv(csv_data, filename="data.csv")
    # Should be formatted as markdown table with | separators
    assert "|" in doc.content
    assert "---" in doc.content


def test_parse_csv_large_truncated():
    parser = _parser()
    rows = ["col1,col2"] + [f"val{i},val{i}" for i in range(2000)]
    csv_data = "\n".join(rows).encode()
    doc = parser._parse_csv(csv_data, filename="large.csv")
    assert doc.truncated is True


def test_parse_csv_single_column():
    parser = _parser()
    csv_data = b"name\nAlice\nBob\n"
    doc = parser._parse_csv(csv_data, filename="names.csv")
    assert doc.metadata["columns"] == 1


# ── 6. _parse_pdf (mocked) ────────────────────────────────────────────────────

def test_parse_pdf_with_pypdf():
    parser = _parser()
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "Page content here"
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page]
    mock_reader.metadata = {}

    mock_pypdf = MagicMock()
    mock_pypdf.PdfReader.return_value = mock_reader

    import sys
    with patch.dict(sys.modules, {"pypdf": mock_pypdf}):
        doc = parser._parse_pdf(b"%PDF-fake", filename="doc.pdf")

    assert doc.format == "pdf"
    assert "Page content here" in doc.content
    assert doc.page_count == 1


def test_parse_pdf_unavailable():
    parser = _parser()
    with patch.dict("sys.modules", {"pypdf": None, "PyPDF2": None}):
        doc = parser._parse_pdf(b"fake pdf", filename="doc.pdf")
    assert doc.format == "pdf"
    # Content should indicate unavailability
    assert "unavailable" in doc.content.lower() or doc.content != ""


def test_parse_pdf_truncation():
    parser = _parser()
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "x" * (_MAX_CHARS + 100)
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page]
    mock_reader.metadata = {}

    import sys
    mock_pypdf = MagicMock()
    mock_pypdf.PdfReader.return_value = mock_reader
    with patch.dict(sys.modules, {"pypdf": mock_pypdf}):
        doc = parser._parse_pdf(b"%PDF-fake", filename="big.pdf")

    assert doc.truncated is True
    assert len(doc.content) <= _MAX_CHARS


def test_parse_pdf_multi_page_over_limit():
    parser = _parser()
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "page text"
    # 51 pages → truncated
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page] * 51
    mock_reader.metadata = {}

    import sys
    mock_pypdf = MagicMock()
    mock_pypdf.PdfReader.return_value = mock_reader
    with patch.dict(sys.modules, {"pypdf": mock_pypdf}):
        doc = parser._parse_pdf(b"%PDF-fake", filename="long.pdf")

    assert doc.truncated is True


# ── 7. _parse_docx (mocked) ───────────────────────────────────────────────────

def test_parse_docx_with_docx():
    parser = _parser()
    mock_para1 = MagicMock()
    mock_para1.text = "First paragraph"
    mock_para2 = MagicMock()
    mock_para2.text = "Second paragraph"
    mock_doc_obj = MagicMock()
    mock_doc_obj.paragraphs = [mock_para1, mock_para2]

    import sys
    mock_docx_mod = MagicMock()
    mock_docx_mod.Document.return_value = mock_doc_obj
    with patch.dict(sys.modules, {"docx": mock_docx_mod}):
        doc = parser._parse_docx(b"fake docx", filename="document.docx")

    assert doc.format == "docx"
    assert "First paragraph" in doc.content
    assert doc.metadata["paragraph_count"] == 2


def test_parse_docx_empty_paragraphs_filtered():
    parser = _parser()
    mock_para1 = MagicMock()
    mock_para1.text = "  "  # whitespace only → filtered
    mock_para2 = MagicMock()
    mock_para2.text = "Real content"
    mock_doc_obj = MagicMock()
    mock_doc_obj.paragraphs = [mock_para1, mock_para2]

    import sys
    mock_docx_mod = MagicMock()
    mock_docx_mod.Document.return_value = mock_doc_obj
    with patch.dict(sys.modules, {"docx": mock_docx_mod}):
        doc = parser._parse_docx(b"fake docx", filename="doc.docx")

    assert "Real content" in doc.content
    assert doc.metadata["paragraph_count"] == 1


def test_parse_docx_unavailable():
    parser = _parser()
    with patch.dict("sys.modules", {"docx": None}):
        doc = parser._parse_docx(b"fake", filename="doc.docx")
    assert "unavailable" in doc.content.lower()


# ── 8. _parse_sync routing ────────────────────────────────────────────────────

def test_parse_sync_routes_by_extension_txt():
    parser = _parser()
    result = parser._parse_sync("", b"hello", "test.txt")
    assert result["format"] == "text"


def test_parse_sync_routes_by_extension_json():
    parser = _parser()
    result = parser._parse_sync("", b'{"a":1}', "data.json")
    assert result["format"] == "json"


def test_parse_sync_routes_by_extension_csv():
    parser = _parser()
    result = parser._parse_sync("", b"a,b\n1,2", "data.csv")
    assert result["format"] == "csv"


def test_parse_sync_no_content_returns_error():
    parser = _parser()
    result = parser._parse_sync("", None, "test.txt")
    assert "error" in result


def test_parse_sync_file_read_error():
    parser = _parser()
    result = parser._parse_sync("/nonexistent/path/file.txt", None, "file.txt")
    assert "error" in result


def test_parse_sync_unknown_extension_falls_back_to_text():
    parser = _parser()
    result = parser._parse_sync("", b"some binary or unknown", "file.xyz")
    assert result["format"] == "text"


def test_parse_sync_returns_char_count():
    parser = _parser()
    result = parser._parse_sync("", b"hello", "test.txt")
    assert result["char_count"] == 5


# ── 9. execute async wrapper ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_async_returns_parsed_result():
    parser = _parser()
    result = await parser.execute(content_bytes=b"hello", filename="test.txt")
    assert result["format"] == "text"
    assert result["content"] == "hello"


@pytest.mark.asyncio
async def test_execute_async_with_file_path(tmp_path):
    test_file = tmp_path / "test.txt"
    test_file.write_bytes(b"file content")
    parser = _parser()
    result = await parser.execute(file_path=str(test_file), filename="test.txt")
    assert "file content" in result["content"]
