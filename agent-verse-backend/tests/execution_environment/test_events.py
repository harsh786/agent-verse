"""Tests for execution-environment event helpers."""
from __future__ import annotations

from app.execution_environment.events import (
    make_forwarding_callback,
    make_isolation_event,
    wrap_agent_event,
)


def test_wrap_agent_event_preserves_payload() -> None:
    raw = {"type": "step_complete", "step": "Run tests", "output": "OK"}
    event = wrap_agent_event(
        goal_id="g1",
        tenant_id="t1",
        raw_event=raw,
        runner_type="fake",
        capsule_id="fake-abc",
        attempt_id="att1",
    )
    assert event.event_type == "step_complete"
    assert event.payload == raw
    assert event.runner_type == "fake"
    assert event.capsule_id == "fake-abc"


def test_make_isolation_event_sets_type() -> None:
    event = make_isolation_event(
        goal_id="g1",
        tenant_id="t1",
        event_type="runner_started",
        runner_type="local",
        capsule_id="local-xyz",
        attempt_id="att2",
        startup_time_ms=42.5,
    )
    assert event.event_type == "runner_started"
    assert event.payload["type"] == "runner_started"
    assert event.payload["startup_time_ms"] == 42.5


async def test_forwarding_callback_adds_isolation_metadata() -> None:
    received: list[dict] = []

    async def downstream(event: dict) -> None:
        received.append(event)

    cb = make_forwarding_callback(
        goal_id="g1",
        tenant_id="t1",
        downstream=downstream,
        runner_type="fake",
        capsule_id="fake-def",
        attempt_id="att3",
    )

    await cb({"type": "plan_ready", "steps": ["step1"]})

    assert len(received) == 1
    evt = received[0]
    assert evt["type"] == "plan_ready"
    # Isolation metadata must be present
    assert "_isolation" in evt
    assert evt["_isolation"]["runner_type"] == "fake"
    assert evt["_isolation"]["capsule_id"] == "fake-def"


async def test_forwarding_callback_no_metadata_when_runner_empty() -> None:
    """Events without runner_type set must not have _isolation key."""
    received: list[dict] = []

    async def downstream(event: dict) -> None:
        received.append(event)

    cb = make_forwarding_callback(
        goal_id="g1",
        tenant_id="t1",
        downstream=downstream,
        # runner_type intentionally empty
    )
    await cb({"type": "goal_started", "goal": "test"})
    assert "_isolation" not in received[0]
