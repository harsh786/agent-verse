"""P0-11: the scheduler called pipeline.run(...).success — neither existed."""

from __future__ import annotations

import inspect

from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import PipelineResult


def test_pipeline_result_success_and_skipped_accessors():
    """FAILS TODAY: scheduler reads result.success / result.skipped; only .status existed."""
    indexed = PipelineResult(doc_id="d", source_id="s", tenant_id="t", status="indexed")
    assert indexed.success is True
    assert indexed.skipped is False

    skipped = PipelineResult(doc_id="d", source_id="s", tenant_id="t", status="skipped")
    assert skipped.success is False
    assert skipped.skipped is True

    failed = PipelineResult(doc_id="d", source_id="s", tenant_id="t", status="failed")
    assert failed.success is False
    assert failed.skipped is False


def test_pipeline_exposes_run_adapter():
    """FAILS TODAY: scheduler calls pipeline.run(...); no such method existed."""
    pipeline = IngestionPipeline()
    assert hasattr(pipeline, "run")
    assert inspect.iscoroutinefunction(pipeline.run)


async def test_run_without_source_config_skips_cleanly():
    """A RawDocument with no SourceConfig is skipped, not crashed or fabricated."""
    from app.ingestion.source_config import RawDocument

    pipeline = IngestionPipeline()
    raw = RawDocument(
        doc_id="d1", source_id="s1", tenant_id="t1", content=b"hi", content_type="text/plain"
    )
    result = await pipeline.run(raw)
    assert result.skipped is True
    assert result.skip_reason == "no_source_config"
