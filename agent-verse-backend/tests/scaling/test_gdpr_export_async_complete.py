"""a09-F212-03: the async GDPR export is complete or it fails — never truncated.

``run_gdpr_export`` read goals and audit_log with a bare ``LIMIT 10000`` (rows past
that were silently dropped from an Art. 15/20 export), selected a non-existent
``audit_log.event_id`` column and swallowed the audit read failure to ``[]`` —
so the job was marked ``complete`` with no audit trail at all.

Now both sections are read in keyset pages over ``(created_at, id)`` (the
``(tenant_id, created_at)`` indexes), an audit read error fails the job, and a
section larger than the export ceiling fails the job with ``too_large`` instead
of shipping part of the data.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

TENANT = "t-export"
BASE = datetime(2026, 1, 1, tzinfo=UTC)


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self._rows = rows or []

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class _Tables:
    """In-memory goals / audit_log that answer keyset-paged SELECTs."""

    def __init__(
        self,
        goals: int,
        audit: int,
        audit_error: Exception | None = None,
        extra: dict[str, list[tuple[Any, ...]]] | None = None,
    ) -> None:
        self.goals = [
            (f"g{i:06d}", f"goal {i}", "completed", BASE + timedelta(seconds=i // 3))
            for i in range(goals)
        ]
        self.audit = [
            (f"a{i:06d}", f"g{i:06d}", "tool_x", "ok", "low", BASE + timedelta(seconds=i // 3))
            for i in range(audit)
        ]
        self.audit_error = audit_error
        self.sql: list[tuple[str, dict[str, Any]]] = []
        self.compliance_payload: str | None = None
        self.job_updates: list[dict[str, Any]] = []
        # agents / schedules / knowledge_collections rows (id first, created_at last)
        self.extra = extra or {}
        # (session identity, write kind) — which transaction each write ran in
        self.writes: list[tuple[int, str]] = []

    def _page(self, rows: list[tuple[Any, ...]], params: dict[str, Any]) -> _Result:
        assert params["tid"] == TENANT
        if "c_at" in params:
            rows = [r for r in rows if (r[-1], r[0]) > (params["c_at"], params["c_id"])]
        rows = sorted(rows, key=lambda r: (r[-1], r[0]))
        return _Result(rows[: params["lim"]])

    def execute(
        self, stmt: Any, params: dict[str, Any] | None = None, session_id: int = 0
    ) -> _Result:
        sql = str(stmt)
        params = params or {}
        self.sql.append((sql, params))
        for table, rows in self.extra.items():
            if f"FROM {table} " in sql:
                assert "tenant_id = :tid" in sql
                return self._page(rows, params)
        if "FROM goals" in sql:
            assert "tenant_id = :tid" in sql
            return self._page(self.goals, params)
        if "FROM audit_log" in sql:
            assert "tenant_id = :tid" in sql
            if self.audit_error is not None:
                raise self.audit_error
            return self._page(self.audit, params)
        if "INSERT INTO compliance_requests" in sql:
            self.compliance_payload = params["payload"]
            self.writes.append((session_id, "payload"))
        if "UPDATE gdpr_export_jobs" in sql:
            self.job_updates.append({"sql": sql, **params})
            self.writes.append((session_id, "job"))
        return _Result()


class _Session:
    def __init__(self, tables: _Tables) -> None:
        self._t = tables

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        return self._t.execute(stmt, params, session_id=id(self))

    def begin(self) -> _Session:
        return self

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _factory(tables: _Tables) -> Any:
    def _make() -> _Session:
        return _Session(tables)

    return _make


def _run(tables: _Tables) -> dict[str, Any]:
    from app.scaling.tasks import run_gdpr_export

    with patch("app.db.session.get_session_factory", return_value=_factory(tables)):
        return run_gdpr_export.run(job_id="job-1", tenant_id=TENANT)  # type: ignore[no-any-return]


def test_export_pages_through_every_row_past_the_old_10000_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    import app.scaling.tasks as tasks

    monkeypatch.setattr(tasks, "GDPR_EXPORT_PAGE_SIZE", 7)
    tables = _Tables(goals=50, audit=23)
    result = _run(tables)

    assert result["status"] == "complete"
    payload = json.loads(tables.compliance_payload or "{}")
    assert [g["id"] for g in payload["goals"]] == [g[0] for g in tables.goals]
    assert [a["id"] for a in payload["audit_entries"]] == [a[0] for a in tables.audit]
    assert payload["complete"] is True
    # Keyset pages, ordered on the indexed (created_at, id), never OFFSET.
    goal_reads = [s for s, _ in tables.sql if "FROM goals" in s]
    assert len(goal_reads) == 8  # 50 rows / 7 per page, last page short
    for s in goal_reads:
        assert re.search(r"ORDER BY created_at, id", s)
        assert "OFFSET" not in s.upper()
        assert "LIMIT 10000" not in s
    assert "(created_at, id) > (:c_at, :c_id)" in goal_reads[1]
    # The audit read selects the real primary key, not a non-existent event_id.
    audit_reads = [s for s, _ in tables.sql if "FROM audit_log" in s]
    assert all("event_id" not in s for s in audit_reads)


def test_audit_read_failure_fails_the_job_instead_of_exporting_nothing() -> None:
    tables = _Tables(goals=3, audit=3, audit_error=RuntimeError("audit_log unreadable"))
    with pytest.raises(RuntimeError, match="audit_log unreadable"):
        _run(tables)
    assert tables.compliance_payload is None
    assert len(tables.job_updates) == 1
    assert "status = 'failed'" in tables.job_updates[0]["sql"]


def test_section_over_the_ceiling_fails_too_large_not_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.scaling.tasks as tasks

    monkeypatch.setattr(tasks, "GDPR_EXPORT_PAGE_SIZE", 4)
    monkeypatch.setattr(tasks, "GDPR_EXPORT_MAX_ROWS", 10)
    tables = _Tables(goals=11, audit=1)
    with pytest.raises(tasks.GdprExportTooLargeError):
        _run(tables)
    assert tables.compliance_payload is None
    (update,) = tables.job_updates
    assert "status = 'failed'" in update["sql"]
    assert update["err"].startswith("too_large")


# ── salvage (agent-a9c06085 RV-08): async export parity, atomic completion ───


def _extra_rows() -> dict[str, list[tuple[Any, ...]]]:
    return {
        "agents": [
            (f"ag{i}", f"agent {i}", i % 2 == 0, BASE + timedelta(seconds=i)) for i in range(5)
        ],
        "schedules": [
            (f"s{i}", "ag0", "cron", "0 * * * *", f"hourly {i}", BASE + timedelta(seconds=i))
            for i in range(3)
        ],
        "knowledge_collections": [
            (f"kc{i}", f"kb {i}", None, i, BASE + timedelta(seconds=i)) for i in range(4)
        ],
    }


def test_async_export_has_every_section_the_sync_export_has(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The async job is where a large tenant is sent ("use the asynchronous GDPR
    export"); it exported only goals + audit, silently dropping the agents,
    schedules and knowledge collections the sync export carries."""
    import json

    import app.scaling.tasks as tasks

    monkeypatch.setattr(tasks, "GDPR_EXPORT_PAGE_SIZE", 2)
    tables = _Tables(goals=1, audit=1, extra=_extra_rows())
    assert _run(tables)["status"] == "complete"
    payload = json.loads(tables.compliance_payload or "{}")

    assert [a["id"] for a in payload["agents"]] == ["ag0", "ag1", "ag2", "ag3", "ag4"]
    assert payload["agents"][1] == {
        "id": "ag1",
        "name": "agent 1",
        "is_active": False,
        "created_at": (BASE + timedelta(seconds=1)).isoformat(),
    }
    assert [s["id"] for s in payload["schedules"]] == ["s0", "s1", "s2"]
    assert payload["schedules"][0]["cron_expression"] == "0 * * * *"
    assert [k["id"] for k in payload["knowledge_collections"]] == ["kc0", "kc1", "kc2", "kc3"]
    assert payload["knowledge_collections"][3]["document_count"] == 3
    assert payload["knowledge_collections"][0]["description"] == ""
    assert payload["counts"] == {
        "goals": 1,
        "audit_entries": 1,
        "agents": 5,
        "schedules": 3,
        "knowledge_collections": 4,
    }
    # Same keyset paging as goals/audit (pages of 2 → 3 reads for 5 agents).
    agent_reads = [s for s, _ in tables.sql if "FROM agents " in s]
    assert len(agent_reads) == 3
    assert all("ORDER BY created_at, id" in s for s in agent_reads)


