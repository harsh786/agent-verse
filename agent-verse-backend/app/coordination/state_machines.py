"""Explicit lifecycle transitions and authorization predicates."""

from __future__ import annotations

from datetime import UTC, datetime

from app.coordination.contracts import HandoffCommand


class InvalidTransitionError(ValueError):
    """Raised when a lifecycle transition is not permitted."""


SESSION_TRANSITIONS = {
    "pending": frozenset({"active", "cancelling", "failed"}),
    "active": frozenset({"paused", "cancelling", "completed", "failed"}),
    "paused": frozenset({"active", "cancelling", "failed"}),
    "cancelling": frozenset({"cancelled", "failed"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
    "failed": frozenset(),
}

EXECUTION_TRANSITIONS = {
    "pending": frozenset({"running", "cancelled", "failed"}),
    "running": frozenset({"checkpointed", "cancelling", "completed", "failed"}),
    "checkpointed": frozenset({"running", "cancelling", "completed", "failed"}),
    "cancelling": frozenset({"cancelled", "failed"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
    "failed": frozenset(),
}

HANDOFF_TRANSITIONS = {
    "requested": frozenset({"accepted", "rejected", "expired", "cancelled"}),
    "accepted": frozenset({"active", "completed", "cancelled", "expired"}),
    "active": frozenset({"completed", "cancelled", "expired"}),
    "rejected": frozenset(),
    "completed": frozenset(),
    "cancelled": frozenset(),
    "expired": frozenset(),
}


def _transition(current: str, target: str, transitions: dict[str, frozenset[str]]) -> str:
    if target not in transitions.get(current, frozenset()):
        raise InvalidTransitionError(f"invalid transition: {current} -> {target}")
    return target


def transition_session(current: str, target: str) -> str:
    return _transition(current, target, SESSION_TRANSITIONS)


def transition_execution(current: str, target: str) -> str:
    return _transition(current, target, EXECUTION_TRANSITIONS)


def transition_handoff(current: str, target: str) -> str:
    return _transition(current, target, HANDOFF_TRANSITIONS)


def authorize_handoff(command: HandoffCommand, *, now: datetime | None = None) -> None:
    if command.source_civilization_id != command.target_civilization_id:
        raise PermissionError("handoffs must remain in the same civilization")
    if "coordination:handoff" not in command.authorization.permissions:
        raise PermissionError("coordination:handoff permission is required")
    current_time = now or datetime.now(UTC)
    if command.expires_at <= current_time:
        raise PermissionError("handoff command has expired")

