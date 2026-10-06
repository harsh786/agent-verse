"""Replay of a dead-lettered single-URL ingest (``POST /knowledge/ingest/url``).

The route dead-letters a transient failure (upstream down / timeout, embedder,
screening or persistence unavailable) with a ``{"kind": "url", ...}`` payload;
the ingestion DLQ retry job (``ingestion.retry_dlq_entries`` and the operator
``POST /ingestion/dlq/{id}/retry``) replays it here, through the route's own
fetch → extract → screen → embed → persist code (``run_url_ingest``), on the
worker's services. Replays are idempotent: the document id is stable per
(tenant, collection, URL), an unchanged page is deduplicated by content hash, a
changed one replaces the previous version, and a legal hold is still honoured.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

URL_DLQ_KIND = "url"


class UrlReplayPermanentError(Exception):
    """No retry can fix this entry (blocked URL, 404, legal hold, bad payload)."""


class UrlReplayRetryableError(Exception):
    """This attempt failed but a later one may succeed."""


def url_ingest_dlq_payload(raw_doc_json: object) -> dict[str, Any] | None:
    """The replay parameters of a dead-lettered URL ingest, or None."""
    if not isinstance(raw_doc_json, str) or not raw_doc_json:
        return None
    try:
        payload = json.loads(raw_doc_json)
    except ValueError:
        return None
    if isinstance(payload, dict) and payload.get("kind") == URL_DLQ_KIND:
        return payload
    return None


def _worker_request(tenant_ctx: Any, pipeline: Any) -> Any:
    """A request-shaped object carrying the worker's services for ``run_url_ingest``."""
    from app.db.session import get_session_factory
    from app.governance.legal_holds import LegalHoldManager

    state = SimpleNamespace(
        knowledge_store=getattr(pipeline, "_kb", None),
        embedder=getattr(pipeline, "_embedder", None),
        # Screening (PII + RAG_INGEST guardrail) uses the worker pipeline.
        ingestion_pipeline=pipeline,
        ingestion_quota=getattr(pipeline, "_quota", None),
        # The hold check reads Postgres directly (Redis is only a positive cache);
        # ``manage_pools`` makes a missing manager fail closed, never skip.
        legal_hold_manager=LegalHoldManager(redis=None, db_factory=get_session_factory()),
        manage_pools=True,
    )
    return SimpleNamespace(
        app=SimpleNamespace(state=state), state=SimpleNamespace(tenant=tenant_ctx)
    )


async def replay_url_ingest(
    payload: dict[str, Any], *, tenant_id: str, pipeline: Any, request: Any = None
) -> dict[str, Any]:
    """Re-run the URL ingest a DLQ entry describes; the route's response body.

    Raises :class:`UrlReplayPermanentError` or :class:`UrlReplayRetryableError`.
    ``request`` overrides the worker request (tests).
    """
    from fastapi import HTTPException

    from app.api.knowledge import (
        _enforce_doc_quota_or_http,
        run_url_ingest,
        url_ingest_failure_retryable,
    )
    from app.tenancy.context import PlanTier, TenantContext

    url = str(payload.get("url") or "")
    collection_id = str(payload.get("collection_id") or "")
    source_type = str(payload.get("source_type") or "web")
    if not url or not collection_id:
        raise UrlReplayPermanentError("URL DLQ payload has no url / collection_id")
    tenant_ctx = TenantContext(
        tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="url-ingest-retry"
    )
    req = request if request is not None else _worker_request(tenant_ctx, pipeline)
    store = req.app.state.knowledge_store
    if store is None:
        raise UrlReplayRetryableError("worker has no knowledge store")
    try:
        await _enforce_doc_quota_or_http(req, tenant_ctx)
        return await run_url_ingest(
            req, tenant_ctx, store, url=url, collection_id=collection_id, source_type=source_type
        )
    except HTTPException as exc:
        message = f"{exc.status_code}: {exc.detail}"[:300]
        if exc.status_code == 429 or url_ingest_failure_retryable(exc):
            # 429: the tenant's document quota / budget may free up later.
            raise UrlReplayRetryableError(message) from exc
        raise UrlReplayPermanentError(message) from exc
    except Exception as exc:
        raise UrlReplayRetryableError(repr(exc)[:300]) from exc
