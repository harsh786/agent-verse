"""Prospective memory end to end: create, surface, fire, purge (MEM-16).

The leased lifecycle (``ProspectiveMemoryService`` / ``PostgresProspectiveMemory
Service``) and the planner's read-only "pending intentions" block existed, but
nothing created an intention and nothing ever fired one. This module is the
single implementation used by:

* the REST API (``/memory/prospective``) — users schedule intentions;
* the ``builtin-memory`` agent tool (``defer_intention`` / ``list_intentions``)
  — an agent defers work to later from inside a goal;
* the Celery beat task that leases due intentions per tenant (fencing tokens,
  so duplicate workers lose the race), submits each as a goal for that tenant,
  and marks it completed with the goal id.

Every intention text passes the MEMORY_WRITE guardrail before it is stored.
All persistence is tenant-scoped (Postgres service runs under the tenant RLS).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.memory.prospective import ProspectiveMemory, prospective_id
from app.observability.logging import get_logger

_log = get_logger(__name__)

DEFAULT_TTL = timedelta(days=30)
MAX_TTL = timedelta(days=365)
MAX_INTENTION_CHARS = 2_000

GoalSubmitter = Callable[[ProspectiveMemory], Awaitable[dict[str, Any]]]


class ProspectiveIntentionError(ValueError):
    """The intention is invalid (bad times, empty text) — nothing was stored."""


async def create_intention(
    service: Any,
    *,
    tenant_id: str,
    intention: str,
    due_at: datetime,
    expires_at: datetime | None = None,
    source_goal_id: str = "",
    source_execution_id: str = "",
    agent_id: str | None = None,
    idempotency_key: str | None = None,
    now: datetime | None = None,
) -> ProspectiveMemory:
    """Screen and store one deferred intention for *tenant_id*.

    Raises :class:`ProspectiveIntentionError` for invalid input,
    ``LongTermMemoryBlockedError`` when the guardrail blocks the text and
    ``LongTermMemoryUnavailableError`` when it cannot vet it (fail closed).
    """
    from app.memory.long_term import screen_user_memory_content

    text = " ".join((intention or "").split())
    if not text:
        raise ProspectiveIntentionError("intention must not be empty")
    if len(text) > MAX_INTENTION_CHARS:
        raise ProspectiveIntentionError(f"intention exceeds {MAX_INTENTION_CHARS} characters")
    current = now or datetime.now(UTC)
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=UTC)
    expiry = expires_at or (due_at + DEFAULT_TTL)
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    if expiry <= due_at:
        raise ProspectiveIntentionError("expires_at must be after due_at")
    if expiry - current > MAX_TTL:
        raise ProspectiveIntentionError("an intention may not live longer than 365 days")
    screened = await screen_user_memory_content(text, tenant_id=tenant_id)
    key = idempotency_key or uuid.uuid4().hex
    item = ProspectiveMemory(
        memory_id=prospective_id(tenant_id, key),
        tenant_id=tenant_id,
        intention=screened,
        due_at=due_at,
        expires_at=expiry,
        state="pending",
        source_goal_id=source_goal_id or "",
        source_execution_id=source_execution_id or "",
        policy_snapshot={"agent_id": agent_id} if agent_id else {},
        classification="internal",
        idempotency_key=key,
    )
    return await service.create(item)


def intention_json(item: ProspectiveMemory) -> dict[str, Any]:
    return {
        "id": item.memory_id,
        "intention": item.intention,
        "due_at": item.due_at.isoformat(),
        "expires_at": item.expires_at.isoformat(),
        "state": item.state,
        "source_goal_id": item.source_goal_id,
        "agent_id": (item.policy_snapshot or {}).get("agent_id"),
        "result": item.result,
    }


async def fire_due_intentions(
    service: Any,
    *,
    tenant_id: str,
    submit: GoalSubmitter,
    now: datetime | None = None,
    lease_duration: timedelta = timedelta(minutes=5),
    maximum_items: int = 50,
) -> list[ProspectiveMemory]:
    """Lease the tenant's due intentions, submit each as a goal, mark it completed.

    A submission failure leaves the item leased; the lease expires and a later
    run retries it (attempts counts tries). A lost fencing race (another worker
    re-leased it) is skipped, never double-completed.
    """
    when = now or datetime.now(UTC)
    claimed = await service.lease_due(tenant_id, now=when, lease_duration=lease_duration)
    fired: list[ProspectiveMemory] = []
    for item in claimed[:maximum_items]:
        try:
            result = await submit(item)
        except Exception as exc:
            _log.warning(
                "prospective_fire_failed",
                tenant_id=tenant_id,
                memory_id=item.memory_id,
                error=f"{type(exc).__name__}: {str(exc)[:200]}",
            )
            continue
        try:
            fired.append(
                await service.complete(
                    tenant_id,
                    item.memory_id,
                    fencing_token=item.fencing_token,
                    authorized=True,
                    result=result,
                )
            )
        except RuntimeError:
            _log.info("prospective_lease_lost", tenant_id=tenant_id, memory_id=item.memory_id)
    return fired


# ── Agent tool: builtin-memory ────────────────────────────────────────────────

SERVER_ID = "builtin-memory"
SERVER_NAME = "Agent Memory"
SERVER_DESCRIPTION = (
    "Defer work to later: schedule an intention the platform will run as a goal "
    "when it is due, and list the tenant's pending intentions."
)
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "defer_intention",
        "description": (
            "Remember to do something later. The intention is run as a new goal for "
            "this tenant when it is due (e.g. 'check the deploy status tomorrow')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "intention": {"type": "string", "description": "What to do later."},
                "due_in_minutes": {
                    "type": "integer",
                    "description": "Minutes from now until it is due (default 60).",
                    "minimum": 1,
                },
                "expires_in_days": {
                    "type": "integer",
                    "description": "Days after the due time before it is dropped (default 30).",
                    "minimum": 1,
                },
            },
            "required": ["intention"],
        },
    },
    {
        "name": "list_intentions",
        "description": "List this tenant's pending deferred intentions, due first.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
]

_service_override: Any = None


def set_prospective_service(service: Any) -> None:
    """Bind the process's prospective service (API lifespan / tests)."""
    global _service_override
    _service_override = service


