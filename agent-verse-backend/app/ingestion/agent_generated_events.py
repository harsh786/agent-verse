"""Platform events → immediate syncs of the tenant's ``agent_generated`` Sources (A12).

A goal that completes, an approval that is decided, a workflow run that finishes
or a lesson that is learned calls :func:`notify_agent_generated`. When the
tenant has an enabled ``agent_generated`` Source listening for that kind, the
``ingestion.agent_generated_notify`` task queues a sync of it; the sync reads
everything new since its cursor (the connector is a keyset pull over the
platform tables), so a burst of events costs one or two syncs, and an event
that is lost (a broker outage) is still indexed by the next sync.

The notify never raises into the caller (a goal must not fail because its
output could not be indexed) and costs one indexed query per tenant per
``_LISTENERS_TTL`` seconds when nobody listens.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.ingestion.connectors.agent_generated_connector import (
    CONSENTED_SESSION_SQL,
    KIND_CHAT_TRANSCRIPT,
    SUPPORTED_KINDS,
    AgentGeneratedConfigError,
    parse_options,
    tenant_uuid,
)

_log = logging.getLogger(__name__)

NOTIFY_TASK = "ingestion.agent_generated_notify"
# Let the producing transaction commit before the sync reads it.
NOTIFY_DELAY_SECONDS = 3
# A record not yet visible is re-checked this often, this many times.
READY_RETRY_SECONDS = 5
READY_MAX_ATTEMPTS = 6
# Which kinds a tenant's Sources listen for, cached per process.
_LISTENERS_TTL = 10.0
_listeners: dict[str, tuple[float, frozenset[str]]] = {}
# A sync of the Source was running when an event arrived: run once more after it.
RERUN_KEY = "ingestion:agentgen:rerun:{tenant}:{source}"
RERUN_TTL_SECONDS = 3600


def _session_factory(db_factory: Any) -> Any:
    if db_factory is not None:
        return db_factory
    from app.db.session import get_session_factory

    return get_session_factory()


async def _rows(db_factory: Any, tenant_id: str, sql: str, params: dict[str, Any]) -> list[Any]:
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with _session_factory(db_factory)() as session, sqlalchemy_rls_context(
        session, tenant_id
    ):
        return list((await session.execute(text(sql), params)).fetchall())


async def listening_sources(
    tenant_id: str, kind: str = "", *, source_id: str = "", db_factory: Any = None
) -> list[str]:
    """Ids of the tenant's enabled agent_generated Sources listening for ``kind``."""
    params: dict[str, Any] = {"tid": tenant_id}
    where = ""
    if source_id:
        where = " AND id = :sid"
        params["sid"] = source_id
    rows = await _rows(
        db_factory,
        tenant_id,
        "SELECT id, connection_config FROM source_configs WHERE tenant_id = :tid "
        "AND source_type = 'agent_generated' AND enabled = true AND collection_id IS NOT NULL "
        "AND collection_id <> ''" + where + " ORDER BY id LIMIT 100",
        params,
    )
    out: list[str] = []
    for sid, cfg in rows:
        try:
            kinds = parse_options(cfg if isinstance(cfg, dict) else None).kinds
        except AgentGeneratedConfigError:
            continue
        if not kind or kind in kinds:
            out.append(str(sid))
    return out


async def listening_kinds(tenant_id: str, *, db_factory: Any = None) -> frozenset[str]:
    """Kinds any enabled agent_generated Source of the tenant listens for (cached)."""
    now = time.monotonic()
    hit = _listeners.get(tenant_id)
    if hit is not None and hit[0] > now:
        return hit[1]
    rows = await _rows(
        db_factory,
        tenant_id,
        "SELECT connection_config FROM source_configs WHERE tenant_id = :tid "
        "AND source_type = 'agent_generated' AND enabled = true LIMIT 100",
        {"tid": tenant_id},
    )
    kinds: set[str] = set()
    for (cfg,) in rows:
        try:
            kinds.update(parse_options(cfg if isinstance(cfg, dict) else None).kinds)
        except AgentGeneratedConfigError:
            continue
    result = frozenset(kinds)
    if len(_listeners) > 10_000:
        _listeners.clear()
    _listeners[tenant_id] = (now + _LISTENERS_TTL, result)
    return result


def forget_tenant(tenant_id: str) -> None:
    """Drop the cached listeners of a tenant (its Sources changed in this process)."""
    _listeners.pop(tenant_id, None)


