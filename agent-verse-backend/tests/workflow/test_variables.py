"""Tests for WorkflowVariableStore."""
from __future__ import annotations

import pytest

from app.workflow.variables import WorkflowVariableStore


@pytest.fixture
def store() -> WorkflowVariableStore:
    return WorkflowVariableStore()


def _state(vars_: dict | None = None) -> dict:
    return {"vars": vars_ or {}}


def test_set_new_var(store: WorkflowVariableStore) -> None:
    state = _state()
    updates = store.set(state, "x", 42)  # type: ignore[arg-type]
    assert updates["vars"]["x"] == 42


def test_set_overwrites(store: WorkflowVariableStore) -> None:
    state = _state({"x": 1})
    updates = store.set(state, "x", 99)  # type: ignore[arg-type]
    assert updates["vars"]["x"] == 99


def test_get_existing(store: WorkflowVariableStore) -> None:
    state = _state({"y": "hello"})
    assert store.get(state, "y") == "hello"  # type: ignore[arg-type]


def test_get_missing_returns_none(store: WorkflowVariableStore) -> None:
    state = _state()
    assert store.get(state, "missing") is None  # type: ignore[arg-type]


def test_get_missing_returns_default(store: WorkflowVariableStore) -> None:
    state = _state()
    assert store.get(state, "missing", default=0) == 0  # type: ignore[arg-type]


def test_set_preserves_existing_vars(store: WorkflowVariableStore) -> None:
    state = _state({"a": 1, "b": 2})
    updates = store.set(state, "c", 3)  # type: ignore[arg-type]
    assert updates["vars"]["a"] == 1
    assert updates["vars"]["b"] == 2
    assert updates["vars"]["c"] == 3


def test_all_vars(store: WorkflowVariableStore) -> None:
    state = _state({"x": 1, "y": "z"})
    result = store.get_all(state)  # type: ignore[arg-type]
    assert result == {"x": 1, "y": "z"}


def test_all_vars_empty(store: WorkflowVariableStore) -> None:
    state = _state()
    result = store.get_all(state)  # type: ignore[arg-type]
    assert result == {}


def test_set_returns_dict_with_vars_key(store: WorkflowVariableStore) -> None:
    state = _state({"x": 1, "y": 2})
    updates = store.set(state, "z", 3)  # type: ignore[arg-type]
    assert "vars" in updates
    assert updates["vars"]["z"] == 3
    # Original vars preserved
    assert updates["vars"]["x"] == 1


def test_get_all_returns_copy(store: WorkflowVariableStore) -> None:
    state = _state({"a": 1})
    result = store.get_all(state)  # type: ignore[arg-type]
    result["b"] = 2  # mutate the copy
    # Original state unchanged
    assert "b" not in (state.get("vars") or {})
