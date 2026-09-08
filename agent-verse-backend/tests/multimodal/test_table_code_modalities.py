"""Tests for TABLE and CODE multimodal ingestion (Coverage-Matrix row 16, D-23).

Verifies:
  * The Modality enum round-trips the new TABLE and CODE members.
  * CODE ingestion produces modality=CODE spans that preserve code structure
    (function/class boundaries) rather than flattening code as prose.
  * TABLE ingestion produces a single structured modality=TABLE span
    (normalized to markdown/records) rather than flat text.
  * detect_and_extract_tables pulls table-like regions out of free text as
    TABLE spans, honestly labelling confidence and recording the extractor
    used in metadata.
"""

from __future__ import annotations

from app.multimodal.models import Modality
from app.multimodal.pipeline import MultimodalPipeline

# ── Enum round-trip ──────────────────────────────────────────────────────────


def test_modality_enum_has_table_and_code():
    assert Modality.TABLE == "table"
    assert Modality.CODE == "code"


def test_modality_enum_round_trips_table_and_code():
    assert Modality("table") is Modality.TABLE
    assert Modality("code") is Modality.CODE
    # value/name round-trip
    assert Modality(Modality.TABLE.value) is Modality.TABLE
    assert Modality(Modality.CODE.value) is Modality.CODE


# ── CODE ingestion ───────────────────────────────────────────────────────────


PY_SOURCE = (
    "import os\n"
    "\n"
    "\n"
    "def alpha(x):\n"
    "    return x + 1\n"
    "\n"
    "\n"
    "class Beta:\n"
    "    def method(self):\n"
    "        return 2\n"
)


async def test_ingest_code_yields_code_modality_spans():
    pipe = MultimodalPipeline()
    job = await pipe.ingest_code(PY_SOURCE, tenant_id="t1", language="python")

    assert job.status == "completed"
    assert job.asset_type is Modality.CODE
    assert job.spans, "expected at least one span"
    assert all(s.modality is Modality.CODE for s in job.spans)


async def test_ingest_code_preserves_structure_not_prose():
    pipe = MultimodalPipeline()
    job = await pipe.ingest_code(PY_SOURCE, tenant_id="t1", language="python")

    # Structure preserved: the function and the class each become their own span
    # (AST-aware), so we get more than one span and their symbol names are tagged.
    names = {s.metadata.get("name") for s in job.spans}
    assert "alpha" in names
    assert "Beta" in names

    # A code span must keep its source verbatim (not prose-chunked / reflowed).
    alpha_span = next(s for s in job.spans if s.metadata.get("name") == "alpha")
    assert "def alpha(x):" in alpha_span.content
    assert "return x + 1" in alpha_span.content
    assert alpha_span.metadata.get("symbol_type") == "function"
    assert alpha_span.metadata.get("language") == "python"


async def test_ingest_code_without_language_still_works():
    pipe = MultimodalPipeline()
    job = await pipe.ingest_code("SELECT 1;\nSELECT 2;\n", tenant_id="t1")
    assert job.status == "completed"
    assert job.spans
    assert all(s.modality is Modality.CODE for s in job.spans)
    # Non-python / unparseable falls back to a whole-content module span (honest).
    assert any("SELECT 1;" in s.content for s in job.spans)


# ── TABLE ingestion ──────────────────────────────────────────────────────────


async def test_ingest_table_from_records_yields_structured_table_span():
    pipe = MultimodalPipeline()
    rows = [
        {"name": "Ada", "role": "Engineer"},
        {"name": "Alan", "role": "Researcher"},
    ]
    job = await pipe.ingest_table(rows, tenant_id="t1")

    assert job.status == "completed"
    assert job.asset_type is Modality.TABLE
    assert len(job.spans) == 1
    span = job.spans[0]
    assert span.modality is Modality.TABLE
    # Normalized to a markdown table (structured, not flat text)
    assert "| name | role |" in span.content
    assert "Ada" in span.content and "Alan" in span.content
    # Structured records preserved in metadata
    assert span.metadata.get("columns") == ["name", "role"]
    assert span.metadata.get("row_count") == 2


async def test_ingest_table_from_csv_yields_table_span():
    pipe = MultimodalPipeline()
    csv_text = "name,role\nAda,Engineer\nAlan,Researcher\n"
    job = await pipe.ingest_table(csv_text, tenant_id="t1")

    assert job.status == "completed"
    span = job.spans[0]
    assert span.modality is Modality.TABLE
    assert span.metadata.get("columns") == ["name", "role"]
    assert span.metadata.get("row_count") == 2
    assert "| name | role |" in span.content


async def test_ingest_table_from_markdown_table_passthrough():
    pipe = MultimodalPipeline()
    md = "| a | b |\n| --- | --- |\n| 1 | 2 |\n"
    job = await pipe.ingest_table(md, tenant_id="t1")
    assert job.status == "completed"
    span = job.spans[0]
    assert span.modality is Modality.TABLE
    assert span.metadata.get("columns") == ["a", "b"]
    assert span.metadata.get("row_count") == 1


# ── detect_and_extract_tables helper ─────────────────────────────────────────


def test_detect_and_extract_tables_pulls_markdown_region():
    pipe = MultimodalPipeline()
    text = (
        "Here is some intro prose.\n\n"
        "| name | role |\n"
        "| --- | --- |\n"
        "| Ada | Engineer |\n"
        "| Alan | Researcher |\n\n"
        "And some trailing prose."
    )
    spans = pipe.detect_and_extract_tables(text)
    assert len(spans) == 1
    span = spans[0]
    assert span.modality is Modality.TABLE
    assert span.metadata.get("columns") == ["name", "role"]
    assert span.metadata.get("row_count") == 2
    # Honest confidence + extractor provenance
    assert 0.0 < span.confidence < 1.0
    assert span.metadata.get("extractor") == "heuristic-markdown"


def test_detect_and_extract_tables_returns_empty_when_no_table():
    pipe = MultimodalPipeline()
    assert pipe.detect_and_extract_tables("just some prose, nothing tabular here") == []
