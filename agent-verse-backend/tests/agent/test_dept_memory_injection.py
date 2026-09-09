"""Regression test for the department-memory injection block in
``app/agent/graph.py``.

The planner-context injection maps each retrieved ``MemoryEntry`` to a small
dict. It previously read ``e.category`` — a field that does not exist on
``MemoryEntry`` — which raised ``AttributeError`` on every call once a department
had any entries. Because the whole block is wrapped in a non-fatal
``except Exception``, the failure was silent: department memory was simply never
injected into the agent's planning context in exactly the case that matters
(entries present). These tests pin the real ``MemoryEntry`` schema and exercise
the exact mapping so the bug cannot silently return.
"""
from __future__ import annotations

import inspect

import pytest

from app.memory.dept_memory import DepartmentMemory, MemoryEntry


def _inject_like_graph(entries: list[MemoryEntry]) -> list[dict[str, object]]:
    """Replicate the mapping performed in app/agent/graph.py's dept-memory block."""
    return [
        {"content": e.content, "confidence": e.confidence, "tags": e.tags}
        for e in entries
    ]


def test_memory_entry_has_no_category_field() -> None:
    """MemoryEntry has no ``category``; ``tags`` is the categorization field."""
    fields = set(MemoryEntry.__dataclass_fields__)
    assert "category" not in fields, (
        "MemoryEntry gained a 'category' field — update graph.py's dept-memory "
        "mapping intentionally if so"
    )
    assert "tags" in fields
    assert {"content", "confidence"} <= fields


@pytest.mark.asyncio
async def test_dept_memory_injection_mapping_succeeds_with_entries() -> None:
    """The real retrieve()->map path yields dicts without raising AttributeError."""
    mem = DepartmentMemory()
    await mem.add(
        dept_id="engineering",
        org_id="org1",
        tenant_id="t1",
        content="The service runs on FastAPI and Postgres.",
        source="agent-1",
        confidence=0.9,
        tags=["stack", "infra"],
    )

    entries = await mem.retrieve(dept_id="engineering", query="What stack do we use?")
    assert entries, "expected at least one retrieved entry (the case the bug hid)"

    mapped = _inject_like_graph(entries)
    assert mapped
    for item in mapped:
        assert set(item) == {"content", "confidence", "tags"}
        assert isinstance(item["content"], str) and item["content"]
        assert isinstance(item["confidence"], float)
        assert isinstance(item["tags"], list)


def test_graph_dept_memory_block_does_not_read_category() -> None:
    """Guard the source: the injection block must not reference ``e.category``."""
    from app.agent import graph

    src = inspect.getsource(graph)
    start = src.find('_org_ctx["dept_memory"]')
    assert start != -1, "dept_memory injection block not found in graph.py"
    block = src[start : start + 400]
    assert "e.category" not in block, (
        "graph.py dept-memory injection reads e.category again — MemoryEntry has "
        "no such field, so this AttributeErrors and is silently swallowed"
    )
    assert "e.tags" in block
