"""AgentGeneratedConnector — index what the platform itself produces (A12).

An ``agent_generated`` Source turns the tenant's own platform records into
searchable knowledge in its collection:

* ``goal_output``     — the final answer of every completed goal
  (``agentverse://goals/<goal_id>``);
* ``hitl_decision``   — every human decision on an approval: goal approvals
  (``agentverse://approvals/<id>``) and workflow approval gates
  (``agentverse://workflow-approvals/<request_id>``);
* ``workflow_output`` — the outputs of every completed workflow run
  (``agentverse://workflow-runs/<run_id>``);
* ``learning``        — active reflexion lessons (``agentverse://memories/<id>``).

It is a pull connector over those tables (keyset-paged per stream, a JSON
cursor per stream, bounded per sync) — so a sync is resumable, idempotent and
backfills whatever was produced before the Source existed. Completion of a goal,
an approval decision, a finished run or a new lesson *triggers* a sync at once
(``app.ingestion.agent_generated_events``), so new knowledge is searchable within
seconds without polling.

Every query carries an explicit ``tenant_id`` predicate on top of RLS. Each
document carries an ``origin`` (kind + goal / approval / run / memory id) that
the pipeline stores on its chunks, so a search hit cites where it came from and
the data-subject erasure can find what a goal produced. The live listing (every
id still eligible upstream) lets reconciliation remove the knowledge of a goal,
run or lesson that was deleted, except under legal hold.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.ingestion.base_connector import (
    BaseConnector,
    ConnectionHealth,
    ConnectorFetchError,
    stable_doc_id,
)
from app.ingestion.connector_registry import register

if TYPE_CHECKING:
    from app.ingestion.source_config import RawDocument, SourceConfig

_log = logging.getLogger(__name__)

KIND_GOAL_OUTPUT = "goal_output"
KIND_HITL_DECISION = "hitl_decision"
KIND_WORKFLOW_OUTPUT = "workflow_output"
KIND_LEARNING = "learning"
SUPPORTED_KINDS = (KIND_GOAL_OUTPUT, KIND_HITL_DECISION, KIND_WORKFLOW_OUTPUT, KIND_LEARNING)
DEFAULT_KINDS = (KIND_GOAL_OUTPUT, KIND_HITL_DECISION)

DEFAULT_MIN_EVAL_SCORE = 0.7
DEFAULT_MAX_ITEMS_PER_SYNC = 1000
MAX_ITEMS_PER_SYNC_LIMIT = 10_000
DEFAULT_LEARNING_CLASSES = ("public", "internal")
_LEARNING_CLASSES = frozenset({"public", "internal", "confidential", "restricted"})

_PAGE = 200
_LIVE_PAGE = 1000
# Rows whose timestamp lies within this window before the cursor are read again
# on the next sync: a row committed late with an earlier timestamp is not lost
# (an unchanged document is skipped by the pipeline's content hash).
_OVERLAP = timedelta(seconds=120)
_MAX_TEXT_CHARS = 200_000
_MAX_JSON_CHARS = 20_000
_ZERO_UUID = "00000000-0000-0000-0000-000000000000"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class AgentGeneratedConfigError(ValueError):
    """The Source's ``connection_config`` is not a valid agent_generated config."""


@dataclass(frozen=True)
class _Options:
    kinds: tuple[str, ...]
    agent_ids: tuple[str, ...]
    workflow_ids: tuple[str, ...]
    min_eval_score: float
    require_eval_score: bool
    include_subgoals: bool
    learning_classes: tuple[str, ...]
    max_items: int


def _str_list(cfg: dict[str, Any], key: str) -> tuple[str, ...]:
    value = cfg.get(key, [])
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise AgentGeneratedConfigError(f"{key} must be a list of non-empty strings")
    return tuple(value)


