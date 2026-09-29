"""Regression: the RAG_INGEST guardrail failed OPEN.

``IngestionPipeline.screen_text`` wrapped the Guardrails 2.0 RAG_INGEST check in
``except Exception: log`` and returned the text as clean. So when the engine
raised — including ``GuardrailRulesUnavailableError`` when a tenant's persisted
rules could not be loaded — the document was chunked, embedded and indexed
unscreened. It must now be refused: the connector path reports the document as
``failed`` (DLQ / retried, nothing indexed) and the direct ingest routes answer
503.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.guardrails_v2.engine import GuardrailRulesUnavailableError, guardrails_engine
from app.ingestion.pipeline import (
    IngestionPipeline,
    IngestionScreeningUnavailableError,
    screen_ingest_text,
)
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily

_TENANT = "tid-rag-ingest-fail-closed"
_TEXT = (
    "Quarterly operations summary. Throughput rose four percent and the backlog "
    "of open tickets fell to its lowest level since the start of the year."
)


class _KB:
    def __init__(self) -> None:
        self.stored: list[Any] = []

    async def add_chunks(self, *args: Any, **kwargs: Any) -> list[str]:
        self.stored.append((args, kwargs))
        return ["c1"]


class _Embedder:
    def __init__(self) -> None:
        self.seen: list[str] = []

    async def embed(self, request: Any) -> Any:
        from app.providers.base import EmbedResponse

        self.seen.extend(request.texts)
        return EmbedResponse(embeddings=[[0.1] * 8 for _ in request.texts], model="fake")


def _raise_unavailable(*_a: Any, **_k: Any) -> Any:
    raise GuardrailRulesUnavailableError("rules for tenant could not be loaded")


def _raise_boom(*_a: Any, **_k: Any) -> Any:
    raise RuntimeError("guardrail engine exploded")


@pytest.mark.parametrize("failure", [_raise_unavailable, _raise_boom])
async def test_screen_text_raises_when_guardrail_errors(
    monkeypatch: pytest.MonkeyPatch, failure: Any
) -> None:
    async def _evaluate(*a: Any, **k: Any) -> Any:
        return failure()

    monkeypatch.setattr(guardrails_engine, "evaluate", _evaluate)
    pipeline = IngestionPipeline()
    with pytest.raises(IngestionScreeningUnavailableError) as info:
        await pipeline.screen_text(_TEXT, tenant_id=_TENANT, doc_id="d1")
    assert "guardrail" in str(info.value).lower()


async def test_screen_ingest_text_raises_when_rules_fail_to_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _evaluate(*a: Any, **k: Any) -> Any:
        return _raise_unavailable()

    monkeypatch.setattr(guardrails_engine, "evaluate", _evaluate)
    with pytest.raises(IngestionScreeningUnavailableError):
        await screen_ingest_text(_TEXT, tenant_id=_TENANT)


async def test_connector_ingest_fails_and_indexes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _evaluate(*a: Any, **k: Any) -> Any:
        return _raise_unavailable()

    monkeypatch.setattr(guardrails_engine, "evaluate", _evaluate)
    kb, embedder = _KB(), _Embedder()
    pipeline = IngestionPipeline(knowledge_store=kb, embedder=embedder)
    raw = RawDocument(
        doc_id="doc-1",
        source_id="src-1",
        tenant_id=_TENANT,
        content=_TEXT.encode(),
        content_type="text/plain",
    )
    config = SourceConfig(
        source_id="src-1",
        tenant_id=_TENANT,
        name="s",
        family=next(iter(SourceFamily)),
        source_type="http",
    )
    result = await pipeline.ingest(raw, config)
    assert result.status == "failed", result
    assert "guardrail" in (result.error or "").lower()
    assert embedder.seen == [] and kb.stored == []
