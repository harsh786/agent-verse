"""Tests for DuckDBConnector — local analytics DB ingestion via duckdb.

duckdb is not installed in the test environment, so the module is faked via
sys.modules injection to exercise both the "installed" and "not installed"
branches.
"""
from __future__ import annotations

import pathlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import get_settings
from app.ingestion.connectors.duckdb_connector import DuckDBConnector, DuckDBPathError
from app.ingestion.source_config import SourceConfig, SourceFamily


@pytest.fixture(autouse=True)
def _duckdb_root(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    monkeypatch.setattr(get_settings(), "duckdb_data_root", str(tmp_path))
    return tmp_path


def _root_path() -> pathlib.Path:
    return (pathlib.Path(get_settings().duckdb_data_root) / "t1").resolve()


def _assert_tenant_sql(con: MagicMock, sql: str) -> None:
    """``sql`` is the only statement after the three sandboxing SETs."""
    statements = [c.args[0] for c in con.execute.call_args_list]
    assert statements[:3] == [
        f"SET allowed_directories = ['{_root_path()}/']",
        "SET enable_external_access = false",
        "SET lock_configuration = true",
    ]
    assert statements[3:] == [sql]


def _config(**cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id="src-duckdb",
        tenant_id="t1",
        name="duckdb-src",
        family=SourceFamily.OLAP_DATABASE,
        source_type="duckdb",
        connection_config=cc,
    )


def _fake_duckdb_module(con: MagicMock) -> MagicMock:
    mod = MagicMock()
    mod.connect = MagicMock(return_value=con)
    return mod


@pytest.mark.asyncio
async def test_validate_connection_ok() -> None:
    con = MagicMock()
    con.execute = MagicMock()
    con.close = MagicMock()
    fake_mod = _fake_duckdb_module(con)

    with patch.dict("sys.modules", {"duckdb": fake_mod}):
        health = await DuckDBConnector().validate_connection(_config(database=":memory:"))

    assert health.ok is True
    assert health.metadata == {"database": ":memory:"}
    _assert_tenant_sql(con, "SELECT 1")
    con.close.assert_called_once()


@pytest.mark.asyncio
async def test_validate_connection_not_installed() -> None:
    with patch.dict("sys.modules", {"duckdb": None}):
        health = await DuckDBConnector().validate_connection(_config())
    assert health.ok is False
    assert "not installed" in health.error


@pytest.mark.asyncio
async def test_validate_connection_error() -> None:
    fake_mod = MagicMock()
    fake_mod.connect = MagicMock(side_effect=RuntimeError("boom"))
    with patch.dict("sys.modules", {"duckdb": fake_mod}):
        health = await DuckDBConnector().validate_connection(_config())
    assert health.ok is False
    assert "boom" in health.error


@pytest.mark.asyncio
async def test_get_delta_not_installed_yields_nothing() -> None:
    with patch.dict("sys.modules", {"duckdb": None}):
        docs = [d async for d in DuckDBConnector().get_delta(_config(), None)]
    assert docs == []


@pytest.mark.asyncio
async def test_get_delta_file_mode_builds_query_and_yields_docs() -> None:
    con = MagicMock()
    result = MagicMock()
    result.description = [("id",), ("name",)]
    result.fetchall = MagicMock(return_value=[(6, "foo"), (7, "bar")])
    con.execute = MagicMock(return_value=result)
    fake_mod = _fake_duckdb_module(con)

    cfg = _config(
        database=":memory:",
        mode="file",
        file="data.parquet",
        cursor_column="id",
        batch_size=500,
    )

    with patch.dict("sys.modules", {"duckdb": fake_mod}):
        docs = [d async for d in DuckDBConnector().get_delta(cfg, "5")]

    _assert_tenant_sql(con, 
        f"SELECT * FROM '{_root_path()}/data.parquet' WHERE \"id\" > '5' LIMIT 500"
    )
    assert len(docs) == 2
    doc0, cursor0 = docs[0]
    assert b"id: 6" in doc0.content
    assert b"name: foo" in doc0.content
    assert doc0.content_type == "text/plain"
    assert cursor0 == "6"
    doc1, cursor1 = docs[1]
    assert cursor1 == "7"
    con.close.assert_called_once()


@pytest.mark.asyncio
async def test_get_delta_query_mode_replaces_cursor_placeholder() -> None:
    con = MagicMock()
    result = MagicMock()
    result.description = [("x",)]
    result.fetchall = MagicMock(return_value=[(1,)])
    con.execute = MagicMock(return_value=result)
    fake_mod = _fake_duckdb_module(con)

    cfg = _config(query="SELECT * FROM t WHERE x > {cursor}")

    with patch.dict("sys.modules", {"duckdb": fake_mod}):
        docs = [d async for d in DuckDBConnector().get_delta(cfg, "10")]

    _assert_tenant_sql(con, "SELECT * FROM t WHERE x > 10")
    assert len(docs) == 1


@pytest.mark.asyncio
async def test_get_delta_query_mode_default_query() -> None:
    con = MagicMock()
    result = MagicMock()
    result.description = [("1",)]
    result.fetchall = MagicMock(return_value=[])
    con.execute = MagicMock(return_value=result)
    fake_mod = _fake_duckdb_module(con)

    with patch.dict("sys.modules", {"duckdb": fake_mod}):
        docs = [d async for d in DuckDBConnector().get_delta(_config(), None)]

    _assert_tenant_sql(con, "SELECT 1")
    assert docs == []


# --- Security regressions: tenant SQL must never reach host files ----------


@pytest.mark.asyncio
async def test_connect_disables_external_access_and_locks_config() -> None:
    con = MagicMock()
    result = MagicMock()
    result.description = [("x",)]
    result.fetchall = MagicMock(return_value=[])
    con.execute = MagicMock(return_value=result)
    fake_mod = _fake_duckdb_module(con)

    cfg = _config(query="SELECT * FROM read_csv('/etc/passwd')")
    with patch.dict("sys.modules", {"duckdb": fake_mod}):
        _ = [d async for d in DuckDBConnector().get_delta(cfg, None)]

    _, kwargs = fake_mod.connect.call_args
    conf = kwargs["config"]
    assert conf["autoinstall_known_extensions"] is False
    assert conf["autoload_known_extensions"] is False
    # Sandboxed right after connect, in the order real DuckDB accepts, before any
    # tenant SQL runs.
    statements = [c.args[0] for c in con.execute.call_args_list]
    assert statements[:3] == [
        f"SET allowed_directories = ['{_root_path()}/']",
        "SET enable_external_access = false",
        "SET lock_configuration = true",
    ]
    assert statements[3] == "SELECT * FROM read_csv('/etc/passwd')"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/etc/passwd", "../../etc/passwd", "../t2/data.parquet", "s3://bucket/x.parquet"]
)
async def test_file_mode_rejects_paths_outside_tenant_root(path: str) -> None:
    fake_mod = _fake_duckdb_module(MagicMock())
    cfg = _config(mode="file", file=path)
    with patch.dict("sys.modules", {"duckdb": fake_mod}), pytest.raises(DuckDBPathError):
        _ = [d async for d in DuckDBConnector().get_delta(cfg, None)]
    fake_mod.connect.assert_not_called()


