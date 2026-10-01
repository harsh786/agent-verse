"""Tests for InfluxDBConnector — validate_connection, get_delta, cursor handling.

influxdb-client is not installed in the test environment, so the ImportError
branches are exercised naturally; the success branches are exercised by
injecting a fake `influxdb_client` module into sys.modules (mirrors the
pattern used in tests/knowledge/test_ingestors_coverage.py for pypdf).
"""
from __future__ import annotations

import sys
import types

import pytest

from app.ingestion.base_connector import ConnectorUnavailableError

from app.ingestion.connectors.influxdb_connector import InfluxDBConnector
from app.ingestion.source_config import SourceConfig


def _make_config(conn_config: dict | None = None) -> SourceConfig:
    return SourceConfig(
        source_id="src-1",
        tenant_id="t1",
        name="Test InfluxDB",
        family="iot_telemetry",
        source_type="influxdb",
        enabled=True,
        connection_config={"url": "http://influx.test:8086", **(conn_config or {})},
    )


class _FakeHealth:
    def __init__(self, status="pass", version="2.7.1", message=""):
        self.status = status
        self.version = version
        self.message = message


class _FakeRecord:
    def __init__(self, values: dict):
        self.values = values


class _FakeTable:
    def __init__(self, records):
        self.records = records


class _FakeQueryApi:
    def __init__(self, tables):
        self._tables = tables

    def query(self, flux_query, org=None):
        return self._tables


def _install_fake_influxdb_client(health=None, tables=None, query_side_effect=None):
    """Install a fake `influxdb_client` module and return it for assertions."""
    fake_mod = types.ModuleType("influxdb_client")

    class FakeInfluxDBClient:
        instances: list = []

        def __init__(self, url=None, token=None, org=None):
            self.url = url
            self.token = token
            self.org = org
            FakeInfluxDBClient.instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def health(self):
            raise AssertionError("health() is deprecated in influxdb-client; use ping()")

        def ping(self):
            # The real ping() swallows errors and answers False.
            return (health or _FakeHealth()).status == "pass"

        def version(self):
            state = health or _FakeHealth()
            if state.status != "pass":
                raise RuntimeError(state.message or "ping failed")
            return state.version

        def query_api(self):
            if query_side_effect is not None:
                raise query_side_effect
            return _FakeQueryApi(tables or [])

    fake_mod.InfluxDBClient = FakeInfluxDBClient
    sys.modules["influxdb_client"] = fake_mod
    return fake_mod, FakeInfluxDBClient


@pytest.fixture(autouse=True)
def _clean_influxdb_module():
    saved = sys.modules.get("influxdb_client")
    yield
    if saved is not None:
        sys.modules["influxdb_client"] = saved
    else:
        sys.modules.pop("influxdb_client", None)


class TestValidateConnection:
    async def test_no_library_installed_returns_unhealthy(self):
        """Real environment lacks influxdb-client — exercises the ImportError branch."""
        sys.modules["influxdb_client"] = None  # simulate a server without it
        connector = InfluxDBConnector()
        health = await connector.validate_connection(_make_config())
        assert health.ok is False
        assert "influxdb-client" in health.error

    async def test_healthy_connection(self):
        _install_fake_influxdb_client(health=_FakeHealth(status="pass", version="2.7.3"))
        connector = InfluxDBConnector()
        health = await connector.validate_connection(
            _make_config({"url": "http://influx:8086", "token": "tok", "org": "myorg"})
        )
        assert health.ok is True
        assert health.metadata["version"] == "2.7.3"
        assert health.latency_ms >= 0

    async def test_unhealthy_status_reported(self):
        # INFLUX-PING: ping() answers False and hides why; the reason comes from
        # the same /ping endpoint via version().
        _install_fake_influxdb_client(health=_FakeHealth(status="fail", message="disk full"))
        connector = InfluxDBConnector()
        health = await connector.validate_connection(_make_config())
        assert health.ok is False
        assert "disk full" in health.error

    async def test_real_client_validate_uses_ping_not_the_deprecated_health(self):
        """With the real influxdb-client, validate must not call health() — it emits a
        DeprecationWarning (an error under this test config) — but ping()."""
        from unittest.mock import patch

        import influxdb_client

        sys.modules["influxdb_client"] = influxdb_client
        with (
            patch.object(influxdb_client.InfluxDBClient, "ping", return_value=True) as ping,
            patch.object(influxdb_client.InfluxDBClient, "version", return_value="2.7.10"),
        ):
            health = await InfluxDBConnector().validate_connection(
                _make_config({"url": "http://influx.test:8086"})
            )
        assert health.ok is True, health.error
        assert health.metadata["version"] == "2.7.10"
        ping.assert_called_once()

    async def test_generic_exception_is_caught(self):
        _install_fake_influxdb_client(query_side_effect=None)
        fake_mod, cls = _install_fake_influxdb_client()

        def _boom(self, *a, **kw):
            raise RuntimeError("connection refused")

        cls.ping = _boom
        connector = InfluxDBConnector()
        health = await connector.validate_connection(_make_config())
        assert health.ok is False
        assert "connection refused" in health.error


class TestGetDelta:
    async def test_no_library_fails_loudly(self):
        sys.modules["influxdb_client"] = None  # simulate a server without it
        connector = InfluxDBConnector()
        with pytest.raises(ConnectorUnavailableError):
            [d async for d in connector.get_delta(_make_config(), None)]

    async def test_yields_documents_and_advances_cursor(self):
        records = [
            _FakeRecord(
                {
                    "_time": "2026-01-01T00:00:00Z",
                    "_measurement": "cpu",
                    "host": "server-1",
                    "usage": 42.5,
                }
            ),
            _FakeRecord(
                {
                    "_time": "2026-01-02T00:00:00Z",
                    "_measurement": "cpu",
                    "host": "server-2",
                    "usage": 12.1,
                }
            ),
        ]
        _install_fake_influxdb_client(tables=[_FakeTable(records)])
        connector = InfluxDBConnector()
        config = _make_config(
            {"url": "http://influx:8086", "token": "tok", "org": "o", "bucket": "b", "measurement": "cpu"}
        )
        results = [d async for d in connector.get_delta(config, None)]
        assert len(results) == 2
        doc0, cursor0 = results[0]
        assert "cpu" in doc0.content.decode()
        assert doc0.metadata["measurement"] == "cpu"
        doc1, cursor1 = results[1]
        # cursor advances to the max timestamp seen
        assert cursor1 == "2026-01-02T00:00:00Z"
        assert doc1.tenant_id == "t1"
        assert doc1.source_id == "src-1"

    async def test_custom_flux_query_used(self):
        records = [_FakeRecord({"_time": "2026-03-01T00:00:00Z", "_measurement": "mem", "used": 99})]
        _install_fake_influxdb_client(tables=[_FakeTable(records)])
        connector = InfluxDBConnector()
        config = _make_config(
            {
                "url": "http://influx:8086",
                "bucket": "b",
                "flux_query": 'from(bucket:"b") |> range(start: {range_start})',
            }
        )
        results = [d async for d in connector.get_delta(config, "-1h")]
        assert len(results) == 1

    async def test_empty_rows_keeps_cursor(self):
        _install_fake_influxdb_client(tables=[_FakeTable([])])
        connector = InfluxDBConnector()
        config = _make_config({"bucket": "b"})
        results = [d async for d in connector.get_delta(config, "2026-01-01T00:00:00Z")]
        assert results == []


def test_source_type_and_registration():
    from app.ingestion.connector_registry import get_connector

    assert InfluxDBConnector().source_type == "influxdb"
    assert get_connector("influxdb") is InfluxDBConnector
