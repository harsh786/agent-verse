"""Tests for ContextEngine — app/org/context_engine.py"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.org.context_engine import ContextBuildRequest, ContextEngine, OptimizedContext


def _fake_session() -> AsyncMock:
    """A session double whose `execute()` always yields an empty result set.

    ContextEngine's DB-backed sources (mission, recent decisions, role
    guidelines) all degrade gracefully to an empty list when nothing is
    found — this lets tests exercise the real build_context() pipeline
    without a live database.
    """
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none = MagicMock(return_value=None)
    result_mock.scalars.return_value.all = MagicMock(return_value=[])
    session.execute = AsyncMock(return_value=result_mock)
    return session


@pytest.mark.asyncio
async def test_context_engine_builds_context():
    engine = ContextEngine(session=_fake_session())
    ctx = await engine.build_context(
        ContextBuildRequest(
            agent_id="agent-1",
            task_description="top competitors research",
            mission_id="mission-1",
            dept_id=None,
            org_id="org1",
            tenant_id="t1",
        )
    )
    assert isinstance(ctx, OptimizedContext)


@pytest.mark.asyncio
async def test_context_engine_respects_token_budget():
    engine = ContextEngine(session=_fake_session())
    ctx = await engine.build_context(
        ContextBuildRequest(
            agent_id="agent-1",
            task_description="research",
            mission_id="mission-1",
            dept_id=None,
            org_id="org1",
            tenant_id="t1",
            max_tokens=2000,
        )
    )
    assert ctx.total_tokens <= 2000


@pytest.mark.asyncio
async def test_context_engine_deduplicates():
    engine = ContextEngine(session=_fake_session())
    ctx = await engine.build_context(
        ContextBuildRequest(
            agent_id="agent-1",
            task_description="standard task",
            mission_id="mission-1",
            dept_id=None,
            org_id="org1",
            tenant_id="t1",
        )
    )
    # No duplicate items
    contents = [item.content for item in ctx.items]
    assert len(contents) == len(set(contents))


@pytest.mark.asyncio
async def test_context_engine_includes_provenance():
    engine = ContextEngine(session=_fake_session())
    ctx = await engine.build_context(
        ContextBuildRequest(
            agent_id="agent-1",
            task_description="code task in python",
            mission_id="mission-1",
            dept_id=None,
            org_id="org1",
            tenant_id="t1",
        )
    )
    assert isinstance(ctx.provenance, list)


@pytest.mark.asyncio
async def test_context_engine_coding_strategy():
    engine = ContextEngine(session=_fake_session())
    ctx = await engine.build_context(
        ContextBuildRequest(
            agent_id="agent-1",
            task_description="code_generation task",
            mission_id="m1",
            dept_id=None,
            org_id="org1",
            tenant_id="t1",
            strategy="code",
        )
    )
    assert ctx.strategy_used == "code"
    assert ctx.total_tokens <= 8000