@pytest.mark.asyncio
async def test_database_path_outside_tenant_root_rejected() -> None:
    fake_mod = _fake_duckdb_module(MagicMock())
    with patch.dict("sys.modules", {"duckdb": fake_mod}):
        health = await DuckDBConnector().validate_connection(
            _config(database="/var/lib/postgresql/data/app.duckdb")
        )
    assert health.ok is False
    assert "outside the tenant data directory" in (health.error or "")
    fake_mod.connect.assert_not_called()


@pytest.mark.asyncio
async def test_symlink_escape_rejected() -> None:
    root = _root_path()
    root.mkdir(parents=True, exist_ok=True)
    (root / "link.csv").symlink_to("/etc/hosts")
    fake_mod = _fake_duckdb_module(MagicMock())
    cfg = _config(mode="file", file="link.csv")
    with patch.dict("sys.modules", {"duckdb": fake_mod}), pytest.raises(DuckDBPathError):
        _ = [d async for d in DuckDBConnector().get_delta(cfg, None)]


@pytest.mark.asyncio
async def test_cursor_column_must_be_identifier() -> None:
    fake_mod = _fake_duckdb_module(MagicMock())
    cfg = _config(mode="file", file="d.csv", cursor_column="id; DROP TABLE x")
    with patch.dict("sys.modules", {"duckdb": fake_mod}), pytest.raises(ValueError):
        _ = [d async for d in DuckDBConnector().get_delta(cfg, "1")]


# --- KB-20: the confinement exercised against a REAL duckdb (when installed) --


@pytest.mark.asyncio
async def test_real_duckdb_refuses_host_files_and_external_access(
    tmp_path: pathlib.Path,
) -> None:
    """The mocked tests above only prove the config dict is passed; this one
    proves DuckDB itself enforces it. Skips where duckdb is not installed."""
    pytest.importorskip("duckdb")
    outside = tmp_path / "outside.csv"
    outside.write_text("secret\nhost-file-content\n")
    root = _root_path()
    root.mkdir(parents=True, exist_ok=True)
    (root / "mine.csv").write_text("value\ntenant-row\n")

    connector = DuckDBConnector()
    try:
        leaked = [
            d
            async for d in connector.get_delta(
                _config(query=f"SELECT * FROM read_csv('{outside}')"), None
            )
        ]
    except Exception:
        leaked = []  # refused by DuckDB's allowed_directories
    assert all(b"host-file-content" not in d.content for d, _ in leaked)

    own = [
        d
        async for d in connector.get_delta(
            _config(query=f"SELECT * FROM read_csv('{root / 'mine.csv'}')"), None
        )
    ]
    assert any(b"tenant-row" in d.content for d, _ in own)
