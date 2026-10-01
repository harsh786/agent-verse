"""Unit tests for the workflow engine lifecycle audit (WF-ENGINE-AUDIT)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.workflow.engine_audit import (
    audit_tenant_id,
    run_event,
    step_event,
    write_engine_audit,
)


@pytest.mark.parametrize(
    ("old", "new", "event"),
    [
        ("pending", "running", "started"),
        (None, "running", "started"),
        ("paused", "running", "resumed"),
        ("waiting_hitl", "running", "resumed"),
        ("running", "running", None),
        ("running", "waiting_hitl", "waiting_approval"),
        ("running", "paused", "paused"),
        ("running", "complete", "completed"),
        ("running", "failed", "failed"),
        ("waiting_hitl", "cancelled", "cancelled"),
        ("cancelled", "cancelled", None),
    ],
)
def test_run_event_mapping(old: str | None, new: str, event: str | None) -> None:
    assert run_event(old, new) == event


def test_step_event_mapping() -> None:
    assert step_event("running") == "started"
    assert step_event("complete") == "completed"
    assert step_event("failed") == "failed"
    assert step_event("pending") is None


def test_audit_tenant_id_is_the_hex_form() -> None:
    tid = uuid.uuid4()
    assert audit_tenant_id(str(tid)) == tid.hex
    assert audit_tenant_id(tid.hex) == tid.hex
    assert audit_tenant_id("not-a-uuid") == "not-a-uuid"


class _BrokenSession:
    def begin_nested(self) -> Any:
        raise RuntimeError("savepoint unavailable")


async def test_audit_failure_never_raises() -> None:
    # The run's status write must commit even when the audit insert cannot.
    await write_engine_audit(
        _BrokenSession(), tenant_id="t1", run_id="r1", kind="run", event="started"
    )
