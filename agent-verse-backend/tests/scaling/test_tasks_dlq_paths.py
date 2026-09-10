"""Direct coverage for _update_goal_dlq async helper in app/scaling/tasks.py.

Covers lines 464-481 (the async DB update path including the exception handler
that logs and swallows DB failures). Also covers _run_async exception path
when _update_goal_dlq raises inside the event loop.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scaling import tasks

# ── _update_goal_dlq success path ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_goal_dlq_success_path() -> None:
    """_update_goal_dlq exercises the DB update path with mocked session."""
    fake_session_module = MagicMock()
    fake_session_module.update = MagicMock()
    fake_session_module.get_session_factory = MagicMock(
        return_value=MagicMock()  # the session factory callable
    )

    fake_models_module = MagicMock()
    fake_models_module.Goal = MagicMock(name="Goal-model")

    fake_db_rls_module = MagicMock()
    fake_system_session_cm = MagicMock()
    fake_system_session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    fake_system_session_cm.__aexit__ = AsyncMock(return_value=None)
    fake_db_rls_module.system_session = MagicMock(return_value=fake_system_session_cm)

    fake_sqlalchemy_module = MagicMock()
    fake_sqlalchemy_module.update = MagicMock(return_value=MagicMock(name="update-stmt"))

    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    session_cm.__aexit__ = AsyncMock(return_value=None)
    session_factory = MagicMock(return_value=session_cm)
    fake_session_module.get_session_factory = MagicMock(return_value=session_factory)

    with patch.dict(
        "sys.modules",
        {
            "app.db.session": fake_session_module,
            "app.db.models.goal": fake_models_module,
            "app.db.rls": fake_db_rls_module,
            "sqlalchemy": fake_sqlalchemy_module,
        },
    ):
        # Should not raise
        await tasks._update_goal_dlq("goal-1", "tenant-1", "dead-lettered")


# ── _update_goal_dlq exception path (lines 485-486, 489) ─────────────────────


@pytest.mark.asyncio
async def test_update_goal_dlq_swallows_db_exception() -> None:
    """_update_goal_dlq catches and logs DB exceptions instead of propagating."""

    # Make get_session_factory raise when called (simulates DB unavailable)
    fake_session_module = MagicMock()
    fake_session_module.get_session_factory = MagicMock(
        side_effect=RuntimeError("DB unreachable")
    )
    fake_models_module = MagicMock()
    fake_db_rls_module = MagicMock()
    fake_sqlalchemy_module = MagicMock()

    with patch.dict(
        "sys.modules",
        {
            "app.db.session": fake_session_module,
            "app.db.models.goal": fake_models_module,
            "app.db.rls": fake_db_rls_module,
            "sqlalchemy": fake_sqlalchemy_module,
        },
    ):
        # Should swallow the exception and not raise
        await tasks._update_goal_dlq("goal-1", "tenant-1", "DLQ reason")


@pytest.mark.asyncio
async def test_update_goal_dlq_swallows_session_execution_exception() -> None:
    """_update_goal_dlq catches and logs exceptions during session.execute."""
    # Build mocks where session.execute raises
    session = MagicMock()
    session.execute = AsyncMock(side_effect=RuntimeError("asyncpg connection dropped"))
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=None)

    session_factory = MagicMock(return_value=session_cm)
    fake_session_module = MagicMock()
    fake_session_module.get_session_factory = MagicMock(return_value=session_factory)

    # system_session returns an async context manager too
    fake_system_session_cm = MagicMock()
    fake_system_session_cm.__aenter__ = AsyncMock(return_value=None)
    fake_system_session_cm.__aexit__ = AsyncMock(return_value=None)
    fake_db_rls_module = MagicMock()
    fake_db_rls_module.system_session = MagicMock(return_value=fake_system_session_cm)

    fake_models_module = MagicMock()
    fake_sqlalchemy_module = MagicMock()
    fake_sqlalchemy_module.update = MagicMock(return_value=MagicMock())

    with patch.dict(
        "sys.modules",
        {
            "app.db.session": fake_session_module,
            "app.db.models.goal": fake_models_module,
            "app.db.rls": fake_db_rls_module,
            "sqlalchemy": fake_sqlalchemy_module,
        },
    ):
        # Should swallow the asyncpg exception
        await tasks._update_goal_dlq("goal-2", "tenant-2", "max retries exceeded")


# ── _run_async with _update_goal_dlq DB exception (lines 485-486 indirectly) ─


def test_run_goal_dlq_with_db_update_exception_still_succeeds() -> None:
    """DLQ handler logs DB update failure but still returns the dead-lettered status."""
    from app.scaling.tasks import run_goal_dlq

    # Force _run_async to propagate the underlying exception
    with patch("app.scaling.tasks._run_async", side_effect=RuntimeError("loop error")):
        result = run_goal_dlq.run(
            goal_id="goal-x",
            tenant_id="tenant-x",
            reason="bad state",
        )
    # The runtime exception is caught by the outer try/except in run_goal_dlq
    assert result["status"] == "dead_lettered"
    assert result["goal_id"] == "goal-x"
    assert result["tenant_id"] == "tenant-x"
    assert result["reason"] == "bad state"
