"""Tests for org memory — DepartmentMemory + org tier."""
from __future__ import annotations

import pytest

from app.memory.dept_memory import DepartmentMemory


@pytest.mark.asyncio
async def test_dept_memory_add_and_retrieve():
    mem = DepartmentMemory()
    entry = await mem.add(
        content="Company's ICP is mid-market SaaS, 50-200 employees",
        source="agent-123",
        confidence=0.90,
        dept_id="marketing",
        org_id="org1",
        tenant_id="t1",
    )
    assert entry.entry_id
    assert entry.confidence >= 0.70

    results = await mem.retrieve(
        query="Who is our ideal customer?",
        dept_id="marketing",
    )
    assert len(results) >= 1
    assert any("ICP" in r.content or "mid-market" in r.content for r in results)


@pytest.mark.asyncio
async def test_dept_memory_low_confidence_rejected():
    """Memories with confidence < 0.70 should not be promoted."""
    mem = DepartmentMemory()
    with pytest.raises(ValueError, match="confidence"):
        await mem.add(
            content="Some unreliable fact",
            source="agent-456",
            confidence=0.50,   # below threshold
            dept_id="eng",
            org_id="org1",
            tenant_id="t1",
        )


@pytest.mark.asyncio
async def test_dept_memory_deprecate():
    """Deprecated memories should not appear in results."""
    mem = DepartmentMemory()
    entry = await mem.add(
        content="Old stack is Java monolith",
        source="agent-789",
        confidence=0.85,
        dept_id="engineering",
        org_id="org1",
        tenant_id="t1",
    )
    await mem.deprecate(
        dept_id="engineering", entry_id=entry.entry_id, reason="Stack migrated to FastAPI"
    )
    results = await mem.retrieve(dept_id="engineering", query="What is the tech stack?")
    assert not any(r.entry_id == entry.entry_id for r in results)


@pytest.mark.asyncio
async def test_dept_memory_cross_dept_isolation():
    """Marketing memory not visible to Engineering."""
    mem = DepartmentMemory()
    await mem.add(
        content="Marketing secret campaign A",
        source="agent-mk",
        confidence=0.92,
        dept_id="marketing",
        org_id="org1",
        tenant_id="t1",
    )
    eng_results = await mem.retrieve("campaign", "engineering", "org1", "t1")
    assert all("secret campaign A" not in r.content for r in eng_results)
