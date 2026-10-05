"""Connector feature flags are enforced even when callers pass no settings.

Regression: the scheduler, sources API and Celery sync all called
``get_connector(source_type)`` without ``settings``, so LAW-20 feature flags
were never checked. DuckDB (tenant SQL executed in-process on the host) is now
off by default and must be enabled explicitly.
"""

from __future__ import annotations

import importlib

import pytest

from app.core.config import get_settings
from app.ingestion.connector_registry import get_connector


@pytest.fixture(autouse=True)
def _load_duckdb() -> None:
    importlib.import_module("app.ingestion.connectors.duckdb_connector")


def test_duckdb_disabled_by_default_without_explicit_settings() -> None:
    assert get_settings().ingestion_connector_duckdb_enabled is False
    with pytest.raises(RuntimeError, match="disabled by feature flag"):
        get_connector("duckdb")


def test_duckdb_enabled_when_operator_turns_it_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "ingestion_connector_duckdb_enabled", True)
    assert get_connector("duckdb").__name__ == "DuckDBConnector"


# ── TG-15: the MongoDB connector's kill switch ───────────────────────────────


@pytest.fixture
def _mongodb_off(monkeypatch: pytest.MonkeyPatch) -> None:
    importlib.import_module("app.ingestion.connectors.mongodb_connector")
    monkeypatch.setattr(get_settings(), "ingestion_connector_mongodb_enabled", False)


def test_the_mongodb_flag_is_a_declared_setting_enabled_by_default() -> None:
    from app.core.config import Settings

    # It used to name a field Settings did not declare (extra="ignore"), so
    # INGESTION_CONNECTOR_MONGODB_ENABLED=false was silently ignored.
    assert "ingestion_connector_mongodb_enabled" in Settings.model_fields
    assert Settings().ingestion_connector_mongodb_enabled is True
    assert Settings(ingestion_connector_mongodb_enabled=False).ingestion_connector_mongodb_enabled is False


def test_the_env_var_turns_mongodb_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings

    monkeypatch.setenv("INGESTION_CONNECTOR_MONGODB_ENABLED", "false")
    assert Settings().ingestion_connector_mongodb_enabled is False


@pytest.mark.usefixtures("_mongodb_off")
def test_mongodb_off_refuses_create_validate_sync_and_health() -> None:
    from tests.api.test_ingestion_api import _auth, _client

    client = _client()
    payload = {
        "name": "orders",
        "family": "nosql_database",
        "source_type": "mongodb",
        "connection_config": {"uri": "mongodb://db.example.com/", "database": "shop"},
        "collection_id": "kb-1",
    }
    created = client.post("/sources", headers=_auth(), json=payload)
    assert created.status_code == 422, created.text
    assert "ingestion_connector_mongodb_enabled" in created.json()["detail"]

    validated = client.post(
        "/sources/validate?check_connection=false", headers=_auth(), json=payload
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["valid"] is False
    assert any("disabled" in e for e in validated.json()["errors"])

    # A Source created while the connector was on: sync and health refuse it.
    import app.api.ingestion as ingestion_mod
    from app.ingestion.source_config import SourceConfig

    existing = SourceConfig(
        source_id="mongo-existing", tenant_id="tid-ing", name="o",
        family="nosql_database",  # type: ignore[arg-type]
        source_type="mongodb", collection_id="kb-1",
        connection_config=payload["connection_config"],  # type: ignore[arg-type]
    )
    ingestion_mod._SOURCES[existing.source_id] = existing
    try:
        sync = client.post(f"/sources/{existing.source_id}/sync", headers=_auth())
        assert sync.status_code == 422, sync.text
        assert "disabled" in sync.json()["detail"]
        health = client.get(f"/sources/{existing.source_id}/health", headers=_auth())
        assert health.status_code == 200
        assert health.json()["ok"] is False
        assert "disabled" in health.json()["error"]
    finally:
        ingestion_mod._SOURCES.pop(existing.source_id, None)


@pytest.mark.usefixtures("_mongodb_off")
async def test_mongodb_off_fails_a_scheduled_sync_with_the_reason() -> None:
    from unittest.mock import MagicMock, patch

    from app.ingestion.job_tracker import IngestionJobTracker
    from app.ingestion.scheduler import _sync_source_async
    from app.ingestion.source_config import SourceConfig
    from app.ingestion.source_store import SourceConfigStore

    store, tracker = SourceConfigStore(), IngestionJobTracker()
    await store.create(
        SourceConfig(
            source_id="m1", tenant_id="t1", name="o",
            family="nosql_database",  # type: ignore[arg-type]
            source_type="mongodb", collection_id="kb-1", enabled=True,
            connection_config={"uri": "mongodb://db.example.com/", "database": "shop"},
        )
    )
    with patch(
        "app.ingestion.scheduler._build_worker_ingestion",
        return_value=(tracker, MagicMock(), store),
    ):
        result = await _sync_source_async(
            task=MagicMock(), source_id="m1", tenant_id="t1", triggered_by="scheduler"
        )
    assert "disabled" in result["error"]
    (job,) = tracker.list_jobs_for_source("m1")
    assert job.status == "failed" and "disabled" in job.error_message
