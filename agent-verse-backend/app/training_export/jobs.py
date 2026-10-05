"""Durable training-data export jobs (OPS-37).

A large export is a ``training_export_jobs`` row (tenant-scoped, FORCE RLS) run
by a Celery worker: the worker streams examples (``stream.iter_training_examples``)
into a spooled temp file (memory-bounded; spills to disk) and uploads it to S3
compatible object storage under ``tenants/<tenant>/training-exports/<job>.jsonl``.
The API serves status and streams the finished object back to the owning tenant.

State lives only in Postgres and object storage, so any replica can answer for
any job. Every failure is recorded on the row (``status='failed'`` + error) —
a job is never left looking queued after it died.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import tempfile
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.db.rls import sqlalchemy_rls_context
from app.training_export.stream import formatter, iter_training_examples

_log = logging.getLogger(__name__)

_SPOOL_BYTES = 8 * 1024 * 1024
_READ_CHUNK = 1024 * 1024
_JOB_COLUMNS = (
    "id, tenant_id, status, output_format, min_score, row_limit, example_count, "
    "object_key, error, created_at, started_at, completed_at"
)
MAX_LISTED_JOBS = 50
# Active (queued/running) jobs a tenant may hold at once; more is a 429.
MAX_ACTIVE_JOBS_PER_TENANT = 3
# A queued job older than this no longer counts toward the cap (its broker
# message may be gone) — the tenant is never locked out by a lost message.
ACTIVE_QUEUED_WINDOW_HOURS = 24
# The worker refreshes heartbeat_at this often while it streams; a running job
# whose heartbeat is older than STALE_HEARTBEAT_SECONDS belongs to a dead worker
# and may be re-claimed by a redelivered task.
HEARTBEAT_SECONDS = 60.0
STALE_HEARTBEAT_SECONDS = 300


class TrainingExportUnavailableError(RuntimeError):
    """The job store or object storage could not be used (callers answer 503)."""


class TooManyActiveExportJobsError(RuntimeError):
    """The tenant already holds MAX_ACTIVE_JOBS_PER_TENANT active jobs (429)."""


class ExportClaimLostError(RuntimeError):
    """Another worker re-claimed the job; this worker must stop writing to it."""


# ── object storage ────────────────────────────────────────────────────────────


class S3ObjectStore:
    """Minimal S3/MinIO client (boto3 in a worker thread). Raises on any failure."""

    def __init__(self, *, endpoint_url: str, access_key: str, secret_key: str, bucket: str) -> None:
        self._endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self.bucket = bucket

    def _client(self) -> Any:
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name="us-east-1",
        )

    def _upload(self, key: str, fileobj: Any) -> None:
        client = self._client()
        try:
            client.head_bucket(Bucket=self.bucket)
        except Exception:
            client.create_bucket(Bucket=self.bucket)
        client.upload_fileobj(
            fileobj, self.bucket, key, ExtraArgs={"ContentType": "application/x-ndjson"}
        )

    async def upload(self, key: str, fileobj: Any) -> None:
        await asyncio.to_thread(self._upload, key, fileobj)

    async def open_stream(self, key: str) -> Any:
        def _get() -> Any:
            return self._client().get_object(Bucket=self.bucket, Key=key)["Body"]

        return await asyncio.to_thread(_get)


def object_store_from_env() -> S3ObjectStore | None:
    """The configured object store, or None when object storage is not configured.

    No silent local-disk fallback: a replica-local file would be unreachable from
    every other replica.
    """
    endpoint = os.getenv("TRAINING_EXPORT_S3_ENDPOINT") or os.getenv("MINIO_ENDPOINT")
    access = os.getenv("TRAINING_EXPORT_S3_ACCESS_KEY") or os.getenv("MINIO_ACCESS_KEY")
    secret = os.getenv("TRAINING_EXPORT_S3_SECRET_KEY") or os.getenv("MINIO_SECRET_KEY")
    if not (endpoint and access and secret):
        return None
    bucket = os.getenv("TRAINING_EXPORT_BUCKET", "agentverse-training-exports")
    return S3ObjectStore(endpoint_url=endpoint, access_key=access, secret_key=secret, bucket=bucket)


def object_key(tenant_id: str, job_id: str) -> str:
    return f"tenants/{tenant_id}/training-exports/{job_id}.jsonl"


async def iter_object(store: Any, key: str) -> AsyncIterator[bytes]:
    body = await store.open_stream(key)
    try:
        while True:
            chunk = await asyncio.to_thread(body.read, _READ_CHUNK)
            if not chunk:
                return
            yield chunk
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            close()


# ── job store ─────────────────────────────────────────────────────────────────


def _job_dict(row: Any) -> dict[str, Any]:
    (
        job_id,
        tenant_id,
        status,
        output_format,
        min_score,
        row_limit,
        example_count,
        key,
        error,
        created_at,
        started_at,
        completed_at,
    ) = tuple(row)

    def _iso(v: Any) -> str | None:
        return v.isoformat() if isinstance(v, datetime.datetime) else (str(v) if v else None)

    return {
        "job_id": job_id,
        "tenant_id": tenant_id,
        "status": status,
        "format": output_format,
        "min_score": float(min_score) if min_score is not None else None,
        "limit": row_limit,
        "example_count": example_count,
        "has_file": bool(key) and status == "complete",
        "error": error,
        "created_at": _iso(created_at),
        "started_at": _iso(started_at),
        "completed_at": _iso(completed_at),
    }


async def create_job(
    db: Any, tenant_id: str, output_format: str, min_score: float, limit: int
) -> dict[str, Any]:
    """Insert a queued job unless the tenant already holds too many active ones.

    The cap check and the insert run under a per-tenant transaction-scoped
    advisory lock, so concurrent creates on any replica cannot overshoot it.
    """
    from sqlalchemy import text

    job_id = uuid.uuid4().hex
    try:
        async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
                {"lock_key": f"training_export_jobs:{tenant_id}"},
            )
            row = (
                await session.execute(
                    text(
                        "INSERT INTO training_export_jobs (id, tenant_id, status, "
                        "output_format, min_score, row_limit, created_at) "
                        "SELECT CAST(:id AS varchar), CAST(:tid AS varchar), 'queued', "
                        "CAST(:fmt AS varchar), CAST(:min_score AS double precision), "
                        "CAST(:lim AS integer), NOW() "
                        "WHERE (SELECT COUNT(*) FROM training_export_jobs "
                        " WHERE tenant_id = :tid AND ("
                        "  (status = 'queued' AND created_at > NOW() - make_interval(hours => :qh))"
                        "  OR (status = 'running' AND heartbeat_at > "
                        "      NOW() - make_interval(secs => :stale)))) < :cap "
                        f"RETURNING {_JOB_COLUMNS}"
                    ),
                    {
                        "id": job_id,
                        "tid": tenant_id,
                        "fmt": output_format,
                        "min_score": min_score,
                        "lim": limit,
                        "qh": ACTIVE_QUEUED_WINDOW_HOURS,
                        "stale": STALE_HEARTBEAT_SECONDS,
                        "cap": MAX_ACTIVE_JOBS_PER_TENANT,
                    },
                )
            ).fetchone()
    except Exception as exc:
        raise TrainingExportUnavailableError(str(exc)) from exc
    if row is None:
        raise TooManyActiveExportJobsError(
            f"at most {MAX_ACTIVE_JOBS_PER_TENANT} export jobs may be active at once"
        )
    return _job_dict(row)


async def get_job(db: Any, tenant_id: str, job_id: str) -> dict[str, Any] | None:
    row = await _get_row(db, tenant_id, job_id)
    return _job_dict(row) if row is not None else None


async def _get_row(db: Any, tenant_id: str, job_id: str) -> Any:
    from sqlalchemy import text

    try:
        async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
            return (
                await session.execute(
                    text(
                        f"SELECT {_JOB_COLUMNS} FROM training_export_jobs "
                        "WHERE id = :id AND tenant_id = :tid"
                    ),
                    {"id": job_id, "tid": tenant_id},
                )
            ).fetchone()
    except Exception as exc:
        raise TrainingExportUnavailableError(str(exc)) from exc


async def get_job_object_key(db: Any, tenant_id: str, job_id: str) -> tuple[str, str] | None:
    """(status, object_key) for the tenant's job, or None when absent."""
    row = await _get_row(db, tenant_id, job_id)
    if row is None:
        return None
    return str(row[2]), str(row[7] or "")


