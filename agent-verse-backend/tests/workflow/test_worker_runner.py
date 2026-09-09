"""WT-E2 completeness: a Celery worker must execute workflows with a DB-backed
runner, not the in-memory app.state fallback (MemorySaver + mock tools + no
persistence). ``_get_runner`` must detect the in-memory fallback and build a
worker-local DB-backed runner instead."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.workflow.celery_tasks as ct


@pytest.fixture(autouse=True)
def _reset_worker_runner() -> None:
    ct._WORKER_RUNNER = None
    yield
    ct._WORKER_RUNNER = None


def test_get_runner_prefers_db_backed_state_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import app

    db_backed = SimpleNamespace(_run_store=object())
    monkeypatch.setattr(app.state, "workflow_runner", db_backed, raising=False)
    assert ct._get_runner() is db_backed


def test_get_runner_builds_worker_runner_when_state_is_in_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.main import app

    # The in-memory fallback runner has no run store.
    in_memory = SimpleNamespace(_run_store=None)
    monkeypatch.setattr(app.state, "workflow_runner", in_memory, raising=False)

    runner = ct._get_runner()
    # A fresh DB-backed runner is built (not the in-memory one), and it persists.
    assert runner is not in_memory
    assert getattr(runner, "_run_store", None) is not None
    assert getattr(runner, "_celery", None) is not None  # dispatches/records via Celery app


def test_worker_runner_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import app

    monkeypatch.setattr(
        app.state, "workflow_runner", SimpleNamespace(_run_store=None), raising=False
    )
    first = ct._get_runner()
    second = ct._get_runner()
    assert first is second  # built once per worker process
