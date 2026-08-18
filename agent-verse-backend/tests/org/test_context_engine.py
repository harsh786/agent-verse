"""Tests for ContextEngine — app/org/context_engine.py"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from app.org.context_engine import ContextEngine, OptimizedContext


@pytest.mark.asyncio
async def test_context_engine_builds_context():
    engine = ContextEngine()
    ctx = await engine.build_context(
        agent_id="agent-1",
        task={"type": "research", "query": "top competitors"},
        mission_id="mission-1",
        org_id="org1",
        tenant_id="t1",
    )
    assert isinstance(ctx, OptimizedContext)


@pytest.mark.asyncio
async def test_context_engine_respects_token_budget():
    engine = ContextEngine()
    ctx = await engine.build_context(
        agent_id="agent-1",
        task={"type": "research"},
        mission_id="mission-1",
        org_id="org1",
        tenant_id="t1",
        max_tokens=2000,
    )
    assert ctx.total_tokens <= 2000


@pytest.mark.asyncio
async def test_context_engine_deduplicates():
    engine = ContextEngine()
    ctx = await engine.build_context(
        agent_id="agent-1",
        task={"type": "standard"},
        mission_id="mission-1",
        org_id="org1",
        tenant_id="t1",
    )
    # No duplicate items
    contents = [item.content for item in ctx.items if hasattr(item, "content")]
    assert len(contents) == len(set(contents))


@pytest.mark.asyncio
async def test_context_engine_includes_provenance():
    engine = ContextEngine()
    ctx = await engine.build_context(
        agent_id="agent-1",
        task={"type": "code", "language": "python"},
        mission_id="mission-1",
        org_id="org1",
        tenant_id="t1",
    )
    assert isinstance(ctx.provenance, list)


@pytest.mark.asyncio
async def test_context_engine_coding_strategy():
    engine = ContextEngine()
    ctx = await engine.build_context(
        agent_id="agent-1",
        task={"type": "code_generation"},
        mission_id="m1",
        org_id="org1",
        tenant_id="t1",
        strategy="code",
    )
    assert ctx.strategy == "code"
    assert ctx.total_tokens <= 8000