async def list_jobs(db: Any, tenant_id: str) -> list[dict[str, Any]]:
    from sqlalchemy import text

    try:
        async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
            rows = (
                await session.execute(
                    text(
                        f"SELECT {_JOB_COLUMNS} FROM training_export_jobs "
                        "WHERE tenant_id = :tid ORDER BY created_at DESC, id DESC LIMIT :lim"
                    ),
                    {"tid": tenant_id, "lim": MAX_LISTED_JOBS},
                )
            ).fetchall()
    except Exception as exc:
        raise TrainingExportUnavailableError(str(exc)) from exc
    return [_job_dict(r) for r in rows]


async def _update(
    db: Any,
    tenant_id: str,
    job_id: str,
    sql: str,
    params: dict[str, Any],
) -> bool:
    """Run a tenant-scoped UPDATE (``sql`` may extend the WHERE); True if a row changed."""
    from sqlalchemy import text

    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        row = (
            await session.execute(
                text(f"UPDATE training_export_jobs SET {sql} RETURNING id"),
                {**params, "id": job_id, "tid": tenant_id},
            )
        ).fetchone()
        return row is not None


async def mark_failed(
    db: Any, tenant_id: str, job_id: str, error: str, *, claim_token: str | None = None
) -> bool:
    """Record the job failed.

    With ``claim_token`` (the worker) only the claim holder may fail it; without
    (the API, after an enqueue failure) only a job no worker has claimed.
    """
    fence = "claim_token = :tok AND status = 'running'" if claim_token else "status = 'queued'"
    params: dict[str, Any] = {"err": error[:500]}
    if claim_token:
        params["tok"] = claim_token
    return await _update(
        db,
        tenant_id,
        job_id,
        "status = 'failed', error = :err, completed_at = NOW() "
        f"WHERE id = :id AND tenant_id = :tid AND {fence}",
        params,
    )