def parse_options(cfg: dict[str, Any] | None) -> _Options:
    """Validate ``connection_config``; raises :class:`AgentGeneratedConfigError`."""
    cfg = dict(cfg or {})
    kinds = cfg.get("source_types", list(DEFAULT_KINDS))
    if not isinstance(kinds, list) or not kinds:
        raise AgentGeneratedConfigError(
            f"source_types must be a non-empty list of: {', '.join(SUPPORTED_KINDS)}"
        )
    unknown = [k for k in kinds if k not in SUPPORTED_KINDS]
    if unknown:
        raise AgentGeneratedConfigError(
            f"unsupported source_types {unknown}; supported: {', '.join(SUPPORTED_KINDS)}"
        )
    score = cfg.get("min_eval_score", DEFAULT_MIN_EVAL_SCORE)
    if isinstance(score, bool) or not isinstance(score, int | float) or not 0 <= score <= 1:
        raise AgentGeneratedConfigError("min_eval_score must be a number between 0 and 1")
    max_items = cfg.get("max_items_per_sync", DEFAULT_MAX_ITEMS_PER_SYNC)
    if (
        isinstance(max_items, bool)
        or not isinstance(max_items, int)
        or not 1 <= max_items <= MAX_ITEMS_PER_SYNC_LIMIT
    ):
        raise AgentGeneratedConfigError(
            f"max_items_per_sync must be an integer between 1 and {MAX_ITEMS_PER_SYNC_LIMIT}"
        )
    for flag in ("require_eval_score", "include_subgoals"):
        if not isinstance(cfg.get(flag, False), bool):
            raise AgentGeneratedConfigError(f"{flag} must be true or false")
    classes = _str_list(cfg, "learning_classifications") or DEFAULT_LEARNING_CLASSES
    bad = [c for c in classes if c not in _LEARNING_CLASSES]
    if bad:
        raise AgentGeneratedConfigError(
            f"learning_classifications {bad} unknown; use {sorted(_LEARNING_CLASSES)}"
        )
    return _Options(
        kinds=tuple(dict.fromkeys(kinds)),
        agent_ids=_str_list(cfg, "agent_ids"),
        workflow_ids=_str_list(cfg, "workflow_ids"),
        min_eval_score=float(score),
        require_eval_score=bool(cfg.get("require_eval_score", False)),
        include_subgoals=bool(cfg.get("include_subgoals", False)),
        learning_classes=classes,
        max_items=int(max_items),
    )


def tenant_uuid(tenant_id: str) -> str | None:
    """The tenant id as the workflow tables store it (UUID), or None if it is not one."""
    import uuid

    try:
        return str(uuid.UUID(str(tenant_id)))
    except ValueError:
        return None


def _default_session_factory() -> Any:
    from app.db.session import get_session_factory

    return get_session_factory()


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    return str(value or "")


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _as_obj(value: Any) -> Any:
    if isinstance(value, str | bytes):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _compact_json(value: Any, limit: int = _MAX_JSON_CHARS) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str, sort_keys=True)
    return text if len(text) <= limit else text[:limit] + "\n… (truncated)"


def _origin(kind: str, **ids: Any) -> dict[str, str]:
    origin = {"kind": kind}
    origin.update({k: str(v) for k, v in ids.items() if v not in (None, "")})
    return origin


@dataclass(frozen=True)
class _Stream:
    """One keyset-paged table read: the cursor key, how to page, how to render."""

    key: str
    kind: str
    uuid_ids: bool
    page: Callable[[str, _Options, datetime, str, int], Awaitable[list[dict[str, Any]]]]
    render: Callable[[SourceConfig, dict[str, Any]], RawDocument | None]
    live: Callable[[str, _Options, str, int], Awaitable[list[Any]]]


