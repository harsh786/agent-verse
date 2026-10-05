"""P1b-7: MySQL rows sync per table, in keyset batches, against a real MySQL 8.

Live (rw-mysql, 2026-10-05): the connector read ONE table (``tables`` was
ignored: a multi-table source ran an empty query), one batch per sync with
``> cursor`` (the rest of a bulk load sharing the last timestamp was never
read), put whole rows (datetimes, Decimals) into document metadata, rendered
NULLs as ``None`` and could not list rows for upstream-deletion reconciliation.
"""

from __future__ import annotations

import contextlib
import secrets
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration

pymysql = pytest.importorskip("pymysql")


@pytest.fixture(scope="module")
def mysql_root() -> Iterator[dict[str, Any]]:
    try:
        from testcontainers.mysql import MySqlContainer  # type: ignore[import-untyped]

        container = MySqlContainer("mysql:8.0", root_password="rootpw")
        container.start()
    except Exception as exc:  # pragma: no cover - Docker unavailable
        pytest.skip(f"could not start a MySQL testcontainer: {exc}")
    try:
        yield {"host": container.get_container_host_ip(),
               "port": int(container.get_exposed_port(3306)), "password": "rootpw"}
    finally:
        container.stop()


def _root_exec(root: dict[str, Any], *sql: str, db: str | None = None) -> None:
    conn = pymysql.connect(host=root["host"], port=root["port"], user="root",
                           password=root["password"], database=db, autocommit=True,
                           charset="utf8mb4")
    try:
        with conn.cursor() as cur:
            for st in sql:
                cur.execute(st)
    finally:
        conn.close()


@pytest.fixture
def db(mysql_root: dict[str, Any]) -> Iterator[dict[str, Any]]:
    name = f"t_{secrets.token_hex(4)}"
    reader, pw = f"r_{name}", secrets.token_hex(8)
    upd = "updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"
    _root_exec(mysql_root, f"CREATE DATABASE {name} CHARACTER SET utf8mb4")
    _root_exec(
        mysql_root,
        "CREATE TABLE hidden (id INT PRIMARY KEY, v VARCHAR(50), updated_at DATETIME(6) NOT NULL)",
        "INSERT INTO hidden VALUES (1, 'payroll one', '2001-01-01'), (2, 'payroll two', '2001-01-01')",
        f"CREATE TABLE events (id BIGINT AUTO_INCREMENT PRIMARY KEY, note TEXT, meta JSON, "
        f"memo TEXT NULL, {upd})",
        "SET @@cte_max_recursion_depth = 10000",
        "INSERT INTO events (note, meta, updated_at) WITH RECURSIVE g(n) AS (SELECT 1 UNION ALL "
        "SELECT n + 1 FROM g WHERE n < 1200) SELECT CONCAT('évènement ', n), "
        "JSON_OBJECT('seal', CONCAT('SL-', n)), NOW(6) FROM g",
        f"CREATE USER '{reader}'@'%' IDENTIFIED BY '{pw}'",
        f"GRANT SELECT ON {name}.events TO '{reader}'@'%'",
        db=name,
    )
    yield {"db": name, "reader": reader, "pw": pw, "root": mysql_root}
    _root_exec(mysql_root, f"DROP DATABASE {name}", f"DROP USER '{reader}'@'%'")


@contextlib.asynccontextmanager
async def _pin(targets: Any, *, context: str) -> AsyncIterator[Any]:
    class _P:
        @staticmethod
        def ip(host: str) -> str:
            return host

    yield _P()


def _config(db: dict[str, Any], tables: list[str], **cc: Any) -> Any:
    from app.ingestion.source_config import SourceConfig, SourceFamily

    return SourceConfig(
        source_id="src-my", tenant_id="t1", name="my", family=SourceFamily.OLTP_DATABASE,
        source_type="mysql",
        connection_config={"host": db["root"]["host"], "port": db["root"]["port"],
                           "database": db["db"], "username": db["reader"], "password": db["pw"],
                           "tables": tables, "cursor_field": "updated_at", "batch_size": 500,
                           **cc},
    )


async def _sync(config: Any, cursor: str | None) -> tuple[list[Any], str | None, Exception | None]:
    from app.ingestion.connectors.mysql_connector import MySQLConnector

    docs: list[Any] = []
    last, error = cursor, None
    with patch("app.ingestion.connectors.mysql_connector.pin_source_hosts", _pin):
        try:
            async for doc, cur in MySQLConnector().get_delta(config, cursor):
                docs.append(doc)
                last = cur
        except Exception as exc:
            error = exc
    return docs, last, error


async def test_bulk_load_failed_table_and_changes(db: dict[str, Any]) -> None:
    from app.ingestion.connectors.mysql_connector import MySQLConnector

    cfg = _config(db, ["events", "hidden"])
    docs, cursor, error = await _sync(cfg, None)
    assert error is not None and "hidden" in str(error) and "privilege" in str(error)
    assert len(docs) == 1200 and len({d.doc_id for d in docs}) == 1200
    text = docs[-1].content.decode()
    assert "évènement 1200" in text and '"seal": "SL-1200"' in text
    assert "None" not in text  # NULL memo left out
    assert all(set(d.metadata) == {"table", "pk"} for d in docs[:3])

    _root_exec(db["root"], f"GRANT SELECT ON {db['db']}.hidden TO '{db['reader']}'@'%'",
               "UPDATE events SET note = 'edited' WHERE id = 7",
               "INSERT INTO events (note) VALUES ('brand new')",
               "DELETE FROM events WHERE id = 9", db=db["db"])
    changed, _, error2 = await _sync(cfg, cursor)
    assert error2 is None
    titles = sorted(d.title for d in changed)
    new_ids = [t for t in titles if t.startswith("events ") and int(t.split()[1]) > 1200]
    assert len(new_ids) == 1  # the inserted row (InnoDB may skip auto-increment values)
    assert sorted(set(titles) - set(new_ids)) == ["events 7", "hidden 1", "hidden 2"]

    with patch("app.ingestion.connectors.mysql_connector.pin_source_hosts", _pin):
        live = {d async for d in MySQLConnector().iter_live_doc_ids(cfg)}
    assert len(live) == 1200 + 2
    by_title = {d.title: d.doc_id for d in docs}
    assert by_title["events 9"] not in live and by_title["events 7"] in live
