"""Tests for Phase 6: SelfImprovementEngine.process_feedback_batch."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_process_feedback_batch_no_db_returns_zeros() -> None:
    from app.evals.self_improvement_engine import SelfImprovementEngine

    engine = SelfImprovementEngine()
    result = await engine.process_feedback_batch(
        db_session_factory=None,
        tenant_id="tenant-1",
    )
    assert result == {"processed": 0, "actions_derived": 0}


@pytest.mark.asyncio
async def test_process_feedback_batch_tolerates_bad_db() -> None:
    from app.evals.self_improvement_engine import SelfImprovementEngine

    async def bad_factory() -> None:
        raise RuntimeError("DB unavailable")

    engine = SelfImprovementEngine()
    result = await engine.process_feedback_batch(
        db_session_factory=bad_factory,
        tenant_id="tenant-1",
    )
    # Should return zeros rather than raising
    assert isinstance(result, dict)
    assert "processed" in result


class _FakeResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def fetchall(self) -> list:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list, log: list, fail_ltm: bool) -> None:
        self._rows = rows
        self._log = log
        self._fail_ltm = fail_ltm

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    def begin(self) -> _FakeSession:
        return self

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self._log.append("COMMIT")

    async def execute(self, stmt: object, params: dict | None = None) -> _FakeResult:
        sql = str(stmt)
        self._log.append(sql)
        if "INSERT INTO long_term_memory" in sql and self._fail_ltm:
            raise RuntimeError("ltm insert failed")
        if sql.startswith("SELECT id, goal_id"):
            return _FakeResult(self._rows)
        return _FakeResult([])


def _factory(rows: list, log: list, *, fail_ltm: bool = False):  # type: ignore[no-untyped-def]
    def factory() -> _FakeSession:
        return _FakeSession(rows, log, fail_ltm)

    return factory


@pytest.mark.asyncio
async def test_low_rating_feedback_stores_a_real_ltm_lesson() -> None:
    """The lesson reaches long_term_memory (was: nonexistent module, error swallowed)."""
    from types import SimpleNamespace

    from app.evals.self_improvement_engine import SelfImprovementEngine

    rows = [SimpleNamespace(id="f1", goal_id="g1", rating=1, feedback_text="use the v2 API")]
    log: list = []
    result = await SelfImprovementEngine().process_feedback_batch(
        db_session_factory=_factory(rows, log), tenant_id="t-si"
    )

    assert result == {"processed": 1, "actions_derived": 1}
    inserts = [s for s in log if "INSERT INTO long_term_memory" in s]
    assert len(inserts) == 1
    assert any("UPDATE goal_feedback SET processed_at" in s for s in log)


@pytest.mark.asyncio
async def test_failed_lesson_store_is_not_counted_nor_marked_processed() -> None:
    from types import SimpleNamespace

    from app.evals.self_improvement_engine import SelfImprovementEngine

    rows = [SimpleNamespace(id="f1", goal_id="g1", rating=1, feedback_text="use the v2 API")]
    log: list = []
    result = await SelfImprovementEngine().process_feedback_batch(
        db_session_factory=_factory(rows, log, fail_ltm=True), tenant_id="t-si"
    )

    assert result == {"processed": 0, "actions_derived": 0}
    assert not any("UPDATE goal_feedback SET processed_at" in s for s in log)
