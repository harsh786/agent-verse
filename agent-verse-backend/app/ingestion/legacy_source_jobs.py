"""Durable one-shot GitHub / Confluence / Jira / Slack ingestion (a04-F067-01, F070-03).

``POST /knowledge/ingest/{github,confluence,jira,slack}`` used to fetch, screen
and embed a whole source (up to thousands of pages / messages) synchronously
inside the HTTP request on the API replica, with its own ingestor stack
(``app.knowledge.ingestors.*``: separate HTTP clients for Confluence / Jira and
fixed 1,200-character windows) beside the connector framework.

Now the route records a durable ingestion job (the same leased job rows as
repository ingestion; ``GET /knowledge/ingest/jobs/{job_id}`` reports it) and
queues :func:`ingest_legacy_source_task` on the ingestion queue. The worker runs
the REGISTERED CONNECTOR for the source type (the one stack the Sources API
uses) against an ephemeral, never-persisted :class:`SourceConfig`, and every
document goes through the shared :class:`IngestionPipeline` (quota, PII /
RAG_INGEST screening, quality, content-type chunking, metered embedding,
dedup, index). No Source row is created, so nothing re-syncs on a schedule and
the tenant's token is never stored: it travels to the worker vault-encrypted in
the task message and is decrypted only there.

The ephemeral Source id is derived from (tenant, kind, target, collection), so
re-ingesting the same space / project / channel / repository replaces its
documents instead of indexing a second copy.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

from celery import shared_task  # type: ignore[import-not-found]

from app.observability.logging import get_logger

_log = get_logger(__name__)

LEGACY_SOURCE_KINDS: tuple[str, ...] = ("github", "confluence", "jira", "slack")

# Same retry rule as repository jobs (see app.ingestion.repo_tasks).
LEASE_HELD_MAX_RETRIES = 3
# Bounded list of per-document failure reasons kept on the job's message.
_MAX_REPORTED_FAILURES = 3


class LegacySourceJobLeaseHeldError(RuntimeError):
    """Another worker holds this job's live lease; the task retries later."""

    def __init__(self, job_id: str, *, retry_after_seconds: int) -> None:
        self.job_id = job_id
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"ingestion job {job_id} is leased by another worker")


@dataclass(frozen=True)
class LegacySourceRequest:
    """What one legacy route asks for, in connector terms."""

    kind: str
    target: str  # human-readable source label, e.g. "confluence:ENG"
    source_url: str  # the job row's source_url (also hashed into the source id)
    connection_config: dict[str, Any]  # PLAINTEXT — encrypt before queuing
    max_documents: int | None = None  # documents read before stopping (None = connector cap)
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def ephemeral_source_id(tenant_id: str, kind: str, source_url: str, collection_id: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}\x1f{kind}\x1f{source_url}\x1f{collection_id}".encode())
    return f"legacy-{kind}-{digest.hexdigest()[:24]}"


def build_source_config(
    *,
    kind: str,
    tenant_id: str,
    collection_id: str,
    source_url: str,
    connection_config: dict[str, Any],
) -> Any:
    """The never-persisted SourceConfig the connector and pipeline run with."""
    from app.ingestion.source_config import SourceConfig, SourceFamily

    family = {
        "github": SourceFamily.CODE_REPOSITORY,
        "slack": SourceFamily.COMMUNICATION,
    }.get(kind, SourceFamily.DOCUMENT_STORE)
    return SourceConfig(
        source_id=ephemeral_source_id(tenant_id, kind, source_url, collection_id),
        tenant_id=tenant_id,
        name=f"{kind} ingestion",
        family=family,
        source_type=kind,
        connection_config=dict(connection_config),
        sync_mode="full",
        collection_id=collection_id,
    )


def encrypt_secrets(connection_config: dict[str, Any]) -> dict[str, Any]:
    """Vault-encrypt every credential in the config (it rides the broker)."""
    from app.ingestion.source_secrets import encrypt_connection_config

    return encrypt_connection_config(connection_config)


def decrypt_secrets(connection_config: dict[str, Any]) -> dict[str, Any]:
    from app.ingestion.source_secrets import decrypt_connection_config

    plain, _reencrypt = decrypt_connection_config(connection_config)
    return plain


def _public_failure(exc: BaseException, kind: str) -> str:
    """A client-safe reason for the job row (the cause is logged)."""
    from app.ingestion.base_connector import ConnectorPartialFailureError
    from app.ingestion.connector_egress import ConnectorEgressBlockedError

    if isinstance(exc, ConnectorEgressBlockedError):
        return f"{kind}: the source address is not allowed"
    if isinstance(exc, ConnectorPartialFailureError):
        return f"{kind}: parts of the source could not be read"
    return f"{kind}: the source could not be read"


