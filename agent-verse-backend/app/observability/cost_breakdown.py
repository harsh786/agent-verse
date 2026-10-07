"""Per-goal, per-role cost breakdown tracking.

Tracks input/output tokens and estimated cost per LLM role (planner/executor/verifier)
for a single goal execution. Results are stored in goal_events for the UI to display.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import structlog

_log = structlog.get_logger(__name__)


@dataclass
class RoleCostEntry:
    role: str  # "planner" | "executor" | "verifier"
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    # Provenance: the models this role's calls failed over FROM before ``model``
    # served them (a dead preferred model is never recorded as the server).
    fallback_from: list[str] = field(default_factory=list)


def _merge_fallback(current: list[str], new: Sequence[str] | None) -> list[str]:
    out = list(current)
    for m in new or ():
        if m and m not in out:
            out.append(str(m))
    return out


@dataclass
class GoalCostBreakdown:
    goal_id: str
    entries: list[RoleCostEntry] = field(default_factory=list)

    def record(
        self,
        role: str,
        model: str,
        input_tok: int,
        output_tok: int,
        cost: float,
        fallback_from: Sequence[str] | None = None,
    ) -> None:
        for e in self.entries:
            if e.role == role and e.model == model:
                e.input_tokens += input_tok
                e.output_tokens += output_tok
                e.cost_usd += cost
                e.calls += 1
                e.fallback_from = _merge_fallback(e.fallback_from, fallback_from)
                return
        self.entries.append(
            RoleCostEntry(
                role=role,
                model=model,
                input_tokens=input_tok,
                output_tokens=output_tok,
                cost_usd=cost,
                calls=1,
                fallback_from=_merge_fallback([], fallback_from),
            )
        )

    def total_cost(self) -> float:
        return sum(e.cost_usd for e in self.entries)

    def to_state(self) -> dict[str, Any]:
        """Lossless serialization for persistence (raw, unrounded entries)."""
        return {"goal_id": self.goal_id, "entries": [asdict(e) for e in self.entries]}

    @classmethod
    def from_state(cls, data: dict[str, Any]) -> GoalCostBreakdown:
        return cls(
            goal_id=data["goal_id"],
            entries=[RoleCostEntry(**e) for e in data.get("entries", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "total_cost_usd": self.total_cost(),
            "roles": [
                {
                    "role": e.role,
                    "model": e.model,
                    "input_tokens": e.input_tokens,
                    "output_tokens": e.output_tokens,
                    "cost_usd": round(e.cost_usd, 6),
                    "calls": e.calls,
                    "fallback_from": list(e.fallback_from),
                }
                for e in self.entries
            ],
        }


# Per-goal registry (also serves as the local cache / fallback when a backend is set).
_goal_breakdowns: dict[str, GoalCostBreakdown] = {}

# Optional Redis-like backend (synchronous get/set/delete). When configured, breakdowns are
# persisted so the cost-metrics endpoint can still serve them after a process restart
# (production reads via get_breakdown and never calls finalize_breakdown, so the data is
# meant to survive for later retrieval). Left unset -> pure in-memory, as before.
#
# No longer wired by the lifespan: the Postgres backend below (``configure_db``) is
# authoritative. This Redis mirror remains only as an opt-in for DB-less setups.
_backend: Any | None = None


def configure_persistence(redis_client: Any | None) -> None:
    """Enable Redis-backed persistence (client must expose sync get/set/delete)."""
    global _backend
    _backend = redis_client


def reset_persistence() -> None:
    """Disable persistence and revert to the in-memory registry (used by tests)."""
    global _backend
    _backend = None


def _redis_key(goal_id: str) -> str:
    return f"cost_breakdown:{goal_id}"


def _backend_load(goal_id: str) -> GoalCostBreakdown | None:
    """Load a breakdown from the backend, or None on no-backend / miss / error."""
    if _backend is None:
        return None
    try:
        raw = _backend.get(_redis_key(goal_id))
    except Exception:
        return None
    if not raw:
        return None
    try:
        return GoalCostBreakdown.from_state(json.loads(raw))
    except (ValueError, TypeError, KeyError):
        return None


def _persist(goal_id: str, bd: GoalCostBreakdown) -> None:
    # Keep the local cache current too — it is the fallback when the backend is unreachable.
    _goal_breakdowns[goal_id] = bd
    if _backend is None:
        return
    # Best-effort persistence; never break cost recording on a backend hiccup.
    with contextlib.suppress(Exception):
        _backend.set(_redis_key(goal_id), json.dumps(bd.to_state()))


def get_breakdown(goal_id: str) -> GoalCostBreakdown:
    """Return the current breakdown for *goal_id* (read path used by the metrics API)."""
    if _backend is not None:
        bd = _backend_load(goal_id)
        if bd is not None:
            return bd
        # Backend miss or error -> fall back to the local cache (may hold in-flight data).
        return _goal_breakdowns.get(goal_id, GoalCostBreakdown(goal_id=goal_id))
    return _goal_breakdowns.setdefault(goal_id, GoalCostBreakdown(goal_id=goal_id))


def record_role_cost(
    goal_id: str,
    role: str,
    model: str,
    input_tok: int,
    output_tok: int,
    cost: float,
    fallback_from: Sequence[str] | None = None,
) -> None:
    if _backend is not None:
        bd = _backend_load(goal_id) or _goal_breakdowns.setdefault(
            goal_id, GoalCostBreakdown(goal_id=goal_id)
        )
        bd.record(role, model, input_tok, output_tok, cost, fallback_from)
        _persist(goal_id, bd)
    else:
        get_breakdown(goal_id).record(role, model, input_tok, output_tok, cost, fallback_from)


# ── Postgres backend (authoritative when bound) ───────────────────────────────
# Old bug: the breakdown lived in ``_goal_breakdowns`` (this process's memory),
# mirrored into Redis only by the API lifespan. A goal run by a Celery worker or
# another API replica recorded its costs into THAT process, so cost-metrics on
# any other process came back empty, and a restart lost it; the Redis mirror was
# also a read-modify-write of one JSON blob, so concurrent role calls lost
# updates. With a session factory bound (API lifespan, Celery worker) every
# record is an atomic additive UPSERT into ``goal_cost_breakdowns`` (migration
# f7a8b9c0d1e2) and every read comes from there, both under the owning tenant's
# RLS context. The dict / Redis paths remain only for the no-DB test/dev path.
_db: Any | None = None

# ``fallback_from`` (migration e1f3a5c7b9d2) accumulates the distinct models the
# role's calls failed over from; an empty array leaves the stored list as is.
_UPSERT_SQL = (
    "INSERT INTO goal_cost_breakdowns "
    "(tenant_id, goal_id, role, model, input_tokens, output_tokens, cost_usd, calls, "
    "fallback_from) "
    "VALUES (CAST(:tid AS uuid), :gid, :role, :model, :in_tok, :out_tok, :cost, 1, "
    "CAST(:fallback AS jsonb)) "
    "ON CONFLICT (tenant_id, goal_id, role, model) DO UPDATE SET "
    "input_tokens = goal_cost_breakdowns.input_tokens + EXCLUDED.input_tokens, "
    "output_tokens = goal_cost_breakdowns.output_tokens + EXCLUDED.output_tokens, "
    "cost_usd = goal_cost_breakdowns.cost_usd + EXCLUDED.cost_usd, "
    "calls = goal_cost_breakdowns.calls + 1, "
    "fallback_from = CASE WHEN EXCLUDED.fallback_from = '[]'::jsonb "
    "THEN goal_cost_breakdowns.fallback_from "
    "ELSE (SELECT COALESCE(jsonb_agg(DISTINCT m), '[]'::jsonb) FROM "
    "jsonb_array_elements_text(goal_cost_breakdowns.fallback_from || EXCLUDED.fallback_from) "
    "AS m) END, "
    "updated_at = now()"
)
_SELECT_SQL = (
    "SELECT role, model, input_tokens, output_tokens, cost_usd, calls, fallback_from "
    "FROM goal_cost_breakdowns "
    "WHERE tenant_id = CAST(:tid AS uuid) AND goal_id = :gid "
    "ORDER BY first_recorded_at, role, model"
)


def configure_db(session_factory: Any | None) -> None:
    """Bind the Postgres backend (a callable returning an AsyncSession context)."""
    global _db
    _db = session_factory


def reset_db() -> None:
    """Unbind the Postgres backend (tests / shutdown)."""
    global _db
    _db = None


def _is_tenant_uuid(tenant_id: str) -> bool:
    try:
        uuid.UUID(str(tenant_id))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def _db_for(tenant_id: str | None) -> Any | None:
    """The bound DB when *tenant_id* can own a row (tenant ids are UUIDs), else None."""
    if _db is None or not tenant_id or not _is_tenant_uuid(tenant_id):
        return None
    return _db


async def arecord_role_cost(
    goal_id: str,
    role: str,
    model: str,
    input_tok: int,
    output_tok: int,
    cost: float,
    *,
    tenant_id: str | None,
    fallback_from: Sequence[str] | None = None,
) -> None:
    """Record one LLM call's cost for *goal_id* (durable when a DB is bound).

    *model* is the model that SERVED the call; ``fallback_from`` the models it
    failed over from first (provenance — never attribute a call to them).
    """
    fallback = _merge_fallback([], fallback_from)
    db = _db_for(tenant_id)
    if db is None:
        record_role_cost(goal_id, role, model, input_tok, output_tok, cost, fallback)
        return
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    try:
        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, str(tenant_id)),
        ):
            await session.execute(
                text(_UPSERT_SQL),
                {
                    "tid": str(tenant_id),
                    "gid": str(goal_id),
                    "role": str(role),
                    "model": str(model or ""),
                    "in_tok": int(input_tok or 0),
                    "out_tok": int(output_tok or 0),
                    "cost": float(cost or 0.0),
                    "fallback": json.dumps(fallback),
                },
            )
    except Exception as exc:
        # Cost attribution must never break a goal; the failure is logged loudly.
        _log.warning("cost_breakdown_db_record_failed", goal_id=goal_id, error=str(exc)[:200])


async def aget_breakdown(goal_id: str, *, tenant_id: str | None) -> GoalCostBreakdown:
    """Return *goal_id*'s breakdown for *tenant_id* (from Postgres when bound)."""
    db = _db_for(tenant_id)
    if db is None:
        return get_breakdown(goal_id)
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with (
        db() as session,
        session.begin(),
        sqlalchemy_rls_context(session, str(tenant_id)),
    ):
        rows = (
            await session.execute(text(_SELECT_SQL), {"tid": str(tenant_id), "gid": goal_id})
        ).fetchall()
    return GoalCostBreakdown(
        goal_id=goal_id,
        entries=[
            RoleCostEntry(
                role=str(r[0]),
                model=str(r[1]),
                input_tokens=int(r[2] or 0),
                output_tokens=int(r[3] or 0),
                cost_usd=float(r[4] or 0.0),
                calls=int(r[5] or 0),
                fallback_from=_fallback_column(r[6] if len(r) > 6 else None),
            )
            for r in rows
        ],
    )


def _fallback_column(raw: Any) -> list[str]:
    """The ``fallback_from`` JSONB column as a list of model ids."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, list):
        return []
    return [str(m) for m in raw if m]


def finalize_breakdown(goal_id: str) -> dict[str, Any]:
    """Get the final breakdown and remove it from the registry (and backend)."""
    if _backend is not None:
        bd = (
            _backend_load(goal_id)
            or _goal_breakdowns.get(goal_id)
            or GoalCostBreakdown(goal_id=goal_id)
        )
        _goal_breakdowns.pop(goal_id, None)
        with contextlib.suppress(Exception):
            _backend.delete(_redis_key(goal_id))
        return bd.to_dict()
    bd = _goal_breakdowns.pop(goal_id, GoalCostBreakdown(goal_id=goal_id))
    return bd.to_dict()
