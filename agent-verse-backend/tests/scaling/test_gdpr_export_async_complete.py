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

    def __init__(self, goals: int, audit: int, audit_error: Exception | None = None) -> None:
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

    def _page(self, rows: list[tuple[Any, ...]], params: dict[str, Any]) -> _Result:
        assert params["tid"] == TENANT
        if "c_at" in params:
            rows = [r for r in rows if (r[-1], r[0]) > (params["c_at"], params["c_id"])]
        rows = sorted(rows, key=lambda r: (r[-1], r[0]))
        return _Result(rows[: params["lim"]])

    def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(stmt)
        params = params or {}
        self.sql.append((sql, params))
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
        if "UPDATE gdpr_export_jobs" in sql:
            self.job_updates.append({"sql": sql, **params})
        return _Result()


class _Session:
    def __init__(self, tables: _Tables) -> None:
        self._t = tables

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        return self._t.execute(stmt, params)

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
