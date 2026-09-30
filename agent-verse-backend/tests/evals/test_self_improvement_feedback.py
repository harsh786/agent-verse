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


# ── MEM-23: idempotent lessons, shared embedder, SKIP LOCKED, drained batches ──


class _RecordingSession(_FakeSession):
    """Also records statement params; SELECT pages through ``pages``."""

    def __init__(self, state: dict, log: list) -> None:
        super().__init__([], log, False)
        self._state = state

    async def execute(self, stmt: object, params: dict | None = None) -> _FakeResult:
        sql = str(stmt)
        self._log.append(sql)
        self._state.setdefault("params", []).append((sql, dict(params or {})))
        if sql.lstrip().startswith("SELECT id, goal_id"):
            pages = self._state["pages"]
            return _FakeResult(pages.pop(0) if pages else [])
        return _FakeResult([])


def _recording_factory(state: dict, log: list):  # type: ignore[no-untyped-def]
    def factory() -> _RecordingSession:
        return _RecordingSession(state, log)

    return factory


def _row(fid: str, rating: int = 1):  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    return SimpleNamespace(id=fid, goal_id="g1", rating=rating, feedback_text=f"fix {fid}")


@pytest.mark.asyncio
async def test_reprocessing_the_same_feedback_row_stores_one_lesson() -> None:
    """A failed outer commit re-processes the row; the lesson id is deterministic
    (ON CONFLICT) so the second store is the same row, not a duplicate."""
    from app.evals.self_improvement_engine import SelfImprovementEngine

    ids = []
    for _attempt in range(2):
        state: dict = {"pages": [[_row("f1")]]}
        await SelfImprovementEngine().process_feedback_batch(
            db_session_factory=_recording_factory(state, []), tenant_id="t-si"
        )
        ids += [
            p["id"] for sql, p in state["params"] if "INSERT INTO long_term_memory" in sql
        ]
    assert len(ids) == 2 and ids[0] == ids[1]


@pytest.mark.asyncio
async def test_feedback_selection_skips_locked_rows_and_drains_batches() -> None:
    from app.evals.self_improvement_engine import SelfImprovementEngine

    state: dict = {
        "pages": [[_row("a", 5), _row("b", 5)], [_row("c", 5), _row("d", 5)], [_row("e", 5)]]
    }
    out = await SelfImprovementEngine().process_feedback_batch(
        db_session_factory=_recording_factory(state, []), tenant_id="t-si", batch_size=2
    )
    assert out["processed"] == 5
    selects = [sql for sql, _ in state["params"] if sql.lstrip().startswith("SELECT id, goal_id")]
    assert len(selects) == 3
    assert all("FOR UPDATE SKIP LOCKED" in s for s in selects)


@pytest.mark.asyncio
async def test_feedback_run_stops_at_the_per_run_cap() -> None:
    from app.evals.self_improvement_engine import SelfImprovementEngine

    state: dict = {"pages": [[_row(f"r{i}-{j}", 5) for j in range(2)] for i in range(10)]}
    out = await SelfImprovementEngine().process_feedback_batch(
        db_session_factory=_recording_factory(state, []), tenant_id="t-si",
        batch_size=2, max_rows=5,
    )
    assert out["processed"] <= 6 and len(state["pages"]) >= 7


@pytest.mark.asyncio
async def test_lessons_are_embedded_with_the_shared_embedder() -> None:
    from app.evals.self_improvement_engine import SelfImprovementEngine
    from app.providers.base import EmbedResponse

    class _Embedder:
        calls = 0

        async def embed(self, request: object) -> EmbedResponse:
            _Embedder.calls += 1
            return EmbedResponse(embeddings=[[0.1] * 8], model="m")

    state: dict = {"pages": [[_row("f1")]]}
    await SelfImprovementEngine().process_feedback_batch(
        db_session_factory=_recording_factory(state, []), tenant_id="t-si", embedder=_Embedder()
    )
    assert _Embedder.calls == 1
