"""ING-9 (pipeline slice): pipeline token accounting must use the real tokenizer.

The semantic chunker already counts with ``app.agent.tokenizer.count_tokens``, but
``IngestionPipeline`` still tallied ``result.tokens_consumed`` with
``len(text.split())`` — a whitespace word count that diverges badly from real
tokens on code / CJK / punctuation-dense text. This asserts the dry-run path
reports the real token count.
"""

from __future__ import annotations

import pytest

from app.agent.tokenizer import count_tokens
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily

# CJK + code: whitespace-split count is wildly different from the token count.
_TEXT = "指令セット" * 40 + "\n" + "def f(x):return[x*x for x in range(x)]\n" * 20


def _config() -> SourceConfig:
    return SourceConfig(
        source_id="s1", tenant_id="t1", name="T", family=SourceFamily.WEB,
        source_type="test",
    )  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_pipeline_tokens_consumed_uses_real_tokenizer() -> None:
    pipeline = IngestionPipeline(dry_run=True)
    raw = RawDocument(
        doc_id="d1", source_id="s1", tenant_id="t1",
        content=_TEXT.encode("utf-8"), content_type="text/plain",
    )
    result = await pipeline.ingest(raw, _config())
    assert result.status == "dry_run"
    assert result.chunks_created >= 1

    parsed = pipeline.last_parsed_text
    naive_words = len(parsed.split())
    real_tokens = count_tokens(parsed)
    # The CJK/code fixture makes the real token count far exceed the whitespace
    # word count, so this proves the pipeline switched off `.split()` counting.
    assert real_tokens > naive_words * 2
    assert result.tokens_consumed > naive_words
