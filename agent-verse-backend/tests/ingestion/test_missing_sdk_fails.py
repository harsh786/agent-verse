"""MISSING-SDK-FAILS: a connector without its SDK fails loudly, never syncs empty.

neo4j, influxdb, clickhouse, s3, snowflake, bigquery, gcs, pubsub, kinesis,
duckdb, kafka, mqtt, youtube and postgresql logged "not installed" and returned
from ``get_delta`` — reported as a successful sync of zero documents. Now:

* the registry refuses the connector (``get_connector`` raises
  ``ConnectorUnavailableError`` with the missing package), so the scheduler
  records a failed job with the reason;
* the Sources catalogue marks it ``available: false`` with the reason;
* calling the connector directly fails validate and sync with the reason (the
  ``BaseConnector`` guard), and so does each connector's own import path when an
  install is broken in a way the table cannot see.

Every connector in ``CONNECTOR_SDKS`` is exercised; the SDK is hidden with
``sys.modules[name] = None`` (the dev environment has every SDK installed).
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

from app.ingestion import connector_sdks
from app.ingestion.base_connector import ConnectorUnavailableError
from app.ingestion.connector_registry import (
    _REGISTRY,
    get_connector,
    get_connector_metadata,
    load_all_connectors,
)
from app.ingestion.connector_sdks import CONNECTOR_SDKS
from app.ingestion.source_config import SourceConfig, SourceFamily

load_all_connectors()
SOURCE_TYPES = sorted(CONNECTOR_SDKS)

# Enough configuration for every connector to reach its SDK import (hosts are on
# the test allowlist and unresolvable, so nothing is dialled).
_CC: dict[str, Any] = {
    "host": "db",
    "port": 1,
    "uri": "bolt://db:7687",
    "url": "http://db:8086",
    "dsn": "postgresql://u:p@db:5432/x",
    "connection_string": ("mongodb://db:27017/x"),
    "endpoint_url": "http://db:9000",
    "bucket": "b",
    "database": "x",
    "table": "t",
    "query": "SELECT 1",
    "project": "p",
    "subscription": "s",
    "stream_name": "s",
    "topics": ["t"],
    "bootstrap_servers": "db:9092",
    "video_ids": ["v1"],
    "folder_id": "f",
    "feed_url": "http://db/feed.xml",
    "urls": ["http://db/feed.xml"],
    "account": "a",
}


def _config(source_type: str) -> SourceConfig:
    cc = dict(_CC)
    if source_type == "mongodb":
        cc["uri"] = "mongodb://db:27017/x"
    if source_type == "redis":
        cc["uri"] = "redis://db:6379/0"
    if source_type == "azure_blob":
        cc["connection_string"] = "AccountName=a;AccountKey=k;BlobEndpoint=http://db:10000/a"
        cc["container"] = "c"
    return SourceConfig(
        source_id=f"src-{source_type}",
        tenant_id="t1",
        name=source_type,
        family=SourceFamily.WEB,
        source_type=source_type,
        collection_id="col-1",
        connection_config=cc,
    )


def _packages(source_type: str) -> list[str]:
    return [req.package for req in CONNECTOR_SDKS[source_type]]


@pytest.fixture
def hidden_sdk(request: pytest.FixtureRequest) -> Iterator[str]:
    source_type: str = request.param
    blocked = {req.module: None for req in CONNECTOR_SDKS[source_type]}
    with patch.dict(sys.modules, blocked):
        yield source_type


@pytest.mark.parametrize("hidden_sdk", SOURCE_TYPES, indirect=True)
def test_registry_refuses_the_connector_with_the_reason(
    hidden_sdk: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings
    from app.ingestion.connector_registry import _FEATURE_FLAGS

    flag = _FEATURE_FLAGS.get(hidden_sdk, "")
    if hasattr(get_settings(), flag):  # dark-launched connectors (duckdb) are off by default
        monkeypatch.setattr(get_settings(), flag, True)
    with pytest.raises(ConnectorUnavailableError) as exc:
        get_connector(hidden_sdk)
    assert all(pkg in str(exc.value) for pkg in _packages(hidden_sdk))


@pytest.mark.parametrize("hidden_sdk", SOURCE_TYPES, indirect=True)
def test_catalogue_marks_the_connector_unavailable(hidden_sdk: str) -> None:
    entry = next(e for e in get_connector_metadata() if e["source_type"] == hidden_sdk)
    assert entry["available"] is False
    assert all(pkg in entry["unavailable_reason"] for pkg in _packages(hidden_sdk))


def test_catalogue_marks_installed_connectors_available() -> None:
    entries = {e["source_type"]: e for e in get_connector_metadata()}
    assert all(e["available"] and e["unavailable_reason"] == "" for e in entries.values())
    assert entries["neo4j"]["required_packages"] == ["neo4j"]


@pytest.mark.parametrize("hidden_sdk", SOURCE_TYPES, indirect=True)
async def test_validate_connection_reports_the_missing_sdk(hidden_sdk: str) -> None:
    health = await _REGISTRY[hidden_sdk]().validate_connection(_config(hidden_sdk))
    assert health.ok is False
    assert all(pkg in health.error for pkg in _packages(hidden_sdk))


@pytest.mark.parametrize("hidden_sdk", SOURCE_TYPES, indirect=True)
async def test_sync_fails_instead_of_succeeding_empty(hidden_sdk: str) -> None:
    connector = _REGISTRY[hidden_sdk]()
    with pytest.raises(ConnectorUnavailableError) as exc:
        [d async for d in connector.get_delta(_config(hidden_sdk), None)]
    assert all(pkg in str(exc.value) for pkg in _packages(hidden_sdk))


@pytest.mark.parametrize("hidden_sdk", SOURCE_TYPES, indirect=True)
async def test_broken_install_still_fails_the_sync(
    hidden_sdk: str, monkeypatch: pytest.MonkeyPatch, allow_unpinnable_drivers: None
) -> None:
    """The table says installed, the import fails anyway (a broken install): each
    connector's own import path must fail the sync, not return empty."""
    monkeypatch.setattr(connector_sdks, "missing_sdks", lambda source_type: [])
    connector = _REGISTRY[hidden_sdk]()
    with pytest.raises((ConnectorUnavailableError, ImportError)):
        [d async for d in connector.get_delta(_config(hidden_sdk), None)]
