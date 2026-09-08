"""ING-13: the two ingestion stacks must agree.

``IngestionPipeline`` (byte/connector/Celery path) chunks via
``ChunkingStrategySelector.select_and_chunk``; ``IngestionOrchestrator``
(string/RAG-API path) chunks via ``IngestionOrchestrator._chunk``. Phase-0
(ING-6) unified both behind ``get_chunker_for_strategy``; this parity test pins
that they produce identical chunk boundaries for the same input + strategy, so
the two paths can never silently diverge again (the P0-8 class of bug).

Both stacks also construct the same ``ParserRegistry`` and
``ChunkingStrategySelector``, asserted below.
"""

from __future__ import annotations

import pytest

from app.ingestion.chunking_strategy_selector import ChunkingStrategySelector
from app.ingestion.content_classifier import ContentType
from app.ingestion.orchestrator import IngestionOrchestrator

# A document with headings, paragraphs, and a table-ish block so different
# strategies actually produce different boundaries (not all one chunk).
_DOC = (
    "# Quarterly Report\n\n"
    "Revenue rose twelve percent across all regions this quarter.\n\n"
    "## Regional Breakdown\n\n"
    "North America led with strong enterprise renewals and expansion.\n\n"
    "## Risks\n\n"
    "Currency headwinds and a lengthening sales cycle remain the key risks.\n\n"
    "region, revenue, growth\n"
    "na, 1200, 0.12\n"
    "emea, 800, 0.08\n"
)

# Standard strategies that both stacks dispatch through get_chunker_for_strategy
# (the orchestrator special-cases parent_child / sentence_window / fixed *before*
# the shared dispatch, so those are intentionally excluded from parity).
_SHARED_STRATEGIES = ["heading", "semantic", "table", "ast", "record", "row_group"]


@pytest.fixture
def selector() -> ChunkingStrategySelector:
    return ChunkingStrategySelector()


@pytest.fixture
def orchestrator() -> IngestionOrchestrator:
    return IngestionOrchestrator(knowledge_store=None, embedder=None)


@pytest.mark.parametrize("strategy", _SHARED_STRATEGIES)
def test_chunk_boundary_parity_between_stacks(
    strategy: str, selector: ChunkingStrategySelector, orchestrator: IngestionOrchestrator
) -> None:
    pipeline_chunks = selector.select_and_chunk(_DOC, ContentType.TEXT, strategy)
    orchestrator_chunks = orchestrator._chunk(_DOC, ContentType.TEXT, strategy)
    assert pipeline_chunks == orchestrator_chunks, (
        f"stacks diverged for strategy {strategy!r}: "
        f"pipeline={len(pipeline_chunks)} chunks, orchestrator={len(orchestrator_chunks)}"
    )
    # Sanity: the shared strategy actually produced content (no silent empty).
    assert pipeline_chunks and all(c.strip() for c in pipeline_chunks)


def test_both_stacks_share_parser_registry_and_selector(
    selector: ChunkingStrategySelector, orchestrator: IngestionOrchestrator
) -> None:
    from app.ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline(dry_run=True)
    assert type(pipeline._parser_registry) is type(orchestrator._parser_registry)
    assert type(pipeline._chunker_selector) is type(orchestrator._chunking_selector)
