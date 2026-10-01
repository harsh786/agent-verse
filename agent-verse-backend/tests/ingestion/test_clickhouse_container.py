"""BLOCKING-SDK: ClickHouse connector against a real server (testcontainers).

The clickhouse-connect calls now run on the SDK pool inside the egress-checked
driver scope; this proves the real client still validates and syncs through it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.ingestion.source_config import SourceConfig, SourceFamily

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def clickhouse_port() -> Iterator[int]:
    import clickhouse_connect
    from testcontainers.clickhouse import ClickHouseContainer

    with ClickHouseContainer(
        "clickhouse/clickhouse-server:24.8", username="ingest", password="ingest-pw"
    ) as container:
        port = int(container.get_exposed_port(8123))
        client = clickhouse_connect.get_client(
            host="localhost", port=port, username="ingest", password="ingest-pw"
        )
        client.command("CREATE TABLE test.events (id UInt32, updated_at String) ENGINE = Memory")
        client.command("INSERT INTO test.events VALUES (1, '2026-01-01'), (2, '2026-01-02')")
        client.command("CREATE TABLE test.events_dt (id UInt32, ts DateTime) ENGINE = Memory")
        client.command(
            "INSERT INTO test.events_dt VALUES (1, '2026-01-01 00:00:00'), (2, '2026-01-02 00:00:00')"
        )
        yield port


@pytest.fixture(autouse=True)
def _allow_localhost(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app.core.config import get_settings

    monkeypatch.setenv("INGESTION_ALLOW_INTERNAL_SOURCES", "true")
    monkeypatch.setenv("INGESTION_INTERNAL_SOURCE_ALLOWLIST", "localhost")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _config(port: int, **cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id="src-ch",
        tenant_id="tenant-ch",
        name="clickhouse",
        family=SourceFamily.OLAP_DATABASE,
        source_type="clickhouse",
        collection_id="col-1",
        connection_config={
            "host": "localhost",
            "port": port,
            "username": "ingest",
            "password": "ingest-pw",
            "database": "test",
            **cc,
        },
    )


async def test_validate_connection_with_the_real_client(clickhouse_port: int) -> None:
    from app.ingestion.connectors.clickhouse_connector import ClickHouseConnector

    health = await ClickHouseConnector().validate_connection(_config(clickhouse_port))
    assert health.ok, health.error
    assert str(health.metadata["version"]).startswith("24.8")


async def test_get_delta_with_the_real_client(clickhouse_port: int) -> None:
    from app.ingestion.connectors.clickhouse_connector import ClickHouseConnector

    cfg = _config(clickhouse_port, table="events")
    docs = [item async for item in ClickHouseConnector().get_delta(cfg, None)]
    assert len(docs) == 2
    assert docs[-1][1] == "2026-01-02"


# ── SQL-INJECTION: the cursor is a bound parameter against the real server ────


async def _sync(port: int, cursor: str, **cc: Any) -> list[str]:
    from app.ingestion.connectors.clickhouse_connector import ClickHouseConnector

    docs = [d async for d, _c in ClickHouseConnector().get_delta(_config(port, **cc), cursor)]
    return [d.content.decode() for d in docs]


async def test_a_quote_in_the_cursor_cannot_rewrite_the_query(clickhouse_port: int) -> None:
    # Pasted into SQL this read: WHERE updated_at > '2026-01-01' OR '1'='1' -> both rows.
    rows = await _sync(clickhouse_port, "2026-01-01' OR '1'='1", table="events")
    assert len(rows) == 1
    assert "id: 2" in rows[0]


async def test_bound_cursor_compares_with_datetime_and_integer_columns(
    clickhouse_port: int,
) -> None:
    by_time = await _sync(
        clickhouse_port, "2026-01-01 00:00:00", table="events_dt", cursor_column="ts"
    )
    assert len(by_time) == 1
    assert "id: 2" in by_time[0]
    by_id = await _sync(clickhouse_port, "1", table="events_dt", cursor_column="id")
    assert len(by_id) == 1
    assert "id: 2" in by_id[0]
