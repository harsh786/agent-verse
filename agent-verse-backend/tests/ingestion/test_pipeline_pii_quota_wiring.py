"""Regression: pipeline Stage 1 (quota) and Stage 6 (PII) were never wired.

``IngestionPipeline`` accepted ``quota_enforcer`` / ``pii_analyzer`` but no
construction site (``create_app``, the Celery worker) ever passed them, so both
stages were skipped for every document; and Stage 6 was written against
Presidio, which is not installed. These pin:

* the construction sites really wire a PII analyzer (and the worker a quota
  enforcer);
* PII is redacted / rejected before chunks reach the embedder and the store;
* an analyzer error fails closed (``failed``) instead of indexing unscanned text;
* a real quota breach skips ``quota_exceeded``; a DB error is ``failed``, not a
  mislabelled quota skip.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from app.ingestion.pii import RegexPIIAnalyzer
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.quota import IngestionQuotaExceededError
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily

_TENANT = "pii-quota-tenant"
_BODY = (
    "Customer record for the quarterly onboarding review. The customer social "
    "security number is 123-45-6789 and all other fields were verified by staff."
)


class _Embedder:
    def __init__(self) -> None:
        self.seen: list[str] = []

    async def embed(self, request: Any) -> Any:
        from app.providers.base import EmbedResponse

        self.seen.extend(request.texts)
        return EmbedResponse(embeddings=[[0.1] * 8 for _ in request.texts], model="fake")


class _Store:
    def __init__(self) -> None:
        self.chunks: list[Any] = []

    async def exists_by_hash(self, **_: Any) -> bool:
        return False

    async def ingest_chunks_async(self, chunks: list[Any], **_: Any) -> list[str]:
        self.chunks.extend(chunks)
        return [c.chunk_id for c in chunks]


def _doc(body: str = _BODY) -> RawDocument:
    return RawDocument(
        doc_id="d1",
        source_id="s1",
        tenant_id=_TENANT,
        content=body.encode(),
        content_type="text/plain",
    )


def _config(**overrides: Any) -> SourceConfig:
    fields: dict[str, Any] = dict(
        source_id="s1",
        tenant_id=_TENANT,
        name="src",
        family=SourceFamily.WEB,
        source_type="test",
        collection_id="c1",
        min_quality_score=0.0,
    )
    fields.update(overrides)
    return SourceConfig(**fields)


def _pipeline(**kwargs: Any) -> tuple[IngestionPipeline, _Store, _Embedder]:
    store, embedder = _Store(), _Embedder()
    return IngestionPipeline(knowledge_store=store, embedder=embedder, **kwargs), store, embedder


# ── wiring ────────────────────────────────────────────────────────────────────


def test_create_app_wires_a_pii_analyzer_into_the_pipeline() -> None:
    from app.main import create_app

    app = create_app()
    pipeline = app.state.ingestion_pipeline
    assert pipeline is not None
    assert pipeline._pii is not None, "Stage 6 PII analyzer never wired (was always None)"


def test_worker_pipeline_wires_pii_and_quota() -> None:
    from app.ingestion.quota import IngestionQuotaEnforcer
    from app.ingestion.scheduler import _build_worker_ingestion

    with patch("app.providers.registry.resolve_provider", return_value=_Embedder()):
        _tracker, pipeline, _store = _build_worker_ingestion()
    assert isinstance(pipeline._pii, RegexPIIAnalyzer)  # type: ignore[attr-defined]
    assert isinstance(pipeline._quota, IngestionQuotaEnforcer)  # type: ignore[attr-defined]


# ── Stage 6 PII ───────────────────────────────────────────────────────────────


async def test_pii_is_redacted_before_embedding_and_indexing() -> None:
    pipeline, store, embedder = _pipeline(pii_analyzer=RegexPIIAnalyzer())
    result = await pipeline.ingest(_doc(), _config(pii_action="redact"))
    assert result.status == "indexed", result
    assert embedder.seen and all("123-45-6789" not in t for t in embedder.seen)
    assert all("123-45-6789" not in c.content for c in store.chunks)
    assert any("[REDACTED:SSN]" in c.content for c in store.chunks)
    assert all(c.metadata["has_pii_redacted"] is True for c in store.chunks)


async def test_pii_reject_skips_the_document() -> None:
    pipeline, store, embedder = _pipeline(pii_analyzer=RegexPIIAnalyzer())
    result = await pipeline.ingest(_doc(), _config(pii_action="reject"))
    assert (result.status, result.skip_reason) == ("skipped", "pii_rejected")
    assert store.chunks == [] and embedder.seen == []


async def test_pii_analyzer_error_fails_closed() -> None:
    class _Broken:
        def analyze(self, **_: Any) -> list[Any]:
            raise RuntimeError("analyzer down")

    pipeline, store, _ = _pipeline(pii_analyzer=_Broken())
    result = await pipeline.ingest(_doc(), _config(pii_action="redact"))
    assert result.status == "failed"
    assert "pii_scan_failed" in result.error
    assert store.chunks == []


# ── Stage 1 quota ─────────────────────────────────────────────────────────────


async def test_async_quota_breach_skips_quota_exceeded() -> None:
    class _Quota:
        async def check_doc_quota(self, tenant_id: str) -> None:
            raise IngestionQuotaExceededError("document", 10, 10, "free")

    pipeline, store, _ = _pipeline(quota_enforcer=_Quota())
    result = await pipeline.ingest(_doc(), _config())
    assert (result.status, result.skip_reason) == ("skipped", "quota_exceeded")
    assert store.chunks == []


async def test_quota_backend_error_is_a_failure_not_a_quota_skip() -> None:
    class _Quota:
        async def check_doc_quota(self, tenant_id: str) -> None:
            raise ConnectionError("db down")

    pipeline, _, _ = _pipeline(quota_enforcer=_Quota())
    result = await pipeline.ingest(_doc(), _config())
    assert result.status == "failed"
    assert result.skip_reason == ""


async def test_quota_within_limit_indexes() -> None:
    calls: list[str] = []

    class _Quota:
        async def check_doc_quota(self, tenant_id: str) -> None:
            calls.append(tenant_id)

    pipeline, store, _ = _pipeline(quota_enforcer=_Quota())
    result = await pipeline.ingest(_doc(), _config())
    assert result.status == "indexed"
    assert calls == [_TENANT] and store.chunks
