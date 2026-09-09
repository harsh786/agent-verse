"""Tests for working memory."""
import pytest

from app.memory.working_memory import WorkingMemory


class TestWorkingMemory:
    def test_push_and_snapshot(self) -> None:
        wm = WorkingMemory(capacity=5)
        wm.push("item one", source="tool")
        wm.push("item two", source="rag")
        snap = wm.snapshot()
        assert len(snap) == 2
        assert snap[0].content == "item one"
        assert snap[1].content == "item two"

    def test_eviction_at_capacity(self) -> None:
        wm = WorkingMemory(capacity=3)
        wm.push("first")
        wm.push("second")
        wm.push("third")
        wm.push("fourth")  # should evict "first"
        snap = wm.snapshot()
        assert len(snap) == 3
        contents = [item.content for item in snap]
        assert "first" not in contents
        assert "fourth" in contents

    def test_clear(self) -> None:
        wm = WorkingMemory(capacity=5)
        wm.push("a")
        wm.push("b")
        wm.clear()
        assert len(wm) == 0
        assert wm.snapshot() == []

    def test_most_recent(self) -> None:
        wm = WorkingMemory(capacity=10)
        for i in range(5):
            wm.push(f"item {i}")
        recent = wm.most_recent(3)
        assert len(recent) == 3
        # Most recent first
        assert recent[0].content == "item 4"

    def test_format_for_prompt(self) -> None:
        wm = WorkingMemory(capacity=5)
        wm.push("Python is great", source="rag")
        prompt_text = wm.format_for_prompt()
        assert "Python is great" in prompt_text
        assert "rag" in prompt_text

    def test_invalid_capacity_raises(self) -> None:
        with pytest.raises(ValueError):
            WorkingMemory(capacity=0)

    def test_snapshot_as_dicts(self) -> None:
        wm = WorkingMemory(capacity=3)
        wm.push("hello", source="test", metadata={"key": "val"})
        dicts = wm.snapshot_as_dicts()
        assert len(dicts) == 1
        assert dicts[0]["content"] == "hello"
        assert dicts[0]["source"] == "test"
