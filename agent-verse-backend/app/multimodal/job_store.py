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

The raw ``source_base64`` payload is intentionally never persisted — not to
Redis and not to the process-memory copy — it can be large and is only needed
transiently during extraction, not for status/result lookups after the job
completes.

With Redis wired, Redis is the store: the process-memory map holds a job only
while Redis could not take it (an outage), so it cannot grow without bound or
serve a stale copy over a newer one written by another replica. Without Redis
(tests / the in-memory phase) the memory map is the store, bounded to the most
recent ``_MEMORY_MAX_JOBS`` jobs.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections import OrderedDict
from typing import Any, Protocol

from app.multimodal.models import AssetIngestionJob, ExtractedSpan, Modality

_log = logging.getLogger(__name__)

_KEY_PREFIX = "multimodal:job"
# Retention window for a completed/failed job record in Redis.
_JOB_TTL_SECONDS = 7 * 24 * 3600
# Bound on the process-memory map (the no-Redis store and the Redis-outage
# fallback); the oldest job is evicted first.
_MEMORY_MAX_JOBS = 1000


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

    Without a ``redis`` client jobs live in a bounded process-memory map —
    the path unit tests and the ``create_app()`` in-memory phase use. With a
    ``redis`` client, Redis is the store (jobs survive restarts and are
    visible cross-replica); memory only holds jobs Redis could not take.
    """

    def __init__(self, redis: _AsyncRedisLike | None = None) -> None:
        self._redis = redis
        self._memory: OrderedDict[tuple[str, str], AssetIngestionJob] = OrderedDict()

    def is_persistent(self) -> bool:
        """True when a real (non-in-memory-only) backing store is wired."""
        return self._redis is not None

    def _remember(self, job: AssetIngestionJob) -> None:
        """Keep a copy (never the raw asset bytes) in the bounded memory map."""
        key = (job.tenant_id, job.job_id)
        self._memory[key] = dataclasses.replace(job, source_base64=None)
        self._memory.move_to_end(key)
        while len(self._memory) > _MEMORY_MAX_JOBS:
            self._memory.popitem(last=False)

    async def save(self, job: AssetIngestionJob) -> None:
        key = (job.tenant_id, job.job_id)
        if self._redis is None:
            self._remember(job)
            return
        try:
            await self._redis.set(
                _job_key(job.tenant_id, job.job_id),
                json.dumps(_job_to_payload(job)),
                ex=_JOB_TTL_SECONDS,
            )
        except Exception as exc:
            # Persistence is best-effort: a Redis outage must not fail the
            # ingestion request itself. A memory copy answers reads on this
            # process until Redis takes the job.
            _log.warning("asset_job_persist_failed job_id=%s: %s", job.job_id, exc)
            self._remember(job)
            return
        # Redis has it: drop any outage-era copy so it cannot shadow Redis.
        self._memory.pop(key, None)

    async def get(self, job_id: str, tenant_id: str) -> AssetIngestionJob | None:
        key = (tenant_id, job_id)
        if self._redis is None:
            return self._memory.get(key)
        try:
            raw = await self._redis.get(_job_key(tenant_id, job_id))
        except Exception as exc:
            _log.warning("asset_job_fetch_failed job_id=%s: %s", job_id, exc)
            return self._memory.get(key)
        if not raw:
            return self._memory.get(key)
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode()
        try:
            return _payload_to_job(json.loads(raw))
        except Exception as exc:
            _log.warning("asset_job_decode_failed job_id=%s: %s", job_id, exc)
            return self._memory.get(key)

    async def delete(self, job_id: str, tenant_id: str) -> None:
        self._memory.pop((tenant_id, job_id), None)
        if self._redis is None:
            return
        try:
            await self._redis.delete(_job_key(tenant_id, job_id))
        except Exception as exc:
            _log.warning("asset_job_delete_failed job_id=%s: %s", job_id, exc)
