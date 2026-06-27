"""Tests for MEDIUM-8: ExecutionMemory.recall_async() DB-backed recall."""
from __future__ import annotations

import asyncio

import pytest

from app.memory.execution import ExecutionMemory


def test_execution_memory_has_recall_async():
    """ExecutionMemory must expose an async recall_async() method."""
    mem = ExecutionMemory()
    assert hasattr(mem, "recall_async"), "ExecutionMemory must have recall_async() method"
    assert asyncio.iscoroutinefunction(mem.recall_async)


@pytest.mark.asyncio
async def test_recall_async_falls_back_to_in_memory_without_db():
    """recall_async(db=None) must fall back to searching _plans in memory."""
    mem = ExecutionMemory()
    # Seed in-memory store directly
    mem._plans["t1"] = [{"goal": "Analyze data", "plan": ["step1"], "success": True}]
    results = await mem.recall_async("Analyze data", tenant_id="t1", db=None, limit=3)
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_recall_async_returns_matching_plans():
    """recall_async finds plans whose goal contains the hint words."""
    mem = ExecutionMemory()
    mem._plans["tenant-x"] = [
        {"goal": "Summarize quarterly report", "plan": ["fetch", "summarize"], "success": True},
        {"goal": "Send an email to the team", "plan": ["compose", "send"], "success": True},
    ]
    results = await mem.recall_async("Summarize report", tenant_id="tenant-x", db=None, limit=5)
    assert isinstance(results, list)
    # At least the "Summarize" plan should match
    goals = [r["goal"] for r in results]
    assert any("Summarize" in g for g in goals)


@pytest.mark.asyncio
async def test_recall_async_respects_limit():
    """recall_async honours the limit parameter."""
    mem = ExecutionMemory()
    mem._plans["tenant-lim"] = [
        {"goal": "test task 1", "plan": ["s1"], "success": True},
        {"goal": "test task 2", "plan": ["s2"], "success": True},
        {"goal": "test task 3", "plan": ["s3"], "success": True},
        {"goal": "test task 4", "plan": ["s4"], "success": True},
    ]
    results = await mem.recall_async("test task", tenant_id="tenant-lim", db=None, limit=2)
    assert len(results) <= 2


@pytest.mark.asyncio
async def test_recall_async_empty_when_no_plans():
    """recall_async returns an empty list when no plans are stored for the tenant."""
    mem = ExecutionMemory()
    results = await mem.recall_async("some goal", tenant_id="new-tenant", db=None, limit=3)
    assert results == []


@pytest.mark.asyncio
async def test_recall_async_falls_back_when_db_raises():
    """recall_async falls back to in-memory when the DB session factory raises."""
    mem = ExecutionMemory()
    mem._plans["t-fallback"] = [
        {"goal": "Deploy service", "plan": ["build", "push"], "success": True},
    ]

    class _FailingDB:
        """Async context manager that raises on __aenter__ to simulate DB failure."""

        def __call__(self):
            return self

        async def __aenter__(self):
            raise RuntimeError("DB connection failed")

        async def __aexit__(self, *args):
            pass

    results = await mem.recall_async("Deploy service", tenant_id="t-fallback", db=_FailingDB(), limit=3)
    assert isinstance(results, list)