@dataclass
class _Outcome:
    documents: int = 0
    indexed: int = 0
    skipped: int = 0
    failed: int = 0
    chunks: int = 0
    truncated: bool = False
    reasons: list[str] = field(default_factory=list)


async def run_legacy_source_ingest(
    *,
    job_id: str,
    tenant_ctx: Any,
    kind: str,
    collection_id: str,
    source_url: str,
    connection_config: dict[str, Any],
    max_documents: int | None,
    store: Any,
    pipeline: Any,
    lease_seconds: int,
    connector: Any = None,
) -> None:
    """Claim the job, run the connector through the pipeline, record the outcome.

    ``connection_config`` is plaintext here (decrypted by the task).
    """
    lease_owner = uuid.uuid4().hex
    claimed = await store.claim_ingestion_job_async(
        job_id,
        collection_id=collection_id,
        source_url=source_url,
        lease_owner=lease_owner,
        lease_seconds=lease_seconds,
        tenant_ctx=tenant_ctx,
        source_type=kind,
    )
    if not claimed:
        job = await store.get_ingestion_job_async(job_id, tenant_ctx=tenant_ctx)
        status = str((job or {}).get("status") or "")
        if status == "running":
            raise LegacySourceJobLeaseHeldError(job_id, retry_after_seconds=lease_seconds + 1)
        _log.info("legacy_ingest_job_not_claimable", job_id=job_id, status=status or None)
        return

    async def _fail(message: str) -> None:
        try:
            await store.fail_ingestion_job_async(
                job_id, lease_owner=lease_owner, error_message=message, tenant_ctx=tenant_ctx
            )
        except Exception as exc:
            _log.error("legacy_ingest_status_update_failed", job_id=job_id,
                       error=type(exc).__name__)  # fmt: skip

    config = build_source_config(
        kind=kind,
        tenant_id=tenant_ctx.tenant_id,
        collection_id=collection_id,
        source_url=source_url,
        connection_config=connection_config,
    )
    outcome = _Outcome()
    source_error: BaseException | None = None
    try:
        if connector is None:
            from app.ingestion.connector_registry import get_connector, load_all_connectors

            load_all_connectors()
            connector = get_connector(kind)()
        delta = connector.get_delta(config, None)
        try:
            async for raw_doc, _cursor in delta:
                if max_documents is not None and outcome.documents >= max_documents:
                    outcome.truncated = True
                    break
                outcome.documents += 1
                if not await store.heartbeat_ingestion_job_async(
                    job_id, lease_owner=lease_owner, lease_seconds=lease_seconds,
                    tenant_ctx=tenant_ctx,
                ):  # fmt: skip
                    raise RuntimeError("ingestion job lease was lost")
                result = await pipeline.ingest(raw_doc, config)
                if result.status == "indexed":
                    outcome.indexed += 1
                    outcome.chunks += int(result.chunks_created or 0)
                elif result.status == "skipped":
                    outcome.skipped += 1
                else:
                    outcome.failed += 1
                    if len(outcome.reasons) < _MAX_REPORTED_FAILURES:
                        outcome.reasons.append(str(result.error or "failed")[:120])
        finally:
            aclose = getattr(delta, "aclose", None)
            if aclose is not None:
                await aclose()
    except Exception as exc:
        from app.providers.guarded_completion import DecisionBudgetExceededError

        if isinstance(exc, DecisionBudgetExceededError) or str(exc) == (
            "ingestion job lease was lost"
        ):
            _log.warning("legacy_ingest_aborted", job_id=job_id, error=type(exc).__name__)
            await _fail(
                f"{kind}: the tenant's budget is exhausted"
                if isinstance(exc, DecisionBudgetExceededError)
                else f"{kind}: ingestion was interrupted"
            )
            return
        source_error = exc
        _log.warning(
            "legacy_ingest_source_error",
            job_id=job_id,
            kind=kind,
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )

    _log.info(
        "legacy_ingest_finished",
        job_id=job_id,
        kind=kind,
        documents=outcome.documents,
        indexed=outcome.indexed,
        skipped=outcome.skipped,
        failed=outcome.failed,
        chunks=outcome.chunks,
        truncated=outcome.truncated,
    )
    if outcome.indexed == 0 and (source_error is not None or outcome.failed):
        await _fail(
            _public_failure(source_error, kind)
            if source_error is not None
            else f"{kind}: every document failed ({'; '.join(outcome.reasons)})"[:500]
        )
        return
    notes: list[str] = []
    if source_error is not None:
        notes.append(_public_failure(source_error, kind))
    if outcome.failed:
        notes.append(
            f"{outcome.failed} of {outcome.documents} documents failed"
            f" ({'; '.join(outcome.reasons)})"
        )
    if outcome.truncated:
        notes.append(f"stopped at the {max_documents}-document limit")
    if not await store.complete_ingestion_job_async(
        job_id,
        lease_owner=lease_owner,
        chunk_count=outcome.chunks,
        tenant_ctx=tenant_ctx,
        message="; ".join(notes)[:500] or None,
    ):
        _log.warning("legacy_ingest_completion_lost_lease", job_id=job_id)


