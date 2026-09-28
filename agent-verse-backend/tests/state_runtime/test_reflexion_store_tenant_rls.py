"""ReflexionStore persists and hydrates ``reflexion_lessons`` under the tenant GUC.

``reflexion_lessons`` is FORCE ROW LEVEL SECURITY. Lessons are recorded from a
goal's own failure and recalled by that goal's planner, so both directions must
run under ``sqlalchemy_rls_context`` for the tenant — without it a NOBYPASSRLS
role rejected every insert and hydrated nothing, and the store's broad
``except`` hid it.
"""

from __future__ import annotations

from app.state_runtime.reflexion_store import ReflexionStore
from tests._rls_recorder import RlsRecordingDb, assert_tenant_scoped

TENANT = "tenant-refl-a"


async def test_record_async_inserts_under_tenant_guc() -> None:
    db = RlsRecordingDb()
    store = ReflexionStore()

    await store.record_async(
        tenant_id=TENANT,
        lesson="retry with smaller page size",
        source_goal_id="g-1",
        failure_class="timeout",
        db_factory=db,
    )

    (insert,) = assert_tenant_scoped(db, "reflexion_lessons", TENANT)
    assert insert.sql.startswith("INSERT INTO reflexion_lessons")
    assert insert.explicit_txn


async def test_load_from_db_hydrates_only_under_tenant_guc() -> None:
    rows = [(TENANT, "lesson one", "g-1", "timeout")]
    db = RlsRecordingDb(rows_for=lambda sql, _p: rows if "FROM reflexion_lessons" in sql else [])
    store = ReflexionStore()

    await store.load_from_db(tenant_id=TENANT, db_factory=db)

    (select,) = assert_tenant_scoped(db, "reflexion_lessons", TENANT)
    assert "WHERE tenant_id = :tenant_id" in select.sql
    lessons = list(store._lessons[TENANT])
    assert [entry["lesson"] for entry in lessons] == ["lesson one"]
