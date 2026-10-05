"""NF-17: finished training-export files expire from REAL object storage (MinIO).

Before: a finished export's JSONL stayed in the bucket forever. Now a beat task
deletes the objects of jobs completed more than ``training_export_retention_hours``
ago, in bounded batches, and marks those jobs ``expired`` — only after their
object is really gone.

Postgres + MinIO testcontainers (the local ``minio/minio`` image; no pull).
"""

from __future__ import annotations

import asyncio
import io
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.training_export import jobs

pytestmark = pytest.mark.integration

_KEY = "nf17minio"
_SECRET = "nf17minio-secret"
_BUCKET = "nf17-exports"


@pytest.fixture(scope="module")
def minio_store() -> Iterator[jobs.S3ObjectStore]:
    import boto3
    from testcontainers.core.container import DockerContainer

    container = (
        DockerContainer("minio/minio:latest")
        .with_command("server /data")
        .with_env("MINIO_ROOT_USER", _KEY)
        .with_env("MINIO_ROOT_PASSWORD", _SECRET)
        .with_exposed_ports(9000)
    )
    try:
        container.start()
    except Exception as exc:  # pragma: no cover - Docker down
        pytest.skip(f"could not start a MinIO testcontainer: {exc}")
    try:
        endpoint = f"http://localhost:{container.get_exposed_port(9000)}"
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=_KEY,
            aws_secret_access_key=_SECRET,
            region_name="us-east-1",
        )
        for _ in range(60):
            try:
                s3.create_bucket(Bucket=_BUCKET)
                break
            except Exception:
                time.sleep(0.5)
        yield jobs.S3ObjectStore(
            endpoint_url=endpoint, access_key=_KEY, secret_key=_SECRET, bucket=_BUCKET
        )
    finally:
        container.stop()


async def _seed(
    pg_url: str, store: jobs.S3ObjectStore, specs: list[tuple[str, str, str, float, bool]]
) -> dict[str, str]:
    """specs: (name, tenant, status, completed_hours_ago, upload_object)."""
    engine = create_async_engine(pg_url, poolclass=NullPool)
    ids: dict[str, str] = {}
    try:
        async with engine.begin() as conn:
            for name, tenant, status, hours_ago, upload in specs:
                job_id = uuid.uuid4().hex
                key = jobs.object_key(tenant, job_id) if status == "complete" else None
                await conn.execute(
                    text(
                        "INSERT INTO training_export_jobs (id, tenant_id, status, "
                        "output_format, min_score, row_limit, example_count, object_key, "
                        "created_at, completed_at) VALUES (:id, :t, :st, 'openai', 0.8, 10, "
                        "1, :key, now() - make_interval(hours => :h + 1), "
                        "now() - make_interval(hours => :h))"
                    ),
                    {"id": job_id, "t": tenant, "st": status, "key": key, "h": hours_ago},
                )
                if key and upload:
                    await store.upload(key, io.BytesIO(b'{"messages": []}\n'))
                ids[name] = job_id
    finally:
        await engine.dispose()
    return ids


async def _rows(pg_url: str, ids: dict[str, str]) -> dict[str, tuple[str, str | None]]:
    engine = create_async_engine(pg_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT id, status, object_key FROM training_export_jobs "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": list(ids.values())},
                )
            ).fetchall()
    finally:
        await engine.dispose()
    by_id = {r[0]: (r[1], r[2]) for r in rows}
    return {name: by_id[jid] for name, jid in ids.items()}


def _exists(store: jobs.S3ObjectStore, key: str) -> bool:
    try:
        store._client().head_object(Bucket=store.bucket, Key=key)
        return True
    except Exception:
        return False


def _run(pg_url: str, store: Any, **kw: Any) -> dict[str, Any]:
    async def _go() -> dict[str, Any]:
        engine = create_async_engine(pg_url, poolclass=NullPool)
        try:
            return await jobs.expire_finished_exports(
                async_sessionmaker(engine, expire_on_commit=False), store, **kw
            )
        finally:
            await engine.dispose()

    return asyncio.run(_go())


def test_expired_exports_are_deleted_from_minio_in_bounded_batches(
    pg_url: str, minio_store: jobs.S3ObjectStore
) -> None:
    ids = asyncio.run(
        _seed(
            pg_url,
            minio_store,
            [
                ("old_a", "t-nf17-a", "complete", 240, True),
                ("old_b", "t-nf17-b", "complete", 200, True),
                ("old_gone", "t-nf17-a", "complete", 300, False),  # object already missing
                ("fresh", "t-nf17-a", "complete", 1, True),
                ("old_failed", "t-nf17-b", "failed", 400, False),
            ],
        )
    )
    keys = {n: jobs.object_key(t, ids[n]) for n, t in
            (("old_a", "t-nf17-a"), ("old_b", "t-nf17-b"), ("fresh", "t-nf17-a"))}
    assert all(_exists(minio_store, k) for k in keys.values())

    out = _run(pg_url, minio_store, retention_hours=168, batch_size=2, max_batches=10)

    assert out["expired"] == 3
    assert out["delete_failed"] == 0
    assert out["batches"] >= 2  # bounded: 3 expired rows in batches of 2
    rows = asyncio.run(_rows(pg_url, ids))
    for name in ("old_a", "old_b", "old_gone"):
        assert rows[name] == ("expired", None), name
    assert rows["fresh"][0] == "complete" and rows["fresh"][1] == keys["fresh"]
    assert rows["old_failed"][0] == "failed"
    assert not _exists(minio_store, keys["old_a"])
    assert not _exists(minio_store, keys["old_b"])
    assert _exists(minio_store, keys["fresh"])

    # A second run (another replica / the next tick) finds nothing to do.
    again = _run(pg_url, minio_store, retention_hours=168, batch_size=2, max_batches=10)
    assert again["expired"] == 0


class _FailingDeletes:
    """MinIO store whose deletes fail for one key (e.g. a permission problem)."""

    def __init__(self, inner: jobs.S3ObjectStore, poison: str) -> None:
        self._inner = inner
        self._poison = poison

    async def delete_objects(self, keys: list[str]) -> set[str]:
        deleted = await self._inner.delete_objects([k for k in keys if k != self._poison])
        return deleted


def test_a_job_whose_object_cannot_be_deleted_stays_complete(
    pg_url: str, minio_store: jobs.S3ObjectStore
) -> None:
    ids = asyncio.run(
        _seed(
            pg_url,
            minio_store,
            [
                ("poison", "t-nf17-c", "complete", 500, True),
                ("ok", "t-nf17-c", "complete", 499, True),
            ],
        )
    )
    poison_key = jobs.object_key("t-nf17-c", ids["poison"])
    out = _run(
        pg_url,
        _FailingDeletes(minio_store, poison_key),
        retention_hours=168,
        batch_size=1,
        max_batches=10,
    )

    rows = asyncio.run(_rows(pg_url, ids))
    assert rows["poison"] == ("complete", poison_key)  # never marked expired
    assert _exists(minio_store, poison_key)
    assert rows["ok"] == ("expired", None)  # not blocked behind the poison row
    assert out["delete_failed"] == 1
    assert out["expired"] >= 1