def enqueue_notify(
    tenant_id: str,
    kind: str = "",
    ref_id: str = "",
    *,
    source_id: str = "",
    attempt: int = 0,
    countdown: float = NOTIFY_DELAY_SECONDS,
) -> None:
    """Queue ``ingestion.agent_generated_notify`` (raises when the broker refuses).

    Sent through the configured Celery app by name: the task proxy resolves the
    *current* app, which in a thread (``asyncio.to_thread``) of the API process is
    an unconfigured default app (broker on localhost: "connection refused").
    """
    from app.scaling.celery_app import celery_app

    celery_app.send_task(
        NOTIFY_TASK,
        kwargs={
            "tenant_id": tenant_id,
            "kind": kind,
            "ref_id": ref_id,
            "source_id": source_id,
            "attempt": attempt,
        },
        queue="ingestion",
        countdown=countdown,
    )


async def notify_agent_generated(
    tenant_id: str, kind: str, ref_id: str = "", *, db_factory: Any = None
) -> bool:
    """A platform record of ``kind`` was produced: sync the Sources listening for it.

    ``db_factory`` is the caller's session factory (the record's own database);
    without one (an in-memory deployment or a unit test) there is nothing to sync
    from. Returns True when a notify was queued. Never raises: the record is
    indexed by the Source's next sync whatever happens here.
    """
    if not tenant_id or kind not in SUPPORTED_KINDS or db_factory is None:
        return False
    try:
        if kind not in await listening_kinds(tenant_id, db_factory=db_factory):
            return False
        await asyncio.to_thread(enqueue_notify, tenant_id, kind, ref_id)
        return True
    except Exception as exc:
        _log.warning(
            "agent_generated_notify_failed tenant=%s kind=%s ref=%s: %s",
            tenant_id,
            kind,
            ref_id,
            exc,
        )
        return False


async def notify_chat_transcript(
    tenant_id: str, session_id: str, *, db_factory: Any = None
) -> bool:
    """A chat message was saved: sync the listening Sources when the session may
    be indexed (owned by a person who opted in, tenant switch on).

    One cached query when nobody listens for ``chat_transcript``; one indexed
    probe otherwise. Never raises (the next sync reads the session anyway).
    """
    if not tenant_id or not session_id or db_factory is None:
        return False
    try:
        if KIND_CHAT_TRANSCRIPT not in await listening_kinds(tenant_id, db_factory=db_factory):
            return False
        eligible = await _rows(
            db_factory,
            tenant_id,
            "SELECT 1 FROM chat_sessions s WHERE s.id = :sid AND " + CONSENTED_SESSION_SQL,
            {"tid": tenant_id, "sid": session_id},
        )
        if not eligible:
            return False
        await asyncio.to_thread(enqueue_notify, tenant_id, KIND_CHAT_TRANSCRIPT, session_id)
        return True
    except Exception as exc:
        _log.warning(
            "chat_transcript_notify_failed tenant=%s session=%s: %s", tenant_id, session_id, exc
        )
        return False


_READY_SQL = {
    "goal_output": (
        "SELECT 1 FROM goals WHERE tenant_id = :tid AND id = :ref "
        "AND status IN ('complete', 'completed') AND completed_at IS NOT NULL"
    ),
    "hitl_decision": (
        "SELECT 1 FROM approval_requests WHERE tenant_id = :tid AND id = :ref "
        "AND resolved_at IS NOT NULL UNION ALL SELECT 1 FROM workflow_approvals "
        "WHERE tenant_id = CAST(:tid AS uuid) AND request_id = :ref AND status <> 'pending'"
    ),
    "workflow_output": (
        "SELECT 1 FROM workflow_runs WHERE tenant_id = CAST(:tid AS uuid) "
        "AND id::text = :ref AND status = 'complete'"
    ),
    "learning": "SELECT 1 FROM memory_records WHERE tenant_id = :tid AND id = :ref",
    "chat_transcript": "SELECT 1 FROM chat_sessions WHERE tenant_id = :tid AND id = :ref",
}


async def record_ready(
    tenant_id: str, kind: str, ref_id: str, *, db_factory: Any = None
) -> bool:
    """True once the record an event named is committed in its final state."""
    sql = _READY_SQL.get(kind)
    if sql is None or not ref_id:
        return True
    if kind in ("hitl_decision", "workflow_output") and tenant_uuid(tenant_id) is None:
        sql = _READY_SQL["hitl_decision"].split(" UNION ALL ")[0] if kind == "hitl_decision" else ""
        if not sql:
            return True
    rows = await _rows(db_factory, tenant_id, sql, {"tid": tenant_id, "ref": ref_id})
    return bool(rows)
