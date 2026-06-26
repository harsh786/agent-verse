"""Tests for enhanced RollbackEngine with typed actions and preview."""
from __future__ import annotations

import logging

import pytest

from app.reliability.rollback import RollbackAction, RollbackEngine


def test_register_and_rollback_all():
    engine = RollbackEngine()
    log: list[str] = []
    engine.register(action="step1", inverse=lambda: log.append("undo-step1"))
    engine.register(action="step2", inverse=lambda: log.append("undo-step2"))
    rolled = engine.rollback_all()
    assert log == ["undo-step2", "undo-step1"]  # LIFO
    assert len(engine) == 0


def test_rollback_all_returns_action_names():
    engine = RollbackEngine()
    engine.register(action="create_branch", inverse=lambda: None)
    engine.register(action="create_pr", inverse=lambda: None)
    rolled = engine.rollback_all()
    assert rolled == ["create_pr", "create_branch"]


def test_register_typed_no_inverse_logs_warning(caplog):
    engine = RollbackEngine()
    with caplog.at_level(logging.WARNING):
        engine.register_typed(
            action_type=RollbackAction.SEND_MESSAGE,
            action_description="send slack notification",
        )
        engine.rollback_all()
    assert "no inverse function provided" in caplog.text


def test_register_typed_with_inverse_executes():
    engine = RollbackEngine()
    executed: list[str] = []
    engine.register_typed(
        action_type=RollbackAction.CREATE_BRANCH,
        action_description="feature/my-branch",
        inverse_fn=lambda: executed.append("deleted-branch"),
    )
    rolled = engine.rollback_all()
    assert executed == ["deleted-branch"]
    assert len(rolled) == 1
    assert "create_branch:feature/my-branch" in rolled[0]


def test_preview_returns_actions_without_executing():
    engine = RollbackEngine()
    executed: list[int] = []
    engine.register(action="op1", inverse=lambda: executed.append(1))
    engine.register(action="op2", inverse=lambda: executed.append(2))
    preview = engine.preview()
    assert preview == ["op2", "op1"]  # Reversed (LIFO order)
    assert executed == []  # Not executed
    assert len(engine) == 2  # Still registered


def test_rollback_error_does_not_abort_remaining(caplog):
    """If one inverse raises, rollback continues with remaining actions."""
    engine = RollbackEngine()
    log: list[str] = []

    def failing_inverse() -> None:
        raise RuntimeError("boom")

    engine.register(action="first", inverse=lambda: log.append("undone-first"))
    engine.register(action="second", inverse=failing_inverse)
    engine.register(action="third", inverse=lambda: log.append("undone-third"))

    with caplog.at_level(logging.ERROR):
        rolled = engine.rollback_all()

    # "third" and "first" should be rolled back; "second" failed
    assert "undone-third" in log
    assert "undone-first" in log
    assert "Rollback failed for 'second'" in caplog.text
    # Only successful rollbacks are listed
    assert "third" in rolled
    assert "first" in rolled
    assert "second" not in rolled


def test_rollback_action_enum_values():
    assert RollbackAction.CREATE_FILE == "create_file"
    assert RollbackAction.SEND_MESSAGE == "send_message"
    assert RollbackAction.CUSTOM == "custom"


def test_len_tracks_stack_depth():
    engine = RollbackEngine()
    assert len(engine) == 0
    engine.register(action="a", inverse=lambda: None)
    engine.register(action="b", inverse=lambda: None)
    assert len(engine) == 2
    engine.rollback_all()
    assert len(engine) == 0
