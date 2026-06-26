"""Tests for persistence adapter (no real DB needed — tests graceful no-op behavior)."""
from __future__ import annotations

import pytest

from app.services.persistence import persist_audit_event, persist_goal, persist_goal_status


async def test_persist_goal_noop_when_no_session_factory() -> None:
    """persist_goal silently returns when db_session_factory is None."""
    await persist_goal(
        goal_id="g1",
        tenant_id="t1",
        goal_text="test",
        status="planning",
        priority="normal",
        dry_run=False,
        db_session_factory=None,
    )
    # No exception raised — function is a clean no-op


async def test_persist_goal_status_noop_when_no_session_factory() -> None:
    """persist_goal_status silently returns when db_session_factory is None."""
    await persist_goal_status(
        goal_id="g1",
        tenant_id="t1",
        status="complete",
        db_session_factory=None,
    )
    # No exception raised


async def test_persist_audit_event_noop_when_no_session_factory() -> None:
    """persist_audit_event silently returns when db_session_factory is None."""
    await persist_audit_event(
        goal_id="g1",
        tool_name="github",
        action_level="allow_log",
        outcome="step_complete",
        step_id="s1",
        tenant_id="t1",
        db_session_factory=None,
    )
    # No exception raised


async def test_persist_goal_handles_broken_factory_gracefully() -> None:
    """When the factory returns a broken context manager, persist_goal swallows the error."""
    from contextlib import asynccontextmanager

    @asynccontextmanager  # type: ignore[arg-type]
    async def _broken_factory():  # type: ignore[return]
        raise RuntimeError("DB not available")
        yield  # noqa: unreachable — required for asynccontextmanager

    # Should not propagate the RuntimeError
    await persist_goal(
        goal_id="g2",
        tenant_id="t2",
        goal_text="test",
        status="planning",
        priority="high",
        dry_run=True,
        db_session_factory=_broken_factory,
    )


async def test_persist_goal_status_handles_broken_factory_gracefully() -> None:
    """When the factory raises, persist_goal_status swallows the error."""
    from contextlib import asynccontextmanager

    @asynccontextmanager  # type: ignore[arg-type]
    async def _broken_factory():  # type: ignore[return]
        raise RuntimeError("DB not available")
        yield  # noqa: unreachable

    await persist_goal_status(
        goal_id="g2",
        tenant_id="t2",
        status="failed",
        error_message="something went wrong",
        db_session_factory=_broken_factory,
    )


async def test_persist_audit_event_handles_broken_factory_gracefully() -> None:
    """When the factory raises, persist_audit_event swallows the error."""
    from contextlib import asynccontextmanager

    @asynccontextmanager  # type: ignore[arg-type]
    async def _broken_factory():  # type: ignore[return]
        raise RuntimeError("DB not available")
        yield  # noqa: unreachable

    await persist_audit_event(
        goal_id="g2",
        tool_name="slack.post",
        action_level="allow",
        outcome="success",
        step_id="s2",
        tenant_id="t2",
        db_session_factory=_broken_factory,
    )