# ── runner (Celery worker) ────────────────────────────────────────────────────


async def run_export_job(db: Any, store: Any, job_id: str, tenant_id: str) -> dict[str, Any]:
    """Stream the job's examples to object storage and complete the row.

    Claims the job atomically and receives a fencing token; the heartbeat,
    completion and failure writes all require that token, so a worker whose
    claim was taken over (it stalled past STALE_HEARTBEAT_SECONDS) stops with
    ExportClaimLostError instead of overwriting the new owner's row.

    Returns ``busy`` when a live worker holds the job (the task retries later),
    ``skipped`` when it is already finished or gone. Any other failure marks the
    job failed and re-raises.
    """
    token = await _claim(db, tenant_id, job_id)
    row = await _get_row(db, tenant_id, job_id)
    if token is None:
        status = str(row[2]) if row is not None else "missing"
        return {"job_id": job_id, "status": "busy" if status == "running" else "skipped"}
    job = _job_dict(row)
    try:
        if store is None:
            raise TrainingExportUnavailableError("object storage is not configured")
        to_line = formatter(str(job["format"]))
        count = 0
        last_beat = time.monotonic()
        with tempfile.SpooledTemporaryFile(max_size=_SPOOL_BYTES, mode="w+b") as buf:
            async for example in iter_training_examples(
                db, tenant_id, float(job["min_score"] or 0.0), int(job["limit"] or 0)
            ):
                buf.write(json.dumps(to_line(example)).encode())
                buf.write(b"\n")
                count += 1
                if time.monotonic() - last_beat >= HEARTBEAT_SECONDS:
                    await _heartbeat(db, tenant_id, job_id, token)
                    last_beat = time.monotonic()
            await _heartbeat(db, tenant_id, job_id, token)
            buf.seek(0)
            key = object_key(tenant_id, job_id)
            await store.upload(key, buf)
        finished = await _update(
            db,
            tenant_id,
            job_id,
            "status = 'complete', example_count = :n, object_key = :key, "
            "completed_at = NOW(), error = NULL "
            "WHERE id = :id AND tenant_id = :tid AND claim_token = :tok "
            "AND status = 'running'",
            {"n": count, "key": key, "tok": token},
        )
        if not finished:
            raise ExportClaimLostError(f"job {job_id} was re-claimed before it completed")
    except ExportClaimLostError:
        _log.warning("training_export_claim_lost job=%s", job_id)
        raise
    except Exception as exc:
        try:
            await mark_failed(
                db, tenant_id, job_id, f"{type(exc).__name__}: {exc}", claim_token=token
            )
        except Exception as mark_exc:
            # The heartbeat goes stale; a redelivered task re-claims the job.
            _log.error("training_export_mark_failed_failed job=%s: %s", job_id, mark_exc)
        raise
    return {"job_id": job_id, "status": "complete", "example_count": count}


async def _heartbeat(db: Any, tenant_id: str, job_id: str, token: str) -> None:
    alive = await _update(
        db,
        tenant_id,
        job_id,
        "heartbeat_at = NOW() "
        "WHERE id = :id AND tenant_id = :tid AND claim_token = :tok AND status = 'running'",
        {"tok": token},
    )
    if not alive:
        raise ExportClaimLostError(f"job {job_id} was re-claimed by another worker")


async def _claim(db: Any, tenant_id: str, job_id: str) -> str | None:
    """Atomically claim the job; the fencing token on success, else None.

    A queued job is claimed; so is a ``running`` job whose heartbeat is older
    than STALE_HEARTBEAT_SECONDS (its worker died — Celery redelivers it via
    acks_late + reject_on_worker_lost, and it must not stay running forever).
    """
    token = uuid.uuid4().hex
    claimed = await _update(
        db,
        tenant_id,
        job_id,
        "status = 'running', started_at = NOW(), heartbeat_at = NOW(), "
        "claim_token = :tok, error = NULL "
        "WHERE id = :id AND tenant_id = :tid AND (status = 'queued' OR "
        "(status = 'running' AND (heartbeat_at IS NULL OR "
        "heartbeat_at < NOW() - make_interval(secs => :stale))))",
        {"tok": token, "stale": STALE_HEARTBEAT_SECONDS},
    )
    return token if claimed else None
