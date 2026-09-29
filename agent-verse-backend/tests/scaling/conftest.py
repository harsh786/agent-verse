"""Test-isolation hygiene for the scaling (Celery task) suite.

Several Celery maintenance tasks run their async body through
``app.scaling.tasks._run_async``, which builds a *fresh* event loop and then
disposes the module-level asyncpg engine (``app.db.session._engine``) before
closing that loop. If an earlier test in a full-suite run leaves that global
engine bound to an already-closed loop, the dispose step raises
``RuntimeError: Event loop is closed`` and the task falls into its error branch
(returning a zero count), which surfaces as a spurious, order-dependent failure
here — even though these tests pass in isolation and when the scaling suite runs
alone.

Resetting the lazily-rebuilt DB engine/session-factory singletons before each
test makes this suite hermetic against that foreign leakage: a stale engine from
a prior test can never be disposed against a dead loop because there is no stale
engine to begin with; the next access rebuilds one bound to the live loop.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _reset_db_engine_singletons() -> Iterator[None]:
    import app.db.session as _sess

    # Snapshot so we restore whatever the surrounding suite expected.
    prev_engine = _sess._engine
    prev_factory = _sess._session_factory
    _sess._engine = None
    _sess._session_factory = None
    try:
        yield
    finally:
        _sess._engine = prev_engine
        _sess._session_factory = prev_factory


@pytest.fixture(autouse=True)
def _goal_claim_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most run_goal tests exercise what happens AFTER the worker claims its goal.

    ``run_goal`` fails closed when it cannot atomically claim the goal row
    (``_claim_goal_for_execution``), and these unit tests have no real goals
    table — so the claim is stubbed as granted here. The claim itself, and
    run_goal's handling of a terminal / unverifiable claim, are tested in
    ``test_run_goal_claim.py`` (which restores the real function).
    """
    from app.scaling import tasks

    async def _claimed(goal_id: str, tenant_id: str) -> str:
        return "claimed"

    monkeypatch.setattr(tasks, "_claim_goal_for_execution", _claimed)
