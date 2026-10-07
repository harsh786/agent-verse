"""BUG A, worker path: the Celery sync decrypts the Source's stored secrets before use.

``source_configs.connection_config`` stores secrets as ``enc:v1:<fernet>``. The
worker (``_sync_source_async``) reads the Source through the same
``SourceConfigStore`` the API uses; these tests drive a real sync through that
store's decrypting read and a real S3 client (answered in-process, ambient chain /
IMDS trapped — see test_s3_no_ambient_credentials) and prove:

* the S3 client gets the PLAINTEXT tenant keys (never ``enc:v1:`` / blank / the mask);
* a worker whose vault key differs from the API's fails the job with a clear
  reason and sends nothing — it used to blank the credentials and fall back to
  botocore's default chain, i.e. the instance metadata service.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ingestion.connectors.s3_connector import S3Connector
from app.ingestion.job_tracker import IngestionJobTracker
from app.ingestion.pipeline import PipelineResult
from app.ingestion.scheduler import _sync_source_async
from app.ingestion.source_secrets import ENC_PREFIX, encrypt_connection_config
from app.ingestion.source_store import SourceConfigStore
from tests.ingestion.test_s3_no_ambient_credentials import (  # noqa: F401 - fixture
    TENANT_KEY,
    TENANT_SECRET,
    _Endpoint,
    endpoint,
)

API_VAULT_KEY = "api-vault-key-for-worker-secret-test"
OTHER_VAULT_KEY = "a-different-vault-key-on-one-worker"


class _Result:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self._row = row

    def mappings(self) -> _Result:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._row


class _Session:
    """Just enough AsyncSession for ``SourceConfigStore.get`` (RLS GUC + one SELECT)."""

    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    def begin(self) -> _Session:
        return self

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        sql = str(statement)
        if "FROM source_configs" in sql:
            assert params == {"id": "src-1", "tid": "t1"}  # explicit tenant predicate
            return _Result(self._row)
        return _Result(None)


def _stored_row(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The source_configs row exactly as the API persisted it (secrets sealed)."""
    monkeypatch.setenv("VAULT_MASTER_KEY", API_VAULT_KEY)
    cc = encrypt_connection_config(
        {
            "bucket": "b",
            "credentials": {"access_key_id": TENANT_KEY, "secret_access_key": TENANT_SECRET},
        }
    )
    assert str(cc["credentials"]).startswith(ENC_PREFIX)  # sealed at rest
    return {
        "id": "src-1",
        "tenant_id": "t1",
        "name": "bucket",
        "family": "object_storage",
        "source_type": "s3",
        "enabled": True,
        "collection_id": "c",
        "config_status": "ok",
        "consecutive_failures": 0,
        "connection_config": cc,
    }


async def _sync(row: dict[str, Any]) -> tuple[dict[str, Any], IngestionJobTracker, list[Any]]:
    real_store = SourceConfigStore(db=lambda: _Session(row))
    store = AsyncMock()
    store.get = real_store.get  # the worker's real, decrypting read
    tracker = IngestionJobTracker()
    job_id = await tracker.acquire_lock("src-1", "t1")
    pipeline = AsyncMock()

    async def _ingest(raw: Any, config: Any) -> PipelineResult:
        return PipelineResult(doc_id=raw.doc_id, source_id="src-1", tenant_id="t1",
                              status="indexed")

    pipeline.ingest = _ingest
    import app.ingestion.connectors.s3_connector as s3_mod

    keys_seen: list[Any] = []
    real_tenant_client = s3_mod.tenant_client

    def _spy(*args: Any, **kwargs: Any) -> Any:
        keys_seen.append(kwargs.get("keys"))
        return real_tenant_client(*args, **kwargs)

    with (
        patch("app.ingestion.scheduler._build_worker_ingestion",
              return_value=(tracker, pipeline, store)),
        patch("app.ingestion.connector_registry.load_all_connectors"),
        patch("app.ingestion.connector_registry.get_connector", return_value=S3Connector),
        patch("app.ingestion.scheduler._schedule_reconcile_if_due", AsyncMock()),
        patch("app.providers.tenant_vault.ensure_tenant_vault", AsyncMock(return_value=None)),
        patch.object(s3_mod, "tenant_client", _spy),
    ):
        result = await _sync_source_async(task=MagicMock(), source_id="src-1", tenant_id="t1",
                                          triggered_by="manual", job_id=job_id)
    return result, tracker, keys_seen


async def test_worker_sync_decrypts_the_stored_credentials_before_building_the_client(
    monkeypatch: pytest.MonkeyPatch, endpoint: _Endpoint  # noqa: F811
) -> None:
    row = _stored_row(monkeypatch)  # the worker has the API's vault key
    result, tracker, keys_seen = await _sync(row)

    assert "error" not in result, result
    assert keys_seen and all(k is not None for k in keys_seen)
    for keys in keys_seen:
        assert keys.access_key_id == TENANT_KEY
        assert keys.secret_access_key == TENANT_SECRET  # plaintext, not enc:v1:
    assert {op for op, _u, _a in endpoint.requests} == {"ListObjectsV2", "GetObject"}
    assert all(f"Credential={TENANT_KEY}/" in str(a) for _o, _u, a in endpoint.requests)
    jobs = tracker.list_jobs_for_source("src-1")
    assert jobs and jobs[-1].docs_discovered == 1


async def test_worker_on_another_vault_key_fails_the_job_clearly_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, endpoint: _Endpoint  # noqa: F811
) -> None:
    row = _stored_row(monkeypatch)
    monkeypatch.setenv("VAULT_MASTER_KEY", OTHER_VAULT_KEY)  # this worker's key differs
    result, tracker, keys_seen = await _sync(row)

    assert "could not be decrypted" in str(result.get("error")), result
    assert "VAULT_MASTER_KEY" in str(result["error"])
    assert keys_seen == []  # no client was built: not anonymous, not ambient
    assert endpoint.requests == []
    jobs = tracker.list_jobs_for_source("src-1")
    assert jobs and jobs[-1].status == "failed"
    assert "VAULT_MASTER_KEY" in str(jobs[-1].error_message)
