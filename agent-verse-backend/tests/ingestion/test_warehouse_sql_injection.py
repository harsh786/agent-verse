"""SQL-INJECTION: ClickHouse, Snowflake and BigQuery bind values and check identifiers.

The connectors pasted ``table`` / ``cursor_column`` / ``stream_name`` and the
sync cursor into SQL. The cursor is a value read back from the source's rows, so
a row holding ``x' OR '1'='1`` rewrote the next incremental query (and any quote
broke it). Values are now bound parameters; identifiers must be plain SQL names.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.ingestion.sql_safety import UnsafeIdentifierError
from app.ingestion.source_config import SourceConfig, SourceFamily

EVIL_CURSOR = "2026-01-01' OR '1'='1' --"
EVIL_TABLES = ["t; DROP TABLE users", "t` UNION SELECT 1 --", "t WHERE 1=1", "a.b.c.d", ""]


def _config(source_type: str, **cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id=f"src-{source_type}",
        tenant_id="t1",
        name=source_type,
        family=SourceFamily.OLAP_DATABASE,
        source_type=source_type,
        connection_config=cc,
    )


async def _drain(gen: Any) -> list[Any]:
    return [item async for item in gen]


# ── ClickHouse ────────────────────────────────────────────────────────────────


def _fake_clickhouse() -> tuple[dict[str, ModuleType], MagicMock]:
    client = MagicMock()
    client.query.return_value = MagicMock(column_names=["id"], result_rows=[])
    mod = ModuleType("clickhouse_connect")
    mod.get_client = MagicMock(return_value=client)  # type: ignore[attr-defined]
    return {"clickhouse_connect": mod}, client


async def test_clickhouse_binds_the_cursor() -> None:
    from app.ingestion.connectors.clickhouse_connector import ClickHouseConnector

    mods, client = _fake_clickhouse()
    cfg = _config("clickhouse", host="ch.local", table="db.events", cursor_column="updated_at")
    with patch.dict(sys.modules, mods):
        await _drain(ClickHouseConnector().get_delta(cfg, EVIL_CURSOR))
    sql = client.query.call_args.args[0]
    assert EVIL_CURSOR not in sql
    assert sql == (
        "SELECT * FROM `db`.`events` WHERE `updated_at` > {cursor:String} "
        "ORDER BY `updated_at` LIMIT 1000"
    )
    assert client.query.call_args.kwargs["parameters"] == {"cursor": EVIL_CURSOR}


async def test_clickhouse_custom_query_placeholder_is_bound() -> None:
    from app.ingestion.connectors.clickhouse_connector import ClickHouseConnector

    mods, client = _fake_clickhouse()
    cfg = _config("clickhouse", host="ch.local", query="SELECT * FROM e WHERE ts > '{cursor}'")
    with patch.dict(sys.modules, mods):
        await _drain(ClickHouseConnector().get_delta(cfg, EVIL_CURSOR))
    assert client.query.call_args.args[0] == "SELECT * FROM e WHERE ts > {cursor:String}"
    assert client.query.call_args.kwargs["parameters"] == {"cursor": EVIL_CURSOR}


@pytest.mark.parametrize("field", ["table", "cursor_column"])
@pytest.mark.parametrize("evil", EVIL_TABLES)
async def test_clickhouse_refuses_unsafe_identifiers(field: str, evil: str) -> None:
    from app.ingestion.connectors.clickhouse_connector import ClickHouseConnector

    mods, client = _fake_clickhouse()
    cc = {"host": "ch.local", "table": "events", "cursor_column": "updated_at", field: evil}
    with patch.dict(sys.modules, mods), pytest.raises(UnsafeIdentifierError):
        await _drain(ClickHouseConnector().get_delta(_config("clickhouse", **cc), None))
    client.query.assert_not_called()


# ── Snowflake ─────────────────────────────────────────────────────────────────


class _Cursor:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def execute(self, *args: Any) -> None:
        self.calls.append(args)

    def __iter__(self) -> Any:
        return iter([])


def _fake_snowflake() -> tuple[dict[str, ModuleType], _Cursor]:
    cur = _Cursor()
    conn = MagicMock()
    conn.cursor.return_value = cur
    connector = ModuleType("snowflake.connector")
    connector.connect = MagicMock(return_value=conn)  # type: ignore[attr-defined]
    connector.DictCursor = object  # type: ignore[attr-defined]
    pkg = ModuleType("snowflake")
    pkg.connector = connector  # type: ignore[attr-defined]
    return {"snowflake": pkg, "snowflake.connector": connector}, cur


async def test_snowflake_stream_mode_binds_the_cursor() -> None:
    from app.ingestion.connectors.snowflake_connector import SnowflakeConnector

    mods, cur = _fake_snowflake()
    cfg = _config("snowflake", mode="stream", stream_name="DB.PUBLIC.ORDERS_STREAM")
    with patch.dict(sys.modules, mods):
        await _drain(SnowflakeConnector().get_delta(cfg, EVIL_CURSOR))
    assert cur.calls == [
        (
            "SELECT * FROM DB.PUBLIC.ORDERS_STREAM WHERE UPDATED_AT > %(cursor)s LIMIT 1000",
            {"cursor": EVIL_CURSOR},
        )
    ]


async def test_snowflake_query_placeholder_is_bound_and_percent_escaped() -> None:
    from app.ingestion.connectors.snowflake_connector import SnowflakeConnector

    mods, cur = _fake_snowflake()
    query = "SELECT * FROM T WHERE NAME LIKE 'a%' AND TS > '{cursor}'"
    with patch.dict(sys.modules, mods):
        await _drain(SnowflakeConnector().get_delta(_config("snowflake", query=query), EVIL_CURSOR))
    assert cur.calls == [
        ("SELECT * FROM T WHERE NAME LIKE 'a%%' AND TS > %(cursor)s", {"cursor": EVIL_CURSOR})
    ]


async def test_snowflake_query_without_placeholder_appends_a_bound_filter() -> None:
    from app.ingestion.connectors.snowflake_connector import SnowflakeConnector

    mods, cur = _fake_snowflake()
    cfg = _config("snowflake", query="SELECT * FROM T", cursor_column="TS", batch_size=50)
    with patch.dict(sys.modules, mods):
        await _drain(SnowflakeConnector().get_delta(cfg, EVIL_CURSOR))
    assert cur.calls == [
        ("SELECT * FROM T WHERE TS > %(cursor)s LIMIT 50", {"cursor": EVIL_CURSOR})
    ]


async def test_snowflake_first_sync_runs_the_query_unbound() -> None:
    from app.ingestion.connectors.snowflake_connector import SnowflakeConnector

    mods, cur = _fake_snowflake()
    with patch.dict(sys.modules, mods):
        await _drain(SnowflakeConnector().get_delta(_config("snowflake", query="SELECT 1"), None))
    assert cur.calls == [("SELECT 1",)]


@pytest.mark.parametrize("evil", EVIL_TABLES)
async def test_snowflake_refuses_unsafe_stream_names(evil: str) -> None:
    from app.ingestion.connectors.snowflake_connector import SnowflakeConnector

    mods, cur = _fake_snowflake()
    cfg = _config("snowflake", mode="stream", stream_name=evil)
    with patch.dict(sys.modules, mods), pytest.raises(UnsafeIdentifierError):
        await _drain(SnowflakeConnector().get_delta(cfg, None))
    assert cur.calls == []


@pytest.mark.parametrize("evil", EVIL_TABLES)
async def test_snowflake_refuses_unsafe_cursor_columns(evil: str) -> None:
    from app.ingestion.connectors.snowflake_connector import SnowflakeConnector

    mods, cur = _fake_snowflake()
    cfg = _config("snowflake", mode="stream", stream_name="S", cursor_column=evil)
    with patch.dict(sys.modules, mods), pytest.raises(UnsafeIdentifierError):
        await _drain(SnowflakeConnector().get_delta(cfg, EVIL_CURSOR))
    assert cur.calls == []


# ── BigQuery ──────────────────────────────────────────────────────────────────


def _fake_bigquery() -> tuple[dict[str, ModuleType], MagicMock]:
    client = MagicMock()
    client.query.return_value.result.return_value = iter([])
    mod = ModuleType("google.cloud.bigquery")
    mod.Client = MagicMock(return_value=client)  # type: ignore[attr-defined]

    class QueryJobConfig:
        def __init__(self, query_parameters: list[Any] | None = None) -> None:
            self.query_parameters = query_parameters or []

    class ScalarQueryParameter:
        def __init__(self, name: str, type_: str, value: Any) -> None:
            self.name, self.type_, self.value = name, type_, value

    mod.QueryJobConfig = QueryJobConfig  # type: ignore[attr-defined]
    mod.ScalarQueryParameter = ScalarQueryParameter  # type: ignore[attr-defined]
    return {"google.cloud.bigquery": mod}, client


def _bound(client: MagicMock) -> dict[str, Any]:
    job_config = client.query.call_args.kwargs.get("job_config")
    params = job_config.query_parameters if job_config else []
    return {p.name: (p.type_, p.value) for p in params}


async def test_bigquery_table_mode_binds_the_cursor() -> None:
    from app.ingestion.connectors.bigquery_connector import BigQueryConnector

    mods, client = _fake_bigquery()
    cfg = _config("bigquery", project="p", mode="table", table="my-project.sales.orders")
    with patch.dict(sys.modules, mods):
        await _drain(BigQueryConnector().get_delta(cfg, EVIL_CURSOR))
    assert client.query.call_args.args[0] == (
        "SELECT * FROM `my-project`.`sales`.`orders` WHERE `updated_at` > @cursor "
        "ORDER BY `updated_at` LIMIT 1000"
    )
    assert _bound(client) == {"cursor": ("STRING", EVIL_CURSOR)}


async def test_bigquery_query_placeholder_is_bound() -> None:
    from app.ingestion.connectors.bigquery_connector import BigQueryConnector

    mods, client = _fake_bigquery()
    cfg = _config("bigquery", project="p", query="SELECT * FROM d.t WHERE ts > '{cursor}'")
    with patch.dict(sys.modules, mods):
        await _drain(BigQueryConnector().get_delta(cfg, EVIL_CURSOR))
    assert client.query.call_args.args[0] == "SELECT * FROM d.t WHERE ts > @cursor"
    assert _bound(client) == {"cursor": ("STRING", EVIL_CURSOR)}


@pytest.mark.parametrize("field", ["table", "cursor_column"])
@pytest.mark.parametrize("evil", EVIL_TABLES)
async def test_bigquery_refuses_unsafe_identifiers(field: str, evil: str) -> None:
    from app.ingestion.connectors.bigquery_connector import BigQueryConnector

    mods, client = _fake_bigquery()
    cc = {"project": "p", "mode": "table", "table": "d.t", "cursor_column": "ts", field: evil}
    with patch.dict(sys.modules, mods), pytest.raises(UnsafeIdentifierError):
        await _drain(BigQueryConnector().get_delta(_config("bigquery", **cc), None))
    client.query.assert_not_called()
