"""a08-F200-01: a rollback reports only what it really undid.

``RollbackReport.record`` counted any non-``InverseResult`` return as rolled
back, so the ``register_typed`` placeholder (no inverse at all) was reported as
undone, and the sync ``rollback_all`` fire-and-forgot coroutine inverses while
listing them as rolled back.
"""

from __future__ import annotations

import pytest

from app.reliability.rollback import RollbackAction, RollbackEngine, RollbackReport
from app.reliability.tool_inverses import FAILED, ROLLED_BACK, SKIPPED, InverseResult


def test_typed_action_without_inverse_is_skipped_not_rolled_back() -> None:
    engine = RollbackEngine()
    engine.register_typed(action_type=RollbackAction.SEND_MESSAGE, action_description="ping")

    assert engine.rollback_all() == []
    report = engine.last_report
    assert report is not None
    assert report.rolled_back == []
    assert report.skipped == [
        {"action": "send_message:ping", "detail": "no inverse function provided"}
    ]


@pytest.mark.asyncio
async def test_typed_action_without_inverse_is_skipped_on_the_async_path() -> None:
    engine = RollbackEngine()
    engine.register_typed(action_type=RollbackAction.CREATE_TICKET, action_description="T-1")

    assert await engine.rollback_all_async() == []
    assert engine.last_report is not None
    assert engine.last_report.as_dict()["counts"] == {
        "rolled_back": 0,
        "skipped": 1,
        "failed": 0,
    }


def test_record_classifies_every_outcome() -> None:
    report = RollbackReport()
    report.record("a", InverseResult(ROLLED_BACK, ""))
    report.record("b", InverseResult(SKIPPED, "no id"))
    report.record("c", InverseResult(FAILED, "boom"))
    report.record("d", False)
    report.record("e", None)  # a legacy callable that completed without raising

    assert report.rolled_back == ["a", "e"]
    assert [x["action"] for x in report.skipped] == ["b"]
    assert [x["action"] for x in report.failed] == ["c", "d"]


def test_sync_rollback_runs_a_coroutine_inverse_to_completion_without_a_loop() -> None:
    engine = RollbackEngine()
    done: list[str] = []

    async def undo() -> InverseResult:
        done.append("undone")
        return InverseResult(ROLLED_BACK, "")

    async def undo_skipped() -> InverseResult:
        return InverseResult(SKIPPED, "nothing to delete")

    engine.register(action="first", inverse=undo)
    engine.register(action="second", inverse=undo_skipped)

    assert engine.rollback_all() == ["first"]
    assert done == ["undone"]
    assert engine.last_report is not None
    assert [x["action"] for x in engine.last_report.skipped] == ["second"]
