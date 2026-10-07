"""The goal status state machine, as the runtime really moves goals.

Statuses are :class:`app.agent.state.GoalStatus`. The non-terminal ones move
freely between each other — the graph replans (verifying -> planning), a HITL
gate parks a goal (-> waiting_human) and resume returns it to planning or
executing — so the one invariant is that **a terminal status is final**:
complete, failed and cancelled have no outgoing transition (not even to
another terminal status: a cancel that won a race with the worker's
"complete" is kept, and vice versa).

:func:`allowed_predecessors` is what every goal status write is conditioned on
(``GoalService._db_update_goal_status``: ``WHERE status IN (...)``), so the
rule holds for every writer, not only the ones that remembered to pass
``only_if_active`` (a08-F189-03). The previous table here described statuses
that do not exist ("pending", "paused"), forbade real transitions (replan) and
had no callers.
"""

from __future__ import annotations

from app.agent.state import GoalStatus

TERMINAL_STATUSES: frozenset[str] = frozenset(
    {GoalStatus.COMPLETE.value, GoalStatus.FAILED.value, GoalStatus.CANCELLED.value}
)
ACTIVE_STATUSES: frozenset[str] = frozenset(
    s.value for s in GoalStatus if s.value not in TERMINAL_STATUSES
)

_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    **{active: frozenset(s.value for s in GoalStatus) for active in ACTIVE_STATUSES},
    **{terminal: frozenset() for terminal in TERMINAL_STATUSES},
}


def is_valid_transition(from_status: str, to_status: str) -> bool:
    """True when a goal in ``from_status`` may be written as ``to_status``.

    Re-writing the same non-terminal status (e.g. a second "executing") is
    allowed; unknown statuses are never valid.
    """
    return to_status in _VALID_TRANSITIONS.get(from_status, frozenset())


def allowed_predecessors(to_status: str) -> frozenset[str]:
    """Every status a goal may be in for a write of ``to_status`` to apply."""
    return frozenset(src for src, dsts in _VALID_TRANSITIONS.items() if to_status in dsts)
