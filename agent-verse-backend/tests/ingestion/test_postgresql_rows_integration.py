"""P1b-7: PostgreSQL rows sync per table, in keyset batches, against a real Postgres.

Live (rw-pg, 2026-10-05): the connector bound its text cursor to a timestamptz
column (asyncpg refused it), kept ONE cursor for all tables (a table that failed
for lack of a grant was skipped for good once another table moved the cursor),
read one batch per sync with ``> cursor`` (the rest of a bulk load sharing the
batch's last timestamp was never read) and could not list rows for
upstream-deletion reconciliation.
"""

from __future__ import annotations

import contextlib
import secrets
from collections.abc import AsyncIterator, Iterator
from typing import Any
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration

asyncpg = pytest.importorskip("asyncpg")


@pytest.fixture(scope="module")
def pg_admin() -> Iterator[str]:
    try:
        from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

        container = PostgresContainer("postgres:16", driver=None)
        container.start()
    except Exception as exc:  # pragma: no cover - Docker unavailable
        pytest.skip(f"could not start a Postgres testcontainer: {exc}")
    try:
        yield container.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
    finally:
        container.stop()


@contextlib.asynccontextmanager
async def _no_pin(dsn: str, *, context: str) -> AsyncIterator[Any]:
    yield None


async def _exec(dsn: str, *sql: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        for st in sql:
            await conn.execute(st)
    finally:
        await conn.close()


@pytest.fixture
async def db(pg_admin: str) -> AsyncIterator[dict[str, str]]:
    ns = f"t_{secrets.token_hex(4)}"
    reader, pw = f"r_{ns}", secrets.token_hex(8)
    await _exec(
        pg_admin,
        f"CREATE SCHEMA {ns}",
        f"CREATE TABLE {ns}.hidden (id int PRIMARY KEY, v text, "
        f"updated_at timestamptz NOT NULL DEFAULT now() - interval '1 day')",
        f"INSERT INTO {ns}.hidden (id, v) VALUES (1, 'payroll row one'), (2, 'payroll row two')",
        f"CREATE TABLE {ns}.events (id bigserial PRIMARY KEY, note text, meta jsonb, "
        f"updated_at timestamptz NOT NULL DEFAULT now())",
        f"INSERT INTO {ns}.events (note, meta) SELECT 'event ' || g, "
        f"jsonb_build_object('seal', 'SL-' || g) FROM generate_series(1, 1200) g",
        f"CREATE VIEW {ns}.events_v AS SELECT id, note, updated_at FROM {ns}.events",
        f"CREATE ROLE {reader} LOGIN PASSWORD '{pw}'",
        f"GRANT USAGE ON SCHEMA {ns} TO {reader}",
        f"GRANT SELECT ON {ns}.events, {ns}.events_v TO {reader}",
    )
    host_dsn = pg_admin.rsplit("@", 1)[1]
    yield {"ns": ns, "admin": pg_admin, "reader_dsn": f"postgresql://{reader}:{pw}@{host_dsn}",
           "reader": reader}
    await _exec(pg_admin, f"DROP SCHEMA {ns} CASCADE", f"DROP ROLE {reader}")


def _config(db: dict[str, str], tables: list[str], **cc: Any) -> Any:
    from app.ingestion.source_config import SourceConfig, SourceFamily

    return SourceConfig(
        source_id="src-pg", tenant_id="t1", name="pg", family=SourceFamily.OLTP_DATABASE,
        source_type="postgresql",
        connection_config={"dsn": db["reader_dsn"], "tables": [f"{db['ns']}.{t}" for t in tables],
                           "batch_size": 500, **cc},
    )


async def _sync(config: Any, cursor: str | None) -> tuple[list[Any], str | None, Exception | None]:
    from app.ingestion.connectors.postgresql_connector import PostgreSQLConnector

    docs: list[Any] = []
    last = cursor
    error: Exception | None = None
    with (
        patch("app.ingestion.connectors.postgresql_connector.pin_source_dsn", _no_pin),
        patch("app.ingestion.connectors.postgresql_connector._pinned_connect_kwargs",
              lambda dsn, pins: {"dsn": dsn}),
    ):
        try:
            async for doc, cur in PostgreSQLConnector().get_delta(config, cursor):
                docs.append(doc)
                last = cur
        except Exception as exc:
            error = exc
    return docs, last, error


async def _live(config: Any) -> set[str]:
    from app.ingestion.connectors.postgresql_connector import PostgreSQLConnector

    with (
        patch("app.ingestion.connectors.postgresql_connector.pin_source_dsn", _no_pin),
        patch("app.ingestion.connectors.postgresql_connector._pinned_connect_kwargs",
              lambda dsn, pins: {"dsn": dsn}),
    ):
        return {d async for d in PostgreSQLConnector().iter_live_doc_ids(config)}


async def test_a_bulk_load_larger_than_a_batch_is_read_whole(db: dict[str, str]) -> None:
    docs, cursor, error = await _sync(_config(db, ["events"]), None)
    assert error is None
    assert len(docs) == 1200
    assert len({d.doc_id for d in docs}) == 1200
    assert '"seal": "SL-1200"' in docs[-1].content.decode()
    again, _, _ = await _sync(_config(db, ["events"]), cursor)
    assert again == []  # nothing changed


async def test_a_failed_table_is_retried_after_other_tables_advanced(db: dict[str, str]) -> None:
    cfg = _config(db, ["events", "hidden"])
    docs, cursor, error = await _sync(cfg, None)
    assert error is not None and "hidden" in str(error) and "permission" in str(error).lower()
    assert len(docs) == 1200
    await _exec(db["admin"], f"GRANT SELECT ON {db['ns']}.hidden TO {db['reader']}")
    docs2, _, error2 = await _sync(cfg, cursor)
    assert error2 is None
    assert sorted(d.title for d in docs2) == ["hidden 1", "hidden 2"]


async def test_updates_inserts_and_deletes(db: dict[str, str]) -> None:
    cfg = _config(db, ["events", "events_v"], primary_keys={"events_v": ["id"]})
    docs, cursor, error = await _sync(cfg, None)
    assert error is None and len(docs) == 2400
    await _exec(db["admin"],
                f"UPDATE {db['ns']}.events SET note = 'edited', updated_at = now() WHERE id = 7",
                f"INSERT INTO {db['ns']}.events (note) VALUES ('brand new')",
                f"DELETE FROM {db['ns']}.events WHERE id = 9")
    changed, _, error = await _sync(cfg, cursor)
    assert error is None
    titles = sorted(d.title for d in changed)
    assert titles == ["events 1201", "events 7", "events_v 1201", "events_v 7"]
    live = await _live(cfg)
    assert len(live) == 2 * 1200  # 1,200 - 1 deleted + 1 inserted, per table
    by_title = {d.title: d.doc_id for d in docs}
    assert by_title["events 9"] not in live
    assert by_title["events 7"] in live


async def test_a_legacy_text_cursor_still_works(db: dict[str, str]) -> None:
    docs, _, error = await _sync(_config(db, ["events"]), "2000-01-01 00:00:00+00:00")
    assert error is None and len(docs) == 1200