def _tenant_context(tenant_id: str) -> Any:
    from app.tenancy.context import PlanTier, TenantContext

    return TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="legacy-ingest")


def _worker_services() -> tuple[Any, Any]:
    """(DB-backed KnowledgeStore, IngestionPipeline) — the scheduler's worker wiring."""
    from app.db.session import get_session_factory
    from app.ingestion.pii import build_pii_analyzer
    from app.ingestion.pipeline import IngestionPipeline
    from app.ingestion.quota import IngestionQuotaEnforcer
    from app.ingestion.scheduler import _build_worker_kg_hook
    from app.ingestion.worker_services import build_worker_knowledge_services

    db_factory = get_session_factory()
    store, embedder = build_worker_knowledge_services(db_factory)
    pipeline = IngestionPipeline(
        knowledge_store=store,
        embedder=embedder,
        pii_analyzer=build_pii_analyzer(),
        quota_enforcer=IngestionQuotaEnforcer(db_factory),
        kg_hook=_build_worker_kg_hook(db_factory),
    )
    return store, pipeline


async def _run_task_async(
    *,
    job_id: str,
    tenant_id: str,
    kind: str,
    collection_id: str,
    source_url: str,
    connection_config: dict[str, Any],
    max_documents: int | None = None,
) -> None:
    from app.core.config import get_settings

    store, pipeline = _worker_services()
    await run_legacy_source_ingest(
        job_id=job_id,
        tenant_ctx=_tenant_context(tenant_id),
        kind=kind,
        collection_id=collection_id,
        source_url=source_url,
        connection_config=decrypt_secrets(connection_config),
        max_documents=max_documents,
        store=store,
        pipeline=pipeline,
        lease_seconds=get_settings().repo_ingest_lease_seconds,
    )


@shared_task(name="ingestion.ingest_legacy_source", bind=True, acks_late=True)
def ingest_legacy_source_task(self: Any, **params: Any) -> dict[str, Any]:
    """Celery entry point (acks_late: a crashed worker's job is redelivered and,
    once the dead worker's lease expires, taken over)."""
    from app.db.session import run_in_fresh_loop

    try:
        run_in_fresh_loop(_run_task_async(**params))
    except LegacySourceJobLeaseHeldError as exc:
        retries = int(getattr(self.request, "retries", 0) or 0)
        if retries >= LEASE_HELD_MAX_RETRIES:
            _log.warning("legacy_ingest_lease_still_held_dropping_duplicate", job_id=exc.job_id)
            return {"job_id": params.get("job_id"), "status": "lease_held"}
        raise self.retry(
            exc=exc, countdown=exc.retry_after_seconds, max_retries=LEASE_HELD_MAX_RETRIES
        ) from exc
    return {"job_id": params.get("job_id")}


def enqueue_legacy_source_ingest(
    *,
    job_id: str,
    tenant_id: str,
    collection_id: str,
    request: LegacySourceRequest,
) -> None:
    """Queue one ingestion on the ingestion queue (raises if it can't).

    Credentials are vault-encrypted before they reach the broker.
    """
    ingest_legacy_source_task.apply_async(
        kwargs={
            "job_id": job_id,
            "tenant_id": tenant_id,
            "kind": request.kind,
            "collection_id": collection_id,
            "source_url": request.source_url,
            "connection_config": encrypt_secrets(request.connection_config),
            "max_documents": request.max_documents,
        },
        queue="ingestion",
    )


__all__ = [
    "LEGACY_SOURCE_KINDS",
    "LegacySourceJobLeaseHeldError",
    "LegacySourceRequest",
    "build_source_config",
    "enqueue_legacy_source_ingest",
    "ephemeral_source_id",
    "ingest_legacy_source_task",
    "run_legacy_source_ingest",
]