@register("agent_generated")
class AgentGeneratedConnector(BaseConnector):
    """Index goal outputs, HITL decisions, workflow results and lessons as knowledge."""

    source_type = "agent_generated"
    supports_streaming = True

    def __init__(self, db_factory: Any = None) -> None:
        self._db_factory = db_factory
        #: True when the last get_delta stopped at ``max_items_per_sync``.
        self.truncated = False
        #: The position the last complete get_delta reached (read by the scheduler).
        self.completed_cursor: str | None = None

    # ── Configuration ────────────────────────────────────────────────────────

    @classmethod
    def check_connection_policy(cls, connection_config: dict[str, Any]) -> None:
        parse_options(connection_config)

    def _db(self) -> Any:
        if self._db_factory is None:
            self._db_factory = _default_session_factory()
        return self._db_factory

    async def _query(self, tenant_id: str, sql: str, params: dict[str, Any]) -> list[Any]:
        """One short tenant-scoped read (RLS context + the explicit predicate)."""
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with self._db()() as session, sqlalchemy_rls_context(session, tenant_id):
            return list((await session.execute(text(sql), params)).fetchall())

    async def validate_connection(self, config: SourceConfig) -> ConnectionHealth:
        import time

        try:
            opts = parse_options(config.connection_config)
        except AgentGeneratedConfigError as exc:
            return ConnectionHealth(ok=False, latency_ms=0.0, error=str(exc))
        started = time.monotonic()
        try:
            await self._query(config.tenant_id, "SELECT 1", {})
        except Exception as exc:
            return ConnectionHealth(
                ok=False,
                latency_ms=(time.monotonic() - started) * 1000,
                error=f"platform database unavailable: {type(exc).__name__}",
            )
        return ConnectionHealth(
            ok=True,
            latency_ms=(time.monotonic() - started) * 1000,
            metadata={"source_types": list(opts.kinds)},
        )

    # ── Streams ──────────────────────────────────────────────────────────────

    def _streams(self, opts: _Options) -> list[_Stream]:
        streams: list[_Stream] = []
        if KIND_GOAL_OUTPUT in opts.kinds:
            streams.append(
                _Stream(
                    "goal_output", KIND_GOAL_OUTPUT, False,
                    self._page_goals, _render_goal, self._live_goals,
                )
            )
        if KIND_HITL_DECISION in opts.kinds:
            streams.append(
                _Stream(
                    "hitl_decision", KIND_HITL_DECISION, False,
                    self._page_approvals, _render_approval, self._live_approvals,
                )
            )
            streams.append(
                _Stream(
                    "workflow_decision", KIND_HITL_DECISION, False,
                    self._page_workflow_approvals, _render_workflow_approval,
                    self._live_workflow_approvals,
                )
            )
        if KIND_WORKFLOW_OUTPUT in opts.kinds:
            streams.append(
                _Stream(
                    "workflow_output", KIND_WORKFLOW_OUTPUT, True,
                    self._page_runs, _render_run, self._live_runs,
                )
            )
        if KIND_LEARNING in opts.kinds:
            streams.append(
                _Stream(
                    "learning", KIND_LEARNING, False,
                    self._page_learnings, _render_learning, self._live_learnings,
                )
            )
        return streams

    async def get_delta(
        self, config: SourceConfig, cursor: str | None
    ) -> AsyncIterator[tuple[RawDocument, str]]:
        """Yield every eligible record since the per-stream cursor, oldest first."""
        opts = parse_options(config.connection_config)
        state = _load_cursor(cursor)
        self.truncated = False
        self.completed_cursor = None
        budget = opts.max_items
        for stream in self._streams(opts):
            position = state.get(stream.key)
            if position is None:
                start_ts, start_id = _EPOCH, (_ZERO_UUID if stream.uuid_ids else "")
            else:
                start_ts = _parse_ts(position[0]) - _OVERLAP
                start_id = _ZERO_UUID if stream.uuid_ids else ""
            while budget > 0:
                try:
                    rows = await stream.page(
                        config.tenant_id, opts, start_ts, start_id, min(_PAGE, budget)
                    )
                except Exception as exc:
                    raise ConnectorFetchError(
                        f"agent_generated: reading {stream.key} failed: {type(exc).__name__}: "
                        f"{str(exc)[:200]}"
                    ) from exc
                if not rows:
                    break
                for row in rows:
                    budget -= 1
                    start_ts, start_id = row["_ts"], row["_id"]
                    prev = state.get(stream.key)
                    if prev is None or (_parse_ts(prev[0]), prev[1]) < (start_ts, start_id):
                        state[stream.key] = [_iso(start_ts), start_id]
                    doc = stream.render(config, row)
                    if doc is not None:
                        yield doc, _dump_cursor(state)
                if len(rows) < _PAGE:
                    break
            if budget <= 0:
                self.truncated = True
                break
        # A run that rendered nothing new still moves the cursor past skipped rows.
        self.completed_cursor = _dump_cursor(state)

    async def iter_live_doc_ids(self, config: SourceConfig) -> AsyncIterator[str]:
        """Every document id still eligible upstream (deleted goals / runs drop out)."""
        opts = parse_options(config.connection_config)
        for stream in self._streams(opts):
            after = _ZERO_UUID if stream.uuid_ids else ""
            while True:
                rows = await stream.live(config.tenant_id, opts, after, _LIVE_PAGE)
                for row in rows:
                    yield stable_doc_id(config, stream.kind, row[0])
                if len(rows) < _LIVE_PAGE:
                    break
                after = str(rows[-1][0])

    # ── Goal outputs ─────────────────────────────────────────────────────────

    @staticmethod
    def _goal_filters(opts: _Options, params: dict[str, Any]) -> str:
        where = ""
        if not opts.include_subgoals:
            where += " AND g.parent_goal_id IS NULL"
        if opts.agent_ids:
            where += " AND g.agent_id = ANY(CAST(:agents AS text[]))"
            params["agents"] = list(opts.agent_ids)
        return where

    async def _page_goals(
        self, tenant_id: str, opts: _Options, ts: datetime, after: str, limit: int
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"tid": tenant_id, "ts": ts, "id": after, "lim": limit}
        rows = await self._query(
            tenant_id,
            "SELECT g.id, g.goal_text, g.agent_id, g.completed_at "
            "FROM goals g WHERE g.tenant_id = :tid "
            "AND g.status IN ('complete', 'completed') AND g.dry_run = false "
            "AND g.completed_at IS NOT NULL AND (g.completed_at, g.id) > (:ts, :id)"
            + self._goal_filters(opts, params)
            + " ORDER BY g.completed_at, g.id LIMIT :lim",
            params,
        )
        if not rows:
            return []
        ids = [str(r[0]) for r in rows]
        answers = await self._final_answers(tenant_id, ids)
        scores = await self._eval_scores(tenant_id, ids)
        out: list[dict[str, Any]] = []
        for gid, goal_text, agent_id, completed_at in rows:
            score = scores.get(str(gid))
            unscored_refused = score is None and opts.require_eval_score
            below_floor = score is not None and score < opts.min_eval_score
            answer = "" if unscored_refused or below_floor else answers.get(str(gid), "")
            out.append(
                {
                    "_ts": completed_at, "_id": str(gid), "goal_id": str(gid),
                    "goal_text": str(goal_text or ""), "agent_id": str(agent_id or ""),
                    "completed_at": _iso(completed_at), "answer": answer, "eval_score": score,
                }
            )
        return out

    async def _final_answers(self, tenant_id: str, goal_ids: list[str]) -> dict[str, str]:
        from app.services.result_artifacts import final_answer_text

        rows = await self._query(
            tenant_id,
            "SELECT goal_id, event_type, payload FROM ("
            " SELECT goal_id, event_type, payload, sequence, row_number() OVER ("
            "   PARTITION BY goal_id, event_type ORDER BY sequence DESC) AS rn"
            " FROM goal_events WHERE tenant_id = :tid AND goal_id = ANY(CAST(:gids AS text[]))"
            " AND event_type IN ('goal_complete', 'step_complete')"
            ") s WHERE rn = 1 ORDER BY goal_id, sequence",
            {"tid": tenant_id, "gids": goal_ids},
        )
        events: dict[str, list[dict[str, Any]]] = {}
        for goal_id, event_type, payload in rows:
            body = _as_obj(payload)
            event = dict(body) if isinstance(body, dict) else {}
            event.setdefault("type", str(event_type))
            events.setdefault(str(goal_id), []).append(event)
        return {gid: final_answer_text(evts) for gid, evts in events.items()}

    async def _eval_scores(self, tenant_id: str, goal_ids: list[str]) -> dict[str, float]:
        rows = await self._query(
            tenant_id,
            "SELECT DISTINCT ON (goal_id) goal_id, overall_score FROM eval_scorecards "
            "WHERE tenant_id = :tid AND goal_id = ANY(CAST(:gids AS text[])) "
            "ORDER BY goal_id, created_at DESC NULLS LAST",
            {"tid": tenant_id, "gids": goal_ids},
        )
        return {str(r[0]): float(r[1]) for r in rows if r[1] is not None}

    async def _live_goals(
        self, tenant_id: str, opts: _Options, after: str, limit: int
    ) -> list[Any]:
        params: dict[str, Any] = {"tid": tenant_id, "id": after, "lim": limit}
        return await self._query(
            tenant_id,
            "SELECT g.id FROM goals g WHERE g.tenant_id = :tid "
            "AND g.status IN ('complete', 'completed') AND g.dry_run = false AND g.id > :id"
            + self._goal_filters(opts, params)
            + " ORDER BY g.id LIMIT :lim",
            params,
        )

    # ── HITL decisions: goal approvals ───────────────────────────────────────

    async def _page_approvals(
        self, tenant_id: str, opts: _Options, ts: datetime, after: str, limit: int
    ) -> list[dict[str, Any]]:
        rows = await self._query(
            tenant_id,
            "SELECT a.id, a.goal_id, a.action, a.risk_level, a.status, a.approver, a.note, "
            "a.resolved_at, g.goal_text, g.agent_id "
            "FROM approval_requests a LEFT JOIN goals g "
            "  ON g.id = a.goal_id AND g.tenant_id = a.tenant_id "
            "WHERE a.tenant_id = :tid AND a.status IN ('approved', 'rejected') "
            "AND a.resolved_at IS NOT NULL AND (a.resolved_at, a.id) > (:ts, :id) "
            "ORDER BY a.resolved_at, a.id LIMIT :lim",
            {"tid": tenant_id, "ts": ts, "id": after, "lim": limit},
        )
        return [
            {
                "_ts": r[7], "_id": str(r[0]), "approval_id": str(r[0]),
                "goal_id": str(r[1] or ""), "action": str(r[2] or ""),
                "risk_level": str(r[3] or ""), "decision": str(r[4]),
                "approver": str(r[5] or ""), "note": str(r[6] or ""),
                "resolved_at": _iso(r[7]), "goal_text": str(r[8] or ""),
                "agent_id": str(r[9] or ""),
            }
            for r in rows
        ]

    async def _live_approvals(
        self, tenant_id: str, opts: _Options, after: str, limit: int
    ) -> list[Any]:
        return await self._query(
            tenant_id,
            "SELECT a.id FROM approval_requests a WHERE a.tenant_id = :tid "
            "AND a.status IN ('approved', 'rejected') AND a.id > :id ORDER BY a.id LIMIT :lim",
            {"tid": tenant_id, "id": after, "lim": limit},
        )

    # ── HITL decisions: workflow approval gates ──────────────────────────────

    @staticmethod
    def _workflow_filter(opts: _Options, params: dict[str, Any], column: str) -> str:
        if not opts.workflow_ids:
            return ""
        params["wfs"] = list(opts.workflow_ids)
        return f" AND {column}::text = ANY(CAST(:wfs AS text[]))"

    async def _page_workflow_approvals(
        self, tenant_id: str, opts: _Options, ts: datetime, after: str, limit: int
    ) -> list[dict[str, Any]]:
        if tenant_uuid(tenant_id) is None:
            return []  # workflow tables key tenants by UUID: none can exist
        params: dict[str, Any] = {"tid": tenant_id, "ts": ts, "id": after, "lim": limit}
        rows = await self._query(
            tenant_id,
            "SELECT w.request_id, w.run_id, w.workflow_id, w.step_id, w.status, w.payload, "
            "w.updated_at FROM workflow_approvals w WHERE w.tenant_id = CAST(:tid AS uuid) "
            "AND w.status IN ('approved', 'rejected', 'decided') "
            "AND (w.updated_at, w.request_id) > (:ts, :id)"
            + self._workflow_filter(opts, params, "w.workflow_id")
            + " ORDER BY w.updated_at, w.request_id LIMIT :lim",
            params,
        )
        out = []
        for request_id, run_id, workflow_id, step_id, status, payload, updated_at in rows:
            body = _as_obj(payload)
            out.append(
                {
                    "_ts": updated_at, "_id": str(request_id), "approval_id": str(request_id),
                    "run_id": str(run_id or ""), "workflow_id": str(workflow_id or ""),
                    "step_id": str(step_id or ""), "status": str(status),
                    "payload": body if isinstance(body, dict) else {},
                }
            )
        return out

    async def _live_workflow_approvals(
        self, tenant_id: str, opts: _Options, after: str, limit: int
    ) -> list[Any]:
        if tenant_uuid(tenant_id) is None:
            return []  # workflow tables key tenants by UUID: none can exist
        params: dict[str, Any] = {"tid": tenant_id, "id": after, "lim": limit}
        return await self._query(
            tenant_id,
            "SELECT w.request_id FROM workflow_approvals w "
            "WHERE w.tenant_id = CAST(:tid AS uuid) "
            "AND w.status IN ('approved', 'rejected', 'decided') AND w.request_id > :id"
            + self._workflow_filter(opts, params, "w.workflow_id")
            + " ORDER BY w.request_id LIMIT :lim",
            params,
        )

    # ── Workflow run outputs ─────────────────────────────────────────────────

    async def _page_runs(
        self, tenant_id: str, opts: _Options, ts: datetime, after: str, limit: int
    ) -> list[dict[str, Any]]:
        if tenant_uuid(tenant_id) is None:
            return []  # workflow tables key tenants by UUID: none can exist
        params: dict[str, Any] = {"tid": tenant_id, "ts": ts, "id": after, "lim": limit}
        rows = await self._query(
            tenant_id,
            "SELECT r.id, r.workflow_id, d.name, r.trigger_type, r.inputs, r.outputs, "
            "r.completed_at FROM workflow_runs r LEFT JOIN workflow_definitions d "
            "  ON d.id = r.workflow_id AND d.tenant_id = r.tenant_id "
            "WHERE r.tenant_id = CAST(:tid AS uuid) AND r.status = 'complete' "
            "AND COALESCE(r.is_test_run, false) = false AND r.completed_at IS NOT NULL "
            "AND (r.completed_at, r.id) > (:ts, CAST(:id AS uuid))"
            + self._workflow_filter(opts, params, "r.workflow_id")
            + " ORDER BY r.completed_at, r.id LIMIT :lim",
            params,
        )
        return [
            {
                "_ts": r[6], "_id": str(r[0]), "run_id": str(r[0]),
                "workflow_id": str(r[1] or ""), "workflow_name": str(r[2] or ""),
                "trigger_type": str(r[3] or ""), "inputs": _as_obj(r[4]),
                "outputs": _as_obj(r[5]), "completed_at": _iso(r[6]),
            }
            for r in rows
        ]

    async def _live_runs(
        self, tenant_id: str, opts: _Options, after: str, limit: int
    ) -> list[Any]:
        if tenant_uuid(tenant_id) is None:
            return []  # workflow tables key tenants by UUID: none can exist
        params: dict[str, Any] = {"tid": tenant_id, "id": after, "lim": limit}
        return await self._query(
            tenant_id,
            "SELECT r.id FROM workflow_runs r WHERE r.tenant_id = CAST(:tid AS uuid) "
            "AND r.status = 'complete' AND COALESCE(r.is_test_run, false) = false "
            "AND r.id > CAST(:id AS uuid)"
            + self._workflow_filter(opts, params, "r.workflow_id")
            + " ORDER BY r.id LIMIT :lim",
            params,
        )

    # ── Learnings (reflexion lessons) ────────────────────────────────────────

    @staticmethod
    def _learning_filters(opts: _Options, params: dict[str, Any]) -> str:
        params["classes"] = list(opts.learning_classes)
        where = " AND m.classification = ANY(CAST(:classes AS text[]))"
        if opts.agent_ids:
            where += " AND m.agent_id = ANY(CAST(:agents AS text[]))"
            params["agents"] = list(opts.agent_ids)
        return where

    async def _page_learnings(
        self, tenant_id: str, opts: _Options, ts: datetime, after: str, limit: int
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"tid": tenant_id, "ts": ts, "id": after, "lim": limit}
        rows = await self._query(
            tenant_id,
            "SELECT m.id, m.safe_summary, m.source_goal_id, m.agent_id, m.classification, "
            "m.confidence, m.updated_at FROM memory_records m WHERE m.tenant_id = :tid "
            "AND m.memory_kind = 'reflexion' AND m.lifecycle_state = 'active' "
            "AND (m.expires_at IS NULL OR m.expires_at > NOW()) "
            "AND (m.updated_at, m.id) > (:ts, :id)"
            + self._learning_filters(opts, params)
            + " ORDER BY m.updated_at, m.id LIMIT :lim",
            params,
        )
        return [
            {
                "_ts": r[6], "_id": str(r[0]), "memory_id": str(r[0]),
                "lesson": str(r[1] or ""), "goal_id": str(r[2] or ""),
                "agent_id": str(r[3] or ""), "classification": str(r[4] or ""),
                "confidence": r[5], "updated_at": _iso(r[6]),
            }
            for r in rows
        ]

    async def _live_learnings(
        self, tenant_id: str, opts: _Options, after: str, limit: int
    ) -> list[Any]:
        params: dict[str, Any] = {"tid": tenant_id, "id": after, "lim": limit}
        return await self._query(
            tenant_id,
            "SELECT m.id FROM memory_records m WHERE m.tenant_id = :tid "
            "AND m.memory_kind = 'reflexion' AND m.lifecycle_state = 'active' "
            "AND (m.expires_at IS NULL OR m.expires_at > NOW()) AND m.id > :id"
            + self._learning_filters(opts, params)
            + " ORDER BY m.id LIMIT :lim",
            params,
        )


