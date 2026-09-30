"""KB-21: connector documents in binary / structured formats are really parsed.

On the connector path Parquet/Avro bytes were UTF-8-decoded and the fallback
indexed the raw ``PAR1...`` bytes; a ``.ipynb`` went through the JSON/Text
parser as raw notebook JSON; ``_quality_score`` was binary (1.0 / 0.1) and 1.0
when the checker failed; unparseable JSONL lines were dropped silently.
"""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import patch

import pytest

from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily

_TENANT = "t-kb21"


class _Store:
    def __init__(self) -> None:
        self.chunks: list[Any] = []

    async def exists_by_hash(self, **_: Any) -> bool:
        return False

    async def ingest_chunks_async(self, chunks: list[Any], **_: Any) -> list[str]:
        self.chunks.extend(chunks)
        return [c.chunk_id for c in chunks]


class _Embedder:
    async def embed(self, request: Any) -> Any:
        from app.providers.base import EmbedResponse

        return EmbedResponse(embeddings=[[0.1] * 8 for _ in request.texts], model="m")


def _config() -> SourceConfig:
    return SourceConfig(
        source_id="s",
        tenant_id=_TENANT,
        name="n",
        family=SourceFamily.OBJECT_STORAGE,
        source_type="s3",
        collection_id="c",
        min_quality_score=0.0,
    )


def _doc(content: bytes, *, title: str = "", mime: str = "application/octet-stream") -> RawDocument:
    return RawDocument(
        doc_id="d-" + (title or "x"),
        source_id="s",
        tenant_id=_TENANT,
        content=content,
        content_type=mime,
        title=title,
    )


def _parquet_bytes() -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table(
        {
            "customer": ["Acme Corporation", "Globex Industries", "Initech Systems"],
            "region": ["Northern Europe", "Western Pacific", "Central America"],
        }
    )
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


async def _ingest(doc: RawDocument) -> tuple[Any, _Store]:
    store = _Store()
    pipeline = IngestionPipeline(knowledge_store=store, embedder=_Embedder())
    result = await pipeline.ingest(doc, _config())
    return result, store


@pytest.mark.parametrize("title", ["customers.parquet", ""])
async def test_a_real_parquet_file_yields_row_text(title: str) -> None:
    result, store = await _ingest(_doc(_parquet_bytes(), title=title))
    assert result.status == "indexed", result
    text = " ".join(c.content for c in store.chunks)
    assert "Globex Industries" in text
    assert "PAR1" not in text


async def test_an_unparseable_parquet_file_fails_instead_of_indexing_bytes() -> None:
    garbage = b"PAR1" + b"\x00\x15\x04" * 200 + b"PAR1"
    result, store = await _ingest(_doc(garbage, title="broken.parquet"))
    assert result.status == "failed", result
    assert store.chunks == []


async def test_a_notebook_yields_cell_text_not_raw_json() -> None:
    nb = {
        "nbformat": 4,
        "metadata": {"kernelspec": {"language": "python"}},
        "cells": [
            {"cell_type": "markdown", "source": ["# Revenue analysis for the quarter"]},
            {
                "cell_type": "code",
                "source": ["total = sum(revenue_by_region.values())"],
                "outputs": [{"output_type": "stream", "text": ["42 million euros"]}],
            },
        ],
    }
    result, store = await _ingest(_doc(json.dumps(nb).encode(), title="analysis.ipynb"))
    assert result.status == "indexed", result
    text = " ".join(c.content for c in store.chunks)
    assert "Revenue analysis for the quarter" in text
    assert "total = sum(revenue_by_region.values())" in text
    assert '"cell_type"' not in text and '"nbformat"' not in text


async def test_unparseable_jsonl_lines_are_counted() -> None:
    body = "\n".join(
        [
            json.dumps({"event": "signup", "customer": "Acme Corporation"}),
            "{not json at all",
            json.dumps({"event": "upgrade", "customer": "Globex Industries"}),
            "also broken ]",
        ]
    )
    result, store = await _ingest(_doc(body.encode(), title="events.jsonl"))
    assert result.status == "indexed", result
    assert result.metadata["jsonl_lines_skipped"] == 2  # type: ignore[attr-defined]
    text = " ".join(c.content for c in store.chunks)
    assert "Globex Industries" in text


def test_quality_score_is_graded_and_zero_when_the_checker_fails() -> None:
    pipeline = IngestionPipeline()
    prose = pipeline._quality_score(
        "The quarterly review covered retention, onboarding and support backlog trends."
    )
    # A passing-but-mediocre text is no longer rounded up to 1.0, and a noisy
    # one is scored for what it is rather than a flat 0.1.
    mid = pipeline._quality_score("ab cd ef quarterly ab cd ef revenue ab cd ef growth " * 3)
    noisy = pipeline._quality_score("id 12 x 7 ab 99 zz 3 q 1 " * 5)
    assert prose == 1.0
    assert 0.5 < mid < 1.0
    assert noisy == 0.0
    with patch(
        "app.ingestion.quality_checks.QualityChecker.check", side_effect=RuntimeError("boom")
    ):
        assert pipeline._quality_score("anything at all here") == 0.0