def resolve_prospective_service() -> Any:
    """The bound service, else the durable Postgres one on the app's DB."""
    if _service_override is not None:
        return _service_override
    from app.db.session import get_session_factory
    from app.memory.prospective_postgres import PostgresProspectiveMemoryService

    return PostgresProspectiveMemoryService(get_session_factory())


async def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    credentials: dict[str, Any] | None = None,
    tenant_ctx: Any = None,
) -> dict[str, Any]:
    """Built-in handler. Tenant-bound: refuses to act without the caller's tenant."""
    tenant_id = getattr(tenant_ctx, "tenant_id", None)
    if not tenant_id:
        return {"error": "builtin-memory tools need the calling tenant"}
    service = resolve_prospective_service()
    args = arguments or {}
    try:
        if tool_name == "defer_intention":
            now = datetime.now(UTC)
            due = now + timedelta(minutes=max(1, int(args.get("due_in_minutes") or 60)))
            expires = due + timedelta(days=max(1, int(args.get("expires_in_days") or 30)))
            item = await create_intention(
                service,
                tenant_id=tenant_id,
                intention=str(args.get("intention") or ""),
                due_at=due,
                expires_at=expires,
                source_goal_id=str(args.get("_goal_id") or ""),
                now=now,
            )
            return {"deferred": intention_json(item)}
        if tool_name == "list_intentions":
            items = await service.list_active(tenant_id, now=datetime.now(UTC))
            return {"intentions": [intention_json(i) for i in items[:20]]}
    except Exception as exc:
        return {"error": f"{tool_name} failed: {exc}"}
    return {"error": f"Unknown memory tool: {tool_name!r}"}


# Tenant-scoped by construction: it acts only for the ``tenant_ctx`` MCPClient
# passes and reads no credentials or platform env. Marking it lets the registry's
# credential adapters (with_tenant_credentials / platform_scoped) hand it to the
# client unwrapped, so ``tenant_ctx`` reaches it.
call_tool._tenant_scoped = True  # type: ignore[attr-defined]


__all__ = [
    "SERVER_DESCRIPTION",
    "SERVER_ID",
    "SERVER_NAME",
    "TOOL_DEFINITIONS",
    "ProspectiveIntentionError",
    "call_tool",
    "create_intention",
    "fire_due_intentions",
    "intention_json",
    "resolve_prospective_service",
    "set_prospective_service",
]