# ── Cursor ───────────────────────────────────────────────────────────────────


def _load_cursor(cursor: str | None) -> dict[str, list[str]]:
    """Per-stream ``[iso_ts, id]`` positions; an unknown / legacy cursor starts over."""
    if not cursor:
        return {}
    try:
        data = json.loads(cursor)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    state: dict[str, list[str]] = {}
    for key, value in data.items():
        if key == "v" or not isinstance(value, list) or len(value) != 2:
            continue
        try:
            _parse_ts(str(value[0]))
        except ValueError:
            continue
        state[key] = [str(value[0]), str(value[1])]
    return state


def _dump_cursor(state: dict[str, list[str]]) -> str:
    return json.dumps({"v": 1, **state}, sort_keys=True)


# ── Rendering ────────────────────────────────────────────────────────────────


def _doc(
    config: SourceConfig,
    kind: str,
    ref_id: str,
    *,
    title: str,
    body: str,
    url: str,
    modified_at: str,
    author: str,
    origin: dict[str, str],
) -> RawDocument:
    from app.ingestion.source_config import RawDocument

    text = body if len(body) <= _MAX_TEXT_CHARS else body[:_MAX_TEXT_CHARS] + "\n… (truncated)"
    return RawDocument(
        doc_id=stable_doc_id(config, kind, ref_id),
        source_id=config.source_id,
        tenant_id=config.tenant_id,
        content=text.encode("utf-8"),
        content_type="text/markdown",
        source_url=url,
        title=title[:200],
        author=author[:200],
        modified_at=modified_at,
        metadata={"origin": origin, "agent_generated_kind": kind},
    )


