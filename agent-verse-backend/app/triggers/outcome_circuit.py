"""Goal-outcome circuit breaker for triggers, shared through Postgres (TRG-13).

The in-process ``CircuitBreakerRegistry`` only saw goal *enqueue* failures, and
the beat builds a fresh dispatcher (fresh registry) for every scheduled fire,
so a schedule whose goals failed every single time kept firing every tick on
every worker. The durable record already links each fire to its goal
(``trigger_events.goal_id`` → ``goals.status``), so the circuit is evaluated
from that: identical on every worker and replica, and fed by what the goals
actually did.

* closed    — fewer than ``FAILURE_THRESHOLD`` consecutive failed goals;
* open      — that many failed in a row and the newest is younger than
              ``COOLDOWN_SECONDS`` (or a probe goal is still running);
* half_open — cooled down: the next fire is the probe; a failed probe reopens
              the circuit for another cooldown, a completed one closes it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

FAILURE_THRESHOLD = 5
COOLDOWN_SECONDS = 900
_TERMINAL = frozenset({"complete", "failed", "cancelled"})


@dataclass(frozen=True)
class OutcomeCircuit:
    state: str  # closed | open | half_open
    consecutive_failures: int = 0
    retry_at: dt.datetime | None = None


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


def evaluate_outcomes(rows: list[tuple[str, dt.datetime]], now: dt.datetime) -> OutcomeCircuit:
    """Circuit state from the trigger's newest goals, newest first."""
    probing = bool(rows) and str(rows[0][0]) not in _TERMINAL
    window = rows[1:] if probing else rows
    failures = 0
    for status, _fired in window:
        if str(status) != "failed":
            break
        failures += 1
    if failures < FAILURE_THRESHOLD:
        return OutcomeCircuit("closed", failures)
    newest = _aware(rows[0][1])
    retry_at = newest + dt.timedelta(seconds=COOLDOWN_SECONDS)
    if probing or _aware(now) < retry_at:
        return OutcomeCircuit("open", failures, retry_at)
    return OutcomeCircuit("half_open", failures, retry_at)


async def read_outcome_circuit(
    db_factory: Any, tenant_id: str, trigger_id: str, *, now: dt.datetime | None = None
) -> OutcomeCircuit:
    """Evaluate *trigger_id*'s circuit from ``trigger_events`` ⋈ ``goals`` (RLS)."""
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with (
        db_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        result = await session.execute(
            text(
                "SELECT g.status, te.fired_at FROM trigger_events te "
                "JOIN goals g ON g.id = te.goal_id "
                "WHERE te.tenant_id = :t AND te.trigger_id = :tid AND te.goal_created "
                "ORDER BY te.fired_at DESC LIMIT :n"
            ),
            {"t": tenant_id, "tid": trigger_id, "n": FAILURE_THRESHOLD + 1},
        )
        rows = [(str(r[0]), r[1]) for r in result.fetchall()]
    return evaluate_outcomes(rows, now or dt.datetime.now(dt.UTC))
