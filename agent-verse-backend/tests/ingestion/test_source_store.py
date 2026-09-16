"""Tests for SourceConfigStore — durable persistence for ingestion Sources.

Two code paths are exercised:
  - in-memory (``db=None``): the dev/test fallback used across the codebase.
  - DB-backed: a fake SQLAlchemy AsyncSession records every executed
    statement/params so we can assert the right SQL shape and RLS wiring
    without a real Postgres.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.ingestion.source_config import SourceConfig, SourceFamily
from app.ingestion.source_store import SourceConfigStore, _iso, _row_to_config


def _make_config(**overrides) -> SourceConfig:
    base = dict(
        source_id="src-1",
        tenant_id="t1",
        name="My Source",
        family=SourceFamily.WEB,
        source_type="web_crawl",
        connection_config={"seed_urls": ["https://example.com"]},
    )
    base.update(overrides)
    return SourceConfig(**base)


# ── In-memory path ───────────────────────────────────────────────────────────


class TestInMemoryStore:
    async def test_create_and_get(self):
        store = SourceConfigStore()
        cfg = _make_config()
        created = await store.create(cfg)
        assert created is cfg
        fetched = await store.get("src-1", "t1")
        assert fetched is cfg

    async def test_get_wrong_tenant_returns_none(self):
        store = SourceConfigStore()
        await store.create(_make_config())
        assert await store.get("src-1", "other-tenant") is None

    async def test_get_missing_returns_none(self):
        store = SourceConfigStore()
        assert await store.get("nope", "t1") is None

    async def test_update_existing_fields(self):
        store = SourceConfigStore()
        await store.create(_make_config())
        updated = await store.update("src-1", "t1", name="Renamed", enabled=False)
        assert updated is not None
        assert updated.name == "Renamed"
        assert updated.enabled is False

    async def test_update_missing_returns_none(self):
        store = SourceConfigStore()
        assert await store.update("nope", "t1", name="x") is None

    async def test_update_wrong_tenant_returns_none(self):
        store = SourceConfigStore()
        await store.create(_make_config())
        assert await store.update("src-1", "other", name="x") is None

    async def test_delete_existing(self):
        store = SourceConfigStore()
        await store.create(_make_config())
        assert await store.delete("src-1", "t1") is True
        assert await store.get("src-1", "t1") is None

    async def test_delete_missing_returns_false(self):
        store = SourceConfigStore()
        assert await store.delete("nope", "t1") is False

    async def test_delete_wrong_tenant_returns_false(self):
        store = SourceConfigStore()
        await store.create(_make_config())
        assert await store.delete("src-1", "other") is False
        assert await store.get("src-1", "t1") is not None

    async def test_list_filters_by_tenant(self):
        store = SourceConfigStore()
        await store.create(_make_config(source_id="s1", tenant_id="t1"))
        await store.create(_make_config(source_id="s2", tenant_id="t2"))
        await store.create(_make_config(source_id="s3", tenant_id="t1"))
        result = await store.list("t1")
        assert {c.source_id for c in result} == {"s1", "s3"}

    async def test_mark_synced_updates_stats(self):
        store = SourceConfigStore()
        await store.create(_make_config())
        await store.mark_synced("src-1", "t1", docs_indexed=5, chunks=20, failed=0)
        cfg = await store.get("src-1", "t1")
        assert cfg.total_docs_indexed == 5
        assert cfg.total_chunks == 20
        assert cfg.consecutive_failures == 0
        assert cfg.last_synced_at is not None

    async def test_mark_synced_increments_consecutive_failures(self):
        store = SourceConfigStore()
        await store.create(_make_config())
        await store.mark_synced("src-1", "t1", docs_indexed=0, chunks=0, failed=1)
        cfg = await store.get("src-1", "t1")
        assert cfg.consecutive_failures == 1
        await store.mark_synced("src-1", "t1", docs_indexed=1, chunks=1, failed=0)
        cfg = await store.get("src-1", "t1")
        assert cfg.consecutive_failures == 0
        assert cfg.total_docs_indexed == 1

    async def test_mark_synced_missing_source_is_noop(self):
        store = SourceConfigStore()
        await store.mark_synced("nope", "t1", docs_indexed=1, chunks=1, failed=0)  # no raise

    async def test_get_system_delegates_to_get_in_memory(self):
        store = SourceConfigStore()
        await store.create(_make_config())
        cfg = await store.get_system("src-1", "t1")
        assert cfg is not None
        assert cfg.source_id == "src-1"

    async def test_list_due_never_synced(self):
        store = SourceConfigStore()
        await store.create(_make_config(source_id="s1", enabled=True, sync_mode="incremental"))
        due = await store.list_due()
        assert ("s1", "t1") in due

    async def test_list_due_excludes_disabled(self):
        store = SourceConfigStore()
        await store.create(_make_config(source_id="s1", enabled=False))
        due = await store.list_due()
        assert due == []

    async def test_list_due_excludes_streaming(self):
        store = SourceConfigStore()
        await store.create(_make_config(source_id="s1", sync_mode="streaming"))
        due = await store.list_due()
        assert due == []

    async def test_list_due_respects_interval(self):
        store = SourceConfigStore()
        cfg = _make_config(source_id="s1", sync_interval_seconds=3600)
        cfg.last_synced_at = datetime.now(UTC).isoformat()
        await store.create(cfg)
        assert await store.list_due() == []

    async def test_list_due_includes_when_interval_elapsed(self):
        store = SourceConfigStore()
        cfg = _make_config(source_id="s1", sync_interval_seconds=60)
        cfg.last_synced_at = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        await store.create(cfg)
        due = await store.list_due()
        assert ("s1", "t1") in due

    async def test_list_due_malformed_timestamp_included(self):
        store = SourceConfigStore()
        cfg = _make_config(source_id="s1")
        cfg.last_synced_at = "not-a-timestamp"
        await store.create(cfg)
        due = await store.list_due()
        assert ("s1", "t1") in due


# ── _row_to_config mapping ───────────────────────────────────────────────────


class TestIso:
    def test_none_returns_empty_string(self):
        assert _iso(None) == ""

    def test_datetime_returns_isoformat(self):
        dt = datetime(2026, 1, 1, tzinfo=UTC)
        assert _iso(dt) == dt.isoformat()

    def test_other_value_stringified(self):
        assert _iso(123) == "123"


class TestRowToConfig:
    def test_maps_known_family(self):
        row = {
            "id": "s1",
            "tenant_id": "t1",
            "family": "streaming",
            "name": "n",
            "source_type": "kinesis",
            "connection_config": {"a": 1},
            "created_at": None,
            "updated_at": None,
            "last_synced_at": None,
        }
        cfg = _row_to_config(row)
        assert cfg.source_id == "s1"
        assert cfg.family == SourceFamily.STREAMING
        assert cfg.connection_config == {"a": 1}

    @staticmethod
    def _base_row(**overrides) -> dict:
        row = {
            "id": "s1",
            "tenant_id": "t1",
            "family": "web",
            "name": "n",
            "source_type": "web_crawl",
        }
        row.update(overrides)
        return row

    def test_unknown_family_defaults_to_agent_generated(self):
        row = self._base_row(family="not-a-real-family")
        cfg = _row_to_config(row)
        assert cfg.family == SourceFamily.AGENT_GENERATED

    def test_missing_family_defaults_to_web(self):
        row = self._base_row(family=None)
        cfg = _row_to_config(row)
        assert cfg.family == SourceFamily.WEB

    def test_source_id_falls_back_to_id_column(self):
        row = self._base_row(id="row-id-1")
        cfg = _row_to_config(row)
        assert cfg.source_id == "row-id-1"

    def test_prefers_explicit_source_id_over_id(self):
        row = self._base_row(id="row-id", source_id="explicit-id")
        cfg = _row_to_config(row)
        assert cfg.source_id == "explicit-id"

    def test_datetime_last_synced_at_isoformatted(self):
        dt = datetime(2026, 1, 1, tzinfo=UTC)
        row = self._base_row(last_synced_at=dt)
        cfg = _row_to_config(row)
        assert cfg.last_synced_at == dt.isoformat()

    def test_none_scalar_fields_are_skipped(self):
        # `chunking_strategy` explicitly None in the row must not override the
        # SourceConfig default ("auto") — only non-None DB values should stick.
        row = self._base_row(chunking_strategy=None)
        cfg = _row_to_config(row)
        assert cfg.chunking_strategy == "auto"


# ── DB-backed path (fake SQLAlchemy session) ─────────────────────────────────


class _FakeResult:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows if rows is not None else []
        self.rowcount = rowcount

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _FakeSession:
    """Minimal async session: records executed SQL, returns scripted results."""

    def __init__(self, result: _FakeResult | None = None):
        self.executed: list[tuple[str, dict]] = []
        self._result = result or _FakeResult()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def begin(self):
        return self

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params or {}))
        return self._result


def _db_factory(session: _FakeSession):
    return lambda: session


class TestDbBackedStore:
    async def test_create_inserts_row(self):
        session = _FakeSession()
        store = SourceConfigStore(db=_db_factory(session))
        cfg = _make_config()
        result = await store.create(cfg)
        assert result is cfg
        insert_calls = [c for c in session.executed if "INSERT INTO source_configs" in c[0]]
        assert len(insert_calls) == 1
        sql, params = insert_calls[0]
        assert params["id"] == "src-1"
        assert params["tenant_id"] == "t1"
        # RLS context sets and resets the tenant GUC around the write
        assert any("set_config" in s for s, _ in session.executed)

    async def test_get_returns_none_when_no_row(self):
        session = _FakeSession(_FakeResult(rows=[]))
        store = SourceConfigStore(db=_db_factory(session))
        assert await store.get("src-1", "t1") is None

    async def test_get_maps_row_to_config(self):
        row = {
            "id": "src-1",
            "tenant_id": "t1",
            "family": "web",
            "name": "Row Name",
            "source_type": "web_crawl",
            "connection_config": {},
            "created_at": None,
            "updated_at": None,
            "last_synced_at": None,
        }
        session = _FakeSession(_FakeResult(rows=[row]))
        store = SourceConfigStore(db=_db_factory(session))
        cfg = await store.get("src-1", "t1")
        assert cfg is not None
        assert cfg.name == "Row Name"

    async def test_update_no_fields_short_circuits_to_get(self):
        row = {
            "id": "src-1",
            "tenant_id": "t1",
            "family": "web",
            "name": "n",
            "source_type": "web_crawl",
        }
        session = _FakeSession(_FakeResult(rows=[row]))
        store = SourceConfigStore(db=_db_factory(session))
        result = await store.update("src-1", "t1")
        assert result is not None
        # only the SELECT from `get` should have run, no UPDATE
        assert all("UPDATE" not in sql for sql, _ in session.executed)

    async def test_update_builds_set_clause(self):
        row = {
            "id": "src-1",
            "tenant_id": "t1",
            "family": "web",
            "name": "Updated",
            "source_type": "web_crawl",
        }
        session = _FakeSession(_FakeResult(rows=[row]))
        store = SourceConfigStore(db=_db_factory(session))
        await store.update("src-1", "t1", name="Updated", connection_config={"x": 1})
        update_calls = [c for c in session.executed if "UPDATE source_configs" in c[0]]
        assert len(update_calls) == 1
        sql, params = update_calls[0]
        assert "name = :name" in sql
        assert "connection_config = CAST(:connection_config AS jsonb)" in sql
        assert params["name"] == "Updated"

    async def test_update_family_field_uses_enum_value(self):
        row = {
            "id": "src-1",
            "tenant_id": "t1",
            "family": "streaming",
            "name": "n",
            "source_type": "kinesis",
        }
        session = _FakeSession(_FakeResult(rows=[row]))
        store = SourceConfigStore(db=_db_factory(session))
        await store.update("src-1", "t1", family=SourceFamily.STREAMING)
        update_calls = [c for c in session.executed if "UPDATE source_configs" in c[0]]
        assert len(update_calls) == 1
        sql, params = update_calls[0]
        assert "family = :family" in sql
        assert params["family"] == "streaming"

    async def test_delete_returns_true_when_rowcount(self):
        session = _FakeSession(_FakeResult(rowcount=1))
        store = SourceConfigStore(db=_db_factory(session))
        assert await store.delete("src-1", "t1") is True

    async def test_delete_returns_false_when_no_rowcount(self):
        session = _FakeSession(_FakeResult(rowcount=0))
        store = SourceConfigStore(db=_db_factory(session))
        assert await store.delete("src-1", "t1") is False

    async def test_list_returns_mapped_configs(self):
        rows = [
            {"id": "s1", "tenant_id": "t1", "family": "web", "name": "n", "source_type": "web_crawl"},
            {"id": "s2", "tenant_id": "t1", "family": "web", "name": "n", "source_type": "web_crawl"},
        ]
        session = _FakeSession(_FakeResult(rows=rows))
        store = SourceConfigStore(db=_db_factory(session))
        result = await store.list("t1")
        assert [c.source_id for c in result] == ["s1", "s2"]

    async def test_mark_synced_executes_update(self):
        session = _FakeSession()
        store = SourceConfigStore(db=_db_factory(session))
        await store.mark_synced("src-1", "t1", docs_indexed=3, chunks=9, failed=0)
        update_calls = [c for c in session.executed if "UPDATE source_configs" in c[0]]
        assert len(update_calls) == 1
        _sql, params = update_calls[0]
        assert params["d"] == 3
        assert params["c"] == 9
        assert params["f"] == 0

    async def test_get_system_bypasses_tenant_rls(self):
        row = {
            "id": "src-1",
            "tenant_id": "t1",
            "family": "web",
            "name": "n",
            "source_type": "web_crawl",
        }
        session = _FakeSession(_FakeResult(rows=[row]))
        store = SourceConfigStore(db=_db_factory(session))
        cfg = await store.get_system("src-1", "t1")
        assert cfg is not None
        assert any("SET LOCAL row_security" in sql for sql, _ in session.executed)

    async def test_list_due_returns_pairs(self):
        class _DueRow:
            def __init__(self, source_id, tenant_id):
                self.source_id = source_id
                self.tenant_id = tenant_id

        session = _FakeSession()

        class _DueResult(_FakeResult):
            def __iter__(self):
                return iter([_DueRow("s1", "t1"), _DueRow("s2", "t2")])

        session._result = _DueResult()
        store = SourceConfigStore(db=_db_factory(session))
        due = await store.list_due()
        assert due == [("s1", "t1"), ("s2", "t2")]
