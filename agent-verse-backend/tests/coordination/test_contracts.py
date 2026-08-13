from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.coordination.contracts import (
    AuthorizationContext,
    Classification,
    CoordinationEvent,
    EventPayload,
    HandoffCommand,
)


def test_event_envelope_is_immutable_and_versioned() -> None:
    event = CoordinationEvent(
        event_id="evt-1",
        tenant_id="tenant-1",
        session_id="session-1",
        sequence=1,
        event_type="session.created",
        occurred_at=datetime.now(UTC),
        correlation_id="corr-1",
        causation_id=None,
        idempotency_key="create-1",
        classification=Classification.INTERNAL,
        payload=EventPayload(kind="session", data={"state": "pending"}),
    )

    assert event.schema_version == 1
    with pytest.raises(ValidationError):
        event.sequence = 2


@pytest.mark.parametrize(
    ("field", "value"),
    [("sequence", 0), ("schema_version", 0), ("occurred_at", datetime.now())],
)
def test_event_rejects_invalid_ordering_and_time(field: str, value: object) -> None:
    data = {
        "event_id": "evt-1",
        "tenant_id": "tenant-1",
        "session_id": "session-1",
        "sequence": 1,
        "event_type": "session.created",
        "occurred_at": datetime.now(UTC),
        "correlation_id": "corr-1",
        "idempotency_key": "create-1",
        "classification": "internal",
        "payload": {"kind": "session", "data": {}},
    }
    data[field] = value
    with pytest.raises(ValidationError):
        CoordinationEvent.model_validate(data)


def test_handoff_requires_authorization_and_future_expiry() -> None:
    with pytest.raises(ValidationError):
        HandoffCommand(
            tenant_id="tenant-1",
            session_id="session-1",
            source_agent_id="a",
            target_agent_id="b",
            source_civilization_id="civ-1",
            target_civilization_id="civ-1",
            idempotency_key="handoff-1",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )

    command = HandoffCommand(
        tenant_id="tenant-1",
        session_id="session-1",
        source_agent_id="a",
        target_agent_id="b",
        source_civilization_id="civ-1",
        target_civilization_id="civ-1",
        idempotency_key="handoff-1",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        authorization=AuthorizationContext(
            actor_id="user-1", permissions=frozenset({"coordination:handoff"})
        ),
    )
    assert command.authorization.actor_id == "user-1"