def test_new_section_read_error_fails_the_job() -> None:
    class _Boom(_Tables):
        def execute(
            self, stmt: Any, params: dict[str, Any] | None = None, session_id: int = 0
        ) -> _Result:
            if "FROM knowledge_collections " in str(stmt):
                raise RuntimeError("knowledge_collections unreadable")
            return super().execute(stmt, params, session_id)

    tables = _Boom(goals=1, audit=1, extra=_extra_rows())
    with pytest.raises(RuntimeError, match="knowledge_collections unreadable"):
        _run(tables)
    assert tables.compliance_payload is None
    (update,) = tables.job_updates
    assert "status = 'failed'" in update["sql"]


def test_payload_and_job_completion_commit_in_one_transaction() -> None:
    """Two transactions left a crash window: the export stored 'ready' while the
    job row stayed 'pending' forever."""
    tables = _Tables(goals=2, audit=2)
    _run(tables)
    kinds = [k for _, k in tables.writes]
    assert kinds == ["payload", "job"]
    assert tables.writes[0][0] == tables.writes[1][0]
    assert "status = 'complete'" in tables.job_updates[0]["sql"]


def test_failure_to_mark_the_job_failed_is_logged_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock

    import app.scaling.tasks as tasks

    class _NoJobWrite(_Tables):
        def execute(
            self, stmt: Any, params: dict[str, Any] | None = None, session_id: int = 0
        ) -> _Result:
            if "UPDATE gdpr_export_jobs" in str(stmt):
                raise ConnectionError("db gone")
            return super().execute(stmt, params, session_id)

    log = MagicMock()
    monkeypatch.setattr(tasks, "logger", log)
    tables = _NoJobWrite(goals=1, audit=1, audit_error=RuntimeError("audit_log unreadable"))
    with pytest.raises(RuntimeError, match="audit_log unreadable"):
        _run(tables)
    assert log.error.called
    event, kwargs = log.error.call_args.args[0], log.error.call_args.kwargs
    assert event == "gdpr_export_mark_failed_failed"
    assert kwargs["job_id"] == "job-1" and kwargs["tenant_id"] == TENANT