def _render_goal(config: SourceConfig, row: dict[str, Any]) -> RawDocument | None:
    answer = str(row.get("answer") or "").strip()
    if not answer:
        return None  # no answer (or below the score floor): nothing worth indexing
    gid = row["goal_id"]
    lines = [
        f"# Goal result: {row['goal_text'][:300]}",
        "",
        f"- Goal id: {gid}",
        f"- Agent: {row['agent_id'] or 'default'}",
        f"- Completed: {row['completed_at']}",
    ]
    if row.get("eval_score") is not None:
        lines.append(f"- Evaluation score: {row['eval_score']:.2f}")
    lines += ["", "## Goal", row["goal_text"], "", "## Answer", answer]
    return _doc(
        config, KIND_GOAL_OUTPUT, gid,
        title=f"Goal result: {row['goal_text']}",
        body="\n".join(lines),
        url=f"agentverse://goals/{gid}",
        modified_at=row["completed_at"],
        author=row["agent_id"] or "agent",
        origin=_origin(KIND_GOAL_OUTPUT, goal_id=gid, agent_id=row["agent_id"]),
    )


def _render_approval(config: SourceConfig, row: dict[str, Any]) -> RawDocument | None:
    decision = row["decision"].upper()
    lines = [
        f"# Approval {row['decision']}: {row['action'][:200]}",
        "",
        f"- Decision: {decision}",
        f"- Decided by: {row['approver'] or 'unknown'}",
        f"- Decided at: {row['resolved_at']}",
        f"- Approval id: {row['approval_id']}",
        f"- Goal id: {row['goal_id'] or '-'}",
        f"- Risk level: {row['risk_level'] or '-'}",
        "",
        "## Requested action",
        row["action"],
        "",
        "## Reviewer note",
        row["note"] or "(none)",
    ]
    if row["goal_text"]:
        lines += ["", "## Goal", row["goal_text"]]
    return _doc(
        config, KIND_HITL_DECISION, row["approval_id"],
        title=f"Approval {row['decision']}: {row['action']}",
        body="\n".join(lines),
        url=f"agentverse://approvals/{row['approval_id']}",
        modified_at=row["resolved_at"],
        author=row["approver"] or "reviewer",
        origin=_origin(
            KIND_HITL_DECISION, approval_id=row["approval_id"], goal_id=row["goal_id"],
            decision=row["decision"], agent_id=row["agent_id"],
        ),
    )


