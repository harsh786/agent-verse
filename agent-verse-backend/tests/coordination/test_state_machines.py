from datetime import UTC, datetime, timedelta

import pytest

from app.coordination.contracts import AuthorizationContext, HandoffCommand
from app.coordination.state_machines import (
    InvalidTransitionError,
    authorize_handoff,
    transition_handoff,
    transition_session,
)


def test_session_lifecycle_and_terminal_immutability() -> None:
    assert transition_session("pending", "active") == "active"
    assert transition_session("active", "completed") == "completed"
    with pytest.raises(InvalidTransitionError):
        transition_session("completed", "active")


def test_handoff_lifecycle() -> None:
    assert transition_handoff("requested", "accepted") == "accepted"
    assert transition_handoff("accepted", "completed") == "completed"
    with pytest.raises(InvalidTransitionError):
        transition_handoff("expired", "accepted")


def test_cross_civilization_handoff_is_rejected() -> None:
    command = HandoffCommand(
        tenant_id="tenant-1",
        session_id="session-1",
        source_agent_id="a",
        target_agent_id="b",
        source_civilization_id="civ-1",
        target_civilization_id="civ-2",
        idempotency_key="handoff-1",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        authorization=AuthorizationContext(
            actor_id="user-1", permissions=frozenset({"coordination:handoff"})
        ),
    )
    with pytest.raises(PermissionError, match="same civilization"):
        authorize_handoff(command)


def test_handoff_requires_permission_and_nonexpired_command() -> None:
    command = HandoffCommand(
        tenant_id="tenant-1",
        session_id="session-1",
        source_agent_id="a",
        target_agent_id="b",
        source_civilization_id="civ-1",
        target_civilization_id="civ-1",
        idempotency_key="handoff-1",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
        authorization=AuthorizationContext(actor_id="user-1", permissions=frozenset()),
    )
    with pytest.raises(PermissionError):
        authorize_handoff(command)

