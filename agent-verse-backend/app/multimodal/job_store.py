"""Persistent store for AssetIngestionJob records (D-23).

Before this module existed, ``MultimodalPipeline`` kept ingestion jobs in a
plain ``dict`` on the pipeline instance. That dict is lost on every process
restart and is never shared across replicas, so a job created on one pod is
invisible to a request served by another, and a redeploy silently drops every
in-flight/completed job.

``AssetJobStore`` follows the same "in-memory by default, Redis-backed in
production" pattern already used by ``app.governance.cost.CostController`` /
``RedisCostController`` and ``app.reliability.circuit_breaker`` /
``RedisCircuitBreaker``: it is constructed with no Redis client during the
in-memory phase of ``create_app()`` (tests get this path), then upgraded with
a real client in the FastAPI ``lifespan`` when one is available — the
two-phase wiring pattern used throughout ``app/main.py``.

The raw ``source_base64`` payload is intentionally never persisted to Redis —
it can be large and is only needed transiently during extraction, not for
status/result lookups after the job completes.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from typing import Any, Protocol

from app.multimodal.models import AssetIngestionJob, ExtractedSpan, Modality

_log = logging.getLogger(__name__)

_KEY_PREFIX = "multimodal:job"
# Retention window for a completed/failed job record in Redis.
_JOB_TTL_SECONDS = 7 * 24 * 3600


class _AsyncRedisLike(Protocol):
    """Structural protocol for the subset of redis-py's async client we use."""

    async def get(self, key: str) -> Any: ...

    async def set(self, key: str, value: str, ex: int | None = None) -> Any: ...

    async def delete(self, key: str) -> Any: ...


def _job_key(tenant_id: str, job_id: str) -> str:
    return f"{_KEY_PREFIX}:{tenant_id}:{job_id}"


def _job_to_payload(job: AssetIngestionJob) -> dict[str, Any]:
    payload = dataclasses.asdict(job)
    # Never persist the raw asset bytes -- see module docstring.
    payload.pop("source_base64", None)
    return payload


def _payload_to_job(payload: dict[str, Any]) -> AssetIngestionJob:
    spans = [
        ExtractedSpan(**{**span, "modality": Modality(span["modality"])})
        for span in payload.get("spans", [])
    ]
    return AssetIngestionJob(
        job_id=payload["job_id"],
        tenant_id=payload["tenant_id"],
        asset_type=Modality(payload["asset_type"]),
        source_uri=payload.get("source_uri"),
        source_base64=None,
        filename=payload.get("filename"),
        collection_id=payload.get("collection_id"),
        status=payload.get("status", "pending"),
        spans=spans,
        error=payload.get("error"),
        created_at=payload.get("created_at"),
        completed_at=payload.get("completed_at"),
        metadata=payload.get("metadata", {}),
    )


class AssetJobStore:
    """Tenant-scoped persistence for multimodal asset ingestion jobs.

    Without a ``redis`` client this behaves exactly like the old private
    dict (in-memory only, scoped to this process) — that is the path unit
    tests and the ``create_app()`` in-memory phase use. With a ``redis``
    client, every ``save`` mirrors to Redis and every cache-miss ``get``
    falls back to it, so jobs survive restarts and are visible cross-replica.
    """

    def __init__(self, redis: _AsyncRedisLike | None = None) -> None:
        self._redis = redis
        self._memory: dict[tuple[str, str], AssetIngestionJob] = {}

    def is_persistent(self) -> bool:
        """True when a real (non-in-memory-only) backing store is wired."""
        return self._redis is not None

    async def save(self, job: AssetIngestionJob) -> None:
        self._memory[(job.tenant_id, job.job_id)] = job
        if self._redis is None:
            return
        try:
            await self._redis.set(
                _job_key(job.tenant_id, job.job_id),
                json.dumps(_job_to_payload(job)),
                ex=_JOB_TTL_SECONDS,
            )
        except Exception as exc:
            # Persistence is best-effort: a Redis outage must not fail the
            # ingestion request itself. The in-memory copy above still
            # answers reads on this process for the lifetime of the job.
            _log.warning("asset_job_persist_failed job_id=%s: %s", job.job_id, exc)

    async def get(self, job_id: str, tenant_id: str) -> AssetIngestionJob | None:
        cached = self._memory.get((tenant_id, job_id))
        if cached is not None:
            return cached
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(_job_key(tenant_id, job_id))
        except Exception as exc:
            _log.warning("asset_job_fetch_failed job_id=%s: %s", job_id, exc)
            return None
        if not raw:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode()
        try:
            job = _payload_to_job(json.loads(raw))
        except Exception as exc:
            _log.warning("asset_job_decode_failed job_id=%s: %s", job_id, exc)
            return None
        self._memory[(tenant_id, job_id)] = job
        return job

    async def delete(self, job_id: str, tenant_id: str) -> None:
        self._memory.pop((tenant_id, job_id), None)
        if self._redis is None:
            return
        try:
            await self._redis.delete(_job_key(tenant_id, job_id))
        except Exception as exc:
            _log.warning("asset_job_delete_failed job_id=%s: %s", job_id, exc)