def _render_workflow_approval(config: SourceConfig, row: dict[str, Any]) -> RawDocument | None:
    p = row["payload"]
    action = str(p.get("action_taken") or row["status"])
    decision = {"approve": "approved", "reject": "rejected"}.get(action, action)
    step = str(p.get("step_name") or row["step_id"])
    workflow = str(p.get("workflow_name") or row["workflow_id"])
    lines = [
        f"# Workflow approval {decision}: {step} ({workflow})",
        "",
        f"- Decision: {decision.upper()} (action: {action})",
        f"- Decided by: {p.get('reviewed_by') or 'unknown'}",
        f"- Decided at: {p.get('reviewed_at') or ''}",
        f"- Approval id: {row['approval_id']}",
        f"- Workflow: {workflow} ({row['workflow_id']})",
        f"- Run id: {row['run_id']}",
        f"- Step: {step}",
        "",
        "## Reviewer note",
        str(p.get("note") or "(none)"),
    ]
    context = p.get("context")
    if isinstance(context, list) and context:
        lines += ["", "## What the reviewer saw"]
        for item in context[:50]:
            if isinstance(item, dict):
                value = item.get("value")
                shown = value if isinstance(value, str) else _compact_json(value, 4000)
                lines.append(f"- {item.get('label', '')}: {shown}")
    if p.get("form_data"):
        lines += ["", "## Form", _compact_json(p.get("form_data"), 4000)]
    return _doc(
        config, KIND_HITL_DECISION, row["approval_id"],
        title=f"Workflow approval {decision}: {step} ({workflow})",
        body="\n".join(lines),
        url=f"agentverse://workflow-approvals/{row['approval_id']}",
        modified_at=str(p.get("reviewed_at") or _iso(row["_ts"])),
        author=str(p.get("reviewed_by") or "reviewer"),
        origin=_origin(
            KIND_HITL_DECISION, approval_id=row["approval_id"], workflow_run_id=row["run_id"],
            workflow_id=row["workflow_id"], decision=decision,
        ),
    )


