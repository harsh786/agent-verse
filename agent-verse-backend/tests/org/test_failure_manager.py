"""Tests for the 5-class failure taxonomy — app/org/failure_manager.py"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.org.failure_manager import (
    DLQEntry,
    FailureClass,
    FailureClassifier,
    FailureEvent,
    OrgDLQ,
    OrgFailureManager,
)


def _failure(**overrides) -> FailureEvent:
    base = dict(
        failure_id="f1",
        failure_class=FailureClass.TRANSIENT,
        error_message="boom",
        context={},
        org_id="org1",
        tenant_id="t1",
    )
    base.update(overrides)
    return FailureEvent(**base)


# ── FailureClassifier ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "error_type,expected",
    [
        ("Timeout while calling API", FailureClass.TRANSIENT),
        ("rate_limit exceeded", FailureClass.TRANSIENT),
        ("quality_below_threshold on output", FailureClass.DEGRADED),
        ("approval_timeout waiting on human", FailureClass.BLOCKED),
        ("infinite_loop detected in planner", FailureClass.FATAL),
        ("data_corruption found in storage", FailureClass.CATASTROPHIC),
        ("totally_unknown_error", FailureClass.TRANSIENT),  # default fallback
    ],
)
def test_classifier_classifies_known_patterns(error_type, expected):
    classifier = FailureClassifier()
    assert classifier.classify(error_type, {}) == expected


def test_classifier_catastrophic_takes_priority_over_other_patterns():
    # Contains both a transient-looking substring and a catastrophic one.
    classifier = FailureClassifier()
    result = classifier.classify("timeout_then_security_breach", {})
    assert result == FailureClass.CATASTROPHIC


# ── OrgFailureManager.handle() dispatch ───────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_transient_retries_when_under_max():
    manager = OrgFailureManager()
    failure = _failure(
        failure_class=FailureClass.TRANSIENT,
        retry_count=0,
        max_retries=3,
        backoff_seconds=[0, 0, 0],
    )
    result = await manager.handle(failure)
    assert result == "retry"


@pytest.mark.asyncio
async def test_handle_transient_escalates_when_retries_exhausted():
    manager = OrgFailureManager()
    failure = _failure(failure_class=FailureClass.TRANSIENT, retry_count=5, max_retries=5)
    result = await manager.handle(failure)
    assert result == "escalate"
    assert failure.failure_class == FailureClass.BLOCKED


@pytest.mark.asyncio
async def test_handle_degraded_returns_fallback():
    manager = OrgFailureManager()
    failure = _failure(failure_class=FailureClass.DEGRADED)
    assert await manager.handle(failure) == "fallback"


@pytest.mark.asyncio
async def test_handle_blocked_notifies_and_escalates():
    notifier = AsyncMock()
    manager = OrgFailureManager(notification_router=notifier)
    failure = _failure(failure_class=FailureClass.BLOCKED, mission_id="m1")
    result = await manager.handle(failure)
    assert result == "escalate"
    assert notifier.route.await_count == 1


@pytest.mark.asyncio
async def test_handle_blocked_without_notifier_still_escalates():
    manager = OrgFailureManager(notification_router=None)
    failure = _failure(failure_class=FailureClass.BLOCKED)
    assert await manager.handle(failure) == "escalate"


@pytest.mark.asyncio
async def test_handle_blocked_swallows_notifier_errors():
    notifier = AsyncMock()
    notifier.route = AsyncMock(side_effect=RuntimeError("notify failed"))
    manager = OrgFailureManager(notification_router=notifier)
    failure = _failure(failure_class=FailureClass.BLOCKED)
    result = await manager.handle(failure)
    assert result == "escalate"


@pytest.mark.asyncio
async def test_handle_fatal_writes_dlq_and_notifies():
    notifier = AsyncMock()
    dlq = OrgDLQ()
    manager = OrgFailureManager(notification_router=notifier, dlq=dlq)
    failure = _failure(failure_class=FailureClass.FATAL, mission_id="m2")
    result = await manager.handle(failure)
    assert result == "queue"
    assert notifier.route.await_count == 1
    entries = await dlq.list("org1", status="all")
    assert len(entries) == 1
    assert entries[0].reason == "fatal_failure_max_retries"


@pytest.mark.asyncio
async def test_handle_fatal_swallows_notifier_errors():
    notifier = AsyncMock()
    notifier.route = AsyncMock(side_effect=RuntimeError("boom"))
    manager = OrgFailureManager(notification_router=notifier)
    failure = _failure(failure_class=FailureClass.FATAL)
    result = await manager.handle(failure)
    assert result == "queue"


@pytest.mark.asyncio
async def test_handle_catastrophic_stops_and_writes_dlq():
    dlq = OrgDLQ()
    manager = OrgFailureManager(dlq=dlq)
    failure = _failure(failure_class=FailureClass.CATASTROPHIC)
    result = await manager.handle(failure)
    assert result == "stop"
    entries = await dlq.list("org1", status="all")
    assert entries[0].reason == "catastrophic_human_required"


# ── OrgFailureManager helpers ─────────────────────────────────────────────────


def test_classify_delegates_to_classifier():
    manager = OrgFailureManager()
    assert manager.classify("rate_limit hit", {}) == FailureClass.TRANSIENT


def test_is_hard_limit_true_for_known_limits():
    manager = OrgFailureManager()
    assert manager.is_hard_limit("production_infra_destruction_confirmed") is True


def test_is_hard_limit_false_for_normal_action():
    manager = OrgFailureManager()
    assert manager.is_hard_limit("send_slack_message") is False


# ── OrgDLQ ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dlq_write_generates_id_when_missing():
    dlq = OrgDLQ()
    failure = _failure()
    entry = await dlq.write("", failure, "some_reason")
    assert entry.entry_id != ""
    assert isinstance(entry, DLQEntry)


@pytest.mark.asyncio
async def test_dlq_list_filters_by_org_and_status():
    dlq = OrgDLQ()
    await dlq.write("e1", _failure(org_id="org1"), "r1")
    await dlq.write("e2", _failure(org_id="org2"), "r2")
    only_org1 = await dlq.list("org1", status="all")
    assert len(only_org1) == 1
    assert only_org1[0].entry_id == "e1"


@pytest.mark.asyncio
async def test_dlq_list_default_status_pending():
    dlq = OrgDLQ()
    await dlq.write("e3", _failure(org_id="org1"), "r3")
    pending = await dlq.list("org1")
    assert len(pending) == 1


@pytest.mark.asyncio
async def test_dlq_retry_increments_and_marks_retrying():
    dlq = OrgDLQ()
    await dlq.write("e4", _failure(org_id="org1"), "r4")
    ok = await dlq.retry("e4")
    assert ok is True
    entries = await dlq.list("org1", status="retrying")
    assert entries[0].retry_count == 1
    assert entries[0].last_retry_at is not None


@pytest.mark.asyncio
async def test_dlq_retry_returns_false_for_unknown_entry():
    dlq = OrgDLQ()
    assert await dlq.retry("does-not-exist") is False


@pytest.mark.asyncio
async def test_dlq_dismiss_sets_status_and_resolution():
    dlq = OrgDLQ()
    await dlq.write("e5", _failure(org_id="org1"), "r5")
    await dlq.dismiss("e5", "resolved manually")
    entries = await dlq.list("org1", status="dismissed")
    assert entries[0].event.resolution == "resolved manually"


@pytest.mark.asyncio
async def test_dlq_dismiss_unknown_entry_is_noop():
    dlq = OrgDLQ()
    # Should not raise even though the entry doesn't exist.
    await dlq.dismiss("missing", "reason")