def _render_run(config: SourceConfig, row: dict[str, Any]) -> RawDocument | None:
    outputs = row["outputs"]
    if outputs in (None, {}, [], ""):
        return None
    name = row["workflow_name"] or row["workflow_id"] or "workflow"
    lines = [
        f"# Workflow result: {name}",
        "",
        f"- Workflow: {name} ({row['workflow_id']})",
        f"- Run id: {row['run_id']}",
        f"- Trigger: {row['trigger_type'] or '-'}",
        f"- Completed: {row['completed_at']}",
    ]
    if row["inputs"] not in (None, {}, [], ""):
        lines += ["", "## Inputs", _compact_json(row["inputs"], 4000)]
    lines += ["", "## Outputs", _compact_json(outputs, _MAX_TEXT_CHARS)]
    return _doc(
        config, KIND_WORKFLOW_OUTPUT, row["run_id"],
        title=f"Workflow result: {name} (run {row['run_id']})",
        body="\n".join(lines),
        url=f"agentverse://workflow-runs/{row['run_id']}",
        modified_at=row["completed_at"],
        author="workflow",
        origin=_origin(
            KIND_WORKFLOW_OUTPUT, workflow_run_id=row["run_id"], workflow_id=row["workflow_id"]
        ),
    )


def _render_learning(config: SourceConfig, row: dict[str, Any]) -> RawDocument | None:
    lesson = row["lesson"].strip()
    if not lesson:
        return None
    lines = [
        f"# Lesson learned: {lesson[:150]}",
        "",
        f"- Memory id: {row['memory_id']}",
        f"- Learned from goal: {row['goal_id'] or '-'}",
        f"- Agent: {row['agent_id'] or 'default'}",
        f"- Confidence: {row['confidence']}",
        "",
        lesson,
    ]
    return _doc(
        config, KIND_LEARNING, row["memory_id"],
        title=f"Lesson learned: {lesson}",
        body="\n".join(lines),
        url=f"agentverse://memories/{row['memory_id']}",
        modified_at=row["updated_at"],
        author=row["agent_id"] or "agent",
        origin=_origin(
            KIND_LEARNING, memory_id=row["memory_id"], goal_id=row["goal_id"],
            agent_id=row["agent_id"],
        ),
    )
