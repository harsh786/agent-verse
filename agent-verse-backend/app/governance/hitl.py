"""Human-In-The-Loop gateway.

Dual-mode implementation:
  * asyncio.Event (in-process) — backward-compatible path used by existing code.
  * Redis BLPOP (cross-replica) — new path; _wait_for_result / publish_resolution
    allow approval delivery across any replica in the fleet.

The Redis BLPOP path survives server restarts and works across multiple replicas
because the approval result is stored in a Redis list (not process memory).
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import json
import time
import uuid
from collections import OrderedDict
from collections.abc import Generator, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from app.tenancy.context import TenantContext


class ApprovalStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMED_OUT = "timed_out"


class _AwaitableBool:
    """Bool-like object that can also be awaited (P1.3 backward-compat approve return).

    Allows both sync usage (``ok = gateway.approve(...); assert ok``) and async
    usage (``ok = await gateway.approve(...)``) without API changes.
    """

    __slots__ = ("_value",)

    def __init__(self, value: bool) -> None:
        self._value = value

    def __bool__(self) -> bool:
        return self._value

    def __repr__(self) -> str:
        return repr(self._value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, bool):
            return self._value == other
        if isinstance(other, _AwaitableBool):
            return self._value == other._value
        return NotImplemented

    def __await__(self) -> Generator[Any, None, bool]:
        return self._value
        yield  # makes this a generator function so __await__ is valid


@dataclass(eq=False)
class ApprovalRequest:
    """An in-flight HITL approval request.

    Dual-mode: can be used as a plain string (via __str__/__eq__/__hash__) for
    backward compatibility with code that expects ``request_approval()`` to return
    a string ID.  Can also be awaited via ``await gateway.request_approval(...)``
    to obtain the full ``ApprovalRequest`` object in async contexts.
    """

    goal_id: str
    action: str
    risk_level: str
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: ApprovalStatus = ApprovalStatus.PENDING
    approver: str | None = None
    note: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    required_approvers: int = 1
    approvals_received: int = 0
    approvers_list: list[str] = field(default_factory=list)
    # asyncio.Event set when approved/rejected; created in running event loop context
    _event: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)
    # In-process expiry datetime (not persisted to DB directly)
    _expires_at_dt: Any = field(default=None, repr=False, compare=False)

    # ── Dual-mode (sync + await) support ──────────────────────────────────────

    def __await__(self) -> Generator[Any, None, ApprovalRequest]:
        """Enable ``req = await gateway.request_approval(...)``."""
        return self
        yield  # makes this a generator function — required for __await__

    def __str__(self) -> str:
        return self.request_id

    def __repr__(self) -> str:
        return f"ApprovalRequest(request_id={self.request_id!r}, status={self.status!r})"

    def __eq__(self, other: object) -> bool:
        """Allow comparison with plain request_id strings for backward compat."""
        if isinstance(other, str):
            return self.request_id == other
        if isinstance(other, ApprovalRequest):
            return self.request_id == other.request_id
        return NotImplemented

    def __hash__(self) -> int:
        """Hash equals hash(request_id) so ApprovalRequest works as a dict key."""
        return hash(self.request_id)


# Cap on how many phantom approvals one startup sweep expires, so a huge backlog
# cannot hold row locks on approval_requests for long while the API boots. Any
# remainder is picked up by the next sweep.
_PHANTOM_SWEEP_LIMIT = 5000

# Cross-tenant maintenance: expire pending approvals whose owning goal/mission
# already reached a terminal state. Those are phantoms — the work is done and
# there is nothing left to approve — but they would otherwise sit in every
# tenant's DB-backed pending list forever. Goal ownership is matched on tenant as
# well as id; org mission ids are globally unique UUIDs (and their tenant_id is a
# UUID column, not the 32-char text of approval_requests).
_EXPIRE_PHANTOMS_SQL = """
WITH phantom AS (
    SELECT ar.id
    FROM approval_requests ar
    WHERE ar.status = 'pending'
      AND ar.goal_id IS NOT NULL
      AND (
        EXISTS (
            SELECT 1 FROM goals g
            WHERE g.id = ar.goal_id
              AND g.tenant_id = ar.tenant_id
              AND g.status IN ('complete', 'failed', 'cancelled')
        )
        OR EXISTS (
            SELECT 1 FROM org_missions m
            WHERE m.id::text = ar.goal_id
              AND m.status IN ('completed', 'failed', 'cancelled', 'archived')
        )
      )
    ORDER BY ar.created_at
    LIMIT :lim
    FOR UPDATE OF ar SKIP LOCKED
)
UPDATE approval_requests AS ar
SET status = 'expired', resolved_at = NOW()
FROM phantom
WHERE ar.id = phantom.id AND ar.status = 'pending'
RETURNING ar.id, ar.tenant_id
"""

# approval_requests.status values → ApprovalStatus.
_STATUS_BY_DB_VALUE = {
    "pending": ApprovalStatus.PENDING,
    "approved": ApprovalStatus.APPROVED,
    "rejected": ApprovalStatus.REJECTED,
    "timed_out": ApprovalStatus.TIMED_OUT,
    "expired": ApprovalStatus.TIMED_OUT,
}


# Redis list a waiter BLPOPs for a decision taken on any replica.
HITL_RESULT_TTL_S = 86400


def hitl_result_key(request_id: str) -> str:
    return f"hitl_result:{request_id}"


_EXPIRED_RESOLUTION = json.dumps(
    {
        "action": "expired",
        "approver": "system:expiry",
        "note": "Approval expired before a decision was made",
    }
)


async def release_expired_waiters(redis: Any, request_ids: Iterable[str]) -> int:
    """Wake every goal blocked on these (already expired) approvals — any replica.

    The expiry beat only UPDATEd ``approval_requests`` to ``timed_out``; nothing
    pushed ``hitl_result:{id}``, so a goal waiting in the cross-replica BLPOP sat
    ``executing`` until its own (hour-long) HITL timeout (P5-4). The waiter maps
    ``expired`` to TIMED_OUT and the goal fails or replans honestly. Returns the
    number of waiters released; a failed push is logged (the DB row is already
    terminal, so a waiter that misses it still re-reads a terminal state when it
    times out).
    """
    if redis is None:
        return 0
    released = 0
    for request_id in request_ids:
        key = hitl_result_key(str(request_id))
        try:
            await redis.rpush(key, _EXPIRED_RESOLUTION)
            await redis.expire(key, HITL_RESULT_TTL_S)
            released += 1
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning(
                "hitl_expiry_release_failed", request_id=str(request_id), error=str(exc)[:200]
            )
    return released


class HITLResolutionUnavailableError(Exception):
    """The approval decision could not be written to the database.

    Raised instead of reporting success: the resolution used to return ``True``
    on a DB error (fail-open), so the caller answered 200 "approved" while the
    ``approval_requests`` row stayed ``pending`` — the decision was lost on the
    next restart / never seen by other replicas.
    """


class HITLWaitUnavailableError(Exception):
    """The cross-replica result channel (Redis BLPOP) failed mid-wait.

    ``_wait_for_result`` used to swallow the error and return ``None``, which
    ``wait_for_approval`` read as "resolved" and returned the still-PENDING
    status immediately -- callers that only blocked on REJECTED/TIMED_OUT then
    ran the gated action unapproved.
    """


class HITLDeliveryError(Exception):
    """A new approval request could not be made durable (``approval_requests``).

    Raised by :meth:`HITLGateway.request_approval_async` with
    ``require_persisted=True`` so a caller never reports a human was asked when
    the request exists only in one replica's memory.
    """


_RequestKey = tuple[str, str]


class _RequestCache(OrderedDict[_RequestKey, ApprovalRequest]):
    """LRU-bounded process-local approval cache (CORE-28).

    It used to be a plain dict every created or DB-read request was added to and
    never removed, so a long-running replica leaked memory proportional to total
    approval volume. Entries with a live in-process waiter are pinned. Without a
    DB the cache IS the store, so pending requests are never evicted there
    (``keep_pending``); with a DB, Postgres is the source of truth and anything
    unpinned may go.
    """

    def __init__(self, max_entries: int) -> None:
        super().__init__()
        self.max_entries = max(1, int(max_entries))
        self.keep_pending = True
        self._pins: dict[_RequestKey, int] = {}

    def __setitem__(self, key: _RequestKey, value: ApprovalRequest) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        self._shrink()

    def pin(self, key: _RequestKey) -> None:
        self._pins[key] = self._pins.get(key, 0) + 1

    def unpin(self, key: _RequestKey) -> int:
        """Drop one waiter's pin; returns the number of pins left."""
        left = self._pins.get(key, 0) - 1
        if left > 0:
            self._pins[key] = left
            return left
        self._pins.pop(key, None)
        return 0

    def _shrink(self) -> None:
        if len(self) <= self.max_entries:
            return
        for key in list(self.keys()):  # oldest first
            if len(self) <= self.max_entries:
                return
            if self._pins.get(key):
                continue
            if self.keep_pending and self[key].status == ApprovalStatus.PENDING:
                continue
            del self[key]


class HITLGateway:
    """Async-capable HITL gateway with blocking wait and timeout escalation."""

    DEFAULT_TIMEOUT = 300.0  # 5 minutes default
    # Upper bound on the process-local request cache (CORE-28).
    CACHE_MAX_ENTRIES = 5_000

    def __init__(
        self,
        timeout_seconds: float = DEFAULT_TIMEOUT,
        *,
        db_session_factory: Any = None,
        cache_max_entries: int = CACHE_MAX_ENTRIES,
    ) -> None:
        # Process-local cache only — NOT the source of truth. ``approval_requests``
        # in Postgres is, and the ``a*`` read methods (and ``reject``) consult it,
        # because a process-local dict is invisible to every other replica: an
        # approval created on replica B could not be found or listed on replica A,
        # and one resolved on B still read as pending on A until A restarted.
        # It is filled lazily, per tenant, by those DB reads; there is no
        # cross-tenant warm scan at startup (under the API's NOBYPASSRLS role it
        # could not see any rows anyway). Bounded (CORE-28): see _RequestCache.
        self._requests: _RequestCache = _RequestCache(cache_max_entries)
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._timeout = timeout_seconds
        self._notification_service: Any = None
        self._db_session_factory = db_session_factory
        # Redis client for publishing rejection notes (set by create_app lifespan)
        self._redis: Any = None

    @property
    def _db_session_factory(self) -> Any:
        return self._db_factory

    @_db_session_factory.setter
    def _db_session_factory(self, value: Any) -> None:
        # The lifespan binds the DB after construction. Once Postgres is the
        # source of truth, unwaited pending requests may leave the cache too.
        self._db_factory = value
        self._requests.keep_pending = value is None

    def request_approval(
        self,
        *,
        goal_id: str,
        action: str = "",
        step_description: str = "",  # P1.3: alias for action
        risk_level: str = "high",
        tenant_ctx: TenantContext,
        required_approvers: int = 1,  # P1.3: multi-person approval threshold
        context: dict[str, Any] | None = None,
    ) -> ApprovalRequest:
        """Create an approval request and return it (non-blocking).

        The returned ``ApprovalRequest`` is dual-mode:
        - Sync:  ``req_id = gateway.request_approval(...)`` → req_id acts like a string
        - Async: ``req   = await gateway.request_approval(...)`` → req is ApprovalRequest

        Also sets ``_expires_at_dt`` for in-process timeout enforcement.
        """
        req = self._new_request(
            goal_id=goal_id,
            action=step_description or action,  # accept either param name
            risk_level=risk_level,
            tenant_ctx=tenant_ctx,
            required_approvers=required_approvers,
        )
        # Persist to DB in the background (legacy sync callers). Agent gates use
        # request_approval_async, which awaits the write before anyone waits.
        if self._db_session_factory is not None:
            self._spawn_background(
                self._db_persist_approval_request(req, tenant_ctx.tenant_id)
            )
        self._notify_approval_required(req, tenant_ctx.tenant_id)
        return req  # ApprovalRequest: awaitable + string-compatible via __str__/__eq__/__hash__

    def _new_request(
        self,
        *,
        goal_id: str,
        action: str,
        risk_level: str,
        tenant_ctx: TenantContext,
        required_approvers: int,
    ) -> ApprovalRequest:
        """Build a request, cache it locally, and stamp its expiry."""
        from datetime import timedelta

        req = ApprovalRequest(
            goal_id=goal_id,
            action=action,
            risk_level=risk_level,
            required_approvers=required_approvers,
        )
        req._expires_at_dt = datetime.now(UTC) + timedelta(seconds=self._timeout)
        self._requests[(tenant_ctx.tenant_id, req.request_id)] = req
        return req

    def _spawn_background(self, coro: Any) -> None:
        """Run *coro* as a referenced background task whose failure is logged.

        A bare ``loop.create_task`` result can be garbage-collected mid-flight
        and its exception was never observed.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()  # no running loop: nothing can run it
            return
        task = loop.create_task(coro)
        self._background_tasks.add(task)

        def _done(t: asyncio.Task[Any]) -> None:
            self._background_tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                from app.observability.logging import get_logger

                get_logger(__name__).warning(
                    "hitl_background_task_failed", error=str(t.exception())[:200]
                )

        task.add_done_callback(_done)

    def _notify_approval_required(self, req: ApprovalRequest, tenant_id: str) -> None:
        if self._notification_service is None:
            return
        self._spawn_background(
            self._notification_service.notify_approval_required(
                request_id=req.request_id,
                goal_id=req.goal_id,
                action=req.action,
                risk_level=req.risk_level,
                tenant_id=tenant_id,
            )
        )

    async def _db_update_resolution(
        self,
        request_id: str,
        tenant_id: str,
        status: str,
        approver: str = "",
        note: str = "",
    ) -> bool:
        """Persist a resolution (approved/rejected/expired) to the DB row.

        Without this, ``approve``/``reject`` only mutated the in-memory request;
        the ``approval_requests`` row stayed ``pending`` and ``startup_restore``
        re-hydrated it on the next restart — so an approved gate reappeared in the
        inbox. Writing the terminal status here makes an approval durable.

        Returns ``True`` when this call's UPDATE actually flipped the row (i.e. it
        won the ``WHERE status = 'pending'`` compare-and-swap), ``False`` when the
        row was already resolved by someone else (another replica, or a racing
        approve/reject on this one) — the caller uses this to detect a lost
        cross-replica race instead of assuming its own decision took effect.
        When no DB is configured there is no cross-replica concern, so this
        returns ``True`` (matches the pre-existing in-memory-only behavior).

        Raises:
            HITLResolutionUnavailableError: the DB write failed. (It used to
                return ``True`` — fail-open — so a decision that was never
                recorded was reported as taking effect.)
        """
        if self._db_session_factory is None:
            return True
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db_session_factory() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                result = await session.execute(
                    text(
                        "UPDATE approval_requests "
                        "SET status = :s, approver = :a, note = :n, resolved_at = NOW() "
                        "WHERE id = :id AND tenant_id = :tid AND status = 'pending'"
                    ),
                    {
                        "s": status,
                        "a": approver or None,
                        "n": note or "",
                        "id": request_id,
                        "tid": tenant_id,
                    },
                )
            return bool(result.rowcount)
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("hitl_db_resolve_failed", error=str(exc))
            raise HITLResolutionUnavailableError(str(exc)) from exc

    async def _db_read_status(self, request_id: str, tenant_id: str) -> str | None:
        """Read the DB-authoritative status for a request, or ``None`` if unavailable."""
        if self._db_session_factory is None:
            return None
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db_session_factory() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                row = (
                    await session.execute(
                        text(
                            "SELECT status FROM approval_requests "
                            "WHERE id = :id AND tenant_id = :tid"
                        ),
                        {"id": request_id, "tid": tenant_id},
                    )
                ).first()
            return str(row[0]) if row else None
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("hitl_db_read_status_failed", error=str(exc))
            return None

    async def _reconcile_after_db_resolution(
        self, request_id: str, tenant_id: str, status: str, approver: str, note: str
    ) -> None:
        """Persist a resolution and self-heal local state on a lost cross-replica race.

        If this replica's write lost the DB compare-and-swap (another replica —
        or another racing call on this one — already resolved the request with
        a possibly *different* outcome), the local in-memory copy would
        otherwise stay stuck on the outcome this call optimistically assumed,
        forever disagreeing with the DB and with any other replica. Re-reading
        the DB's real status and applying it locally makes any late waiter
        (asyncio.Event or a fresh ``wait_for_approval`` call on this replica)
        observe the true, DB-arbitrated decision instead of a phantom one.
        """
        try:
            won = await self._db_update_resolution(request_id, tenant_id, status, approver, note)
        except HITLResolutionUnavailableError:
            return  # logged by _db_update_resolution; the beat/restore path re-reads
        if won:
            return
        await self._heal_from_db(request_id, tenant_id, status)

    async def _heal_from_db(self, request_id: str, tenant_id: str, status: str) -> None:
        """Apply the DB's real status locally after losing the resolution CAS."""
        real_status = await self._db_read_status(request_id, tenant_id)
        mapped = {
            "approved": ApprovalStatus.APPROVED,
            "rejected": ApprovalStatus.REJECTED,
            "timed_out": ApprovalStatus.TIMED_OUT,
            "expired": ApprovalStatus.TIMED_OUT,
        }.get(real_status or "")
        req = self._requests.get((tenant_id, request_id))
        if mapped is None or req is None or req.status == mapped:
            return
        from app.observability.logging import get_logger

        get_logger(__name__).warning(
            "hitl_cross_replica_conflict_resolved",
            request_id=request_id,
            attempted=status,
            actual=real_status,
        )
        req.status = mapped
        req._event.set()

    def _schedule_db_resolution(
        self, request_id: str, tenant_id: str, status: str, approver: str = "", note: str = ""
    ) -> None:
        """Fire-and-forget the DB resolution write (same pattern as create-persist).

        Uses ``_reconcile_after_db_resolution`` rather than a bare
        ``_db_update_resolution`` call so that a lost cross-replica CAS race is
        self-healed instead of leaving this replica's in-memory state stuck on
        an outcome the DB never actually recorded.
        """
        if self._db_session_factory is None:
            return
        import asyncio as _aio

        try:
            loop = _aio.get_running_loop()
            loop.create_task(  # noqa: RUF006
                self._reconcile_after_db_resolution(request_id, tenant_id, status, approver, note)
            )
        except RuntimeError:
            pass  # No running loop (shouldn't happen in async context)

    async def _db_persist_approval_request(
        self, req: ApprovalRequest, tenant_id: str, *, raise_errors: bool = False
    ) -> None:
        """Persist new approval request to DB (Fix 4).

        Best-effort by default (logged); ``raise_errors`` re-raises the failure.
        """
        if self._db_session_factory is None:
            return
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            # approval_requests is FORCE-RLS protected: without app.tenant_id the
            # INSERT matches no policy under the app's own least-privilege role,
            # and this whole method is fire-and-forget behind a broad except —
            # so the approval would silently never persist at all.
            async with (
                self._db_session_factory() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                await session.execute(
                    text(
                        """INSERT INTO approval_requests
                            (id, tenant_id, goal_id, action, risk_level, status,
                             created_at, expires_at, required_approvers)
                            VALUES (:id, :tid, :gid, :action, :risk, 'pending',
                                    NOW(), :expires_at, :required)
                            ON CONFLICT (id) DO NOTHING"""
                    ),
                    {
                        "id": req.request_id,
                        "tid": tenant_id,
                        "gid": req.goal_id,
                        "action": req.action,
                        "risk": req.risk_level,
                        # Persisted so every replica applies the same threshold
                        # (it used to exist only in the raising process's memory).
                        "required": max(1, int(req.required_approvers or 1)),
                        # The beat expiry (expire_hitl_approvals) matches
                        # ``expires_at < NOW()``; without it no row ever expired.
                        "expires_at": req._expires_at_dt,
                    },
                )
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("hitl_db_persist_failed", error=str(exc))
            if raise_errors:
                raise

    async def wait_for_approval(
        self,
        request_id: str,
        *,
        tenant_ctx: TenantContext,
        timeout: float | None = None,
    ) -> ApprovalStatus:
        """Block until the request is resolved or timeout expires.

        Dual-listen strategy (C6.2): races the in-process asyncio.Event against a
        Redis BLPOP so approvals from *any* replica unblock this waiter.

        The timeout path uses a CAS guard — TIMED_OUT is only written when the
        request is still PENDING, preventing it from overwriting a concurrent
        APPROVED (H17).

        Returns the final ApprovalStatus (APPROVED, REJECTED, or TIMED_OUT) --
        never PENDING. Fails closed: when the Redis channel errors mid-wait the
        waiter keeps listening on the in-process event and polls the
        DB-authoritative status until the deadline; it returns APPROVED only
        when an approval was actually observed.
        """
        key = (tenant_ctx.tenant_id, request_id)
        req = self._requests.get(key)
        if req is None and self._db_session_factory is not None:
            # Raised on another replica (or this process restarted): Postgres is
            # the source of truth, so an id missing locally is not a rejection.
            req = await self.aget_request(request_id, tenant_ctx=tenant_ctx)
        if req is None:
            return ApprovalStatus.REJECTED
        if req.status != ApprovalStatus.PENDING:
            return req.status

        # CORE-28: the cache holds what a live waiter needs. Pin this request
        # (so LRU churn cannot evict it while we wait — a local approve/reject
        # must still find this very object) and drop it once resolved.
        self._requests.pin(key)
        self._requests[key] = req
        try:
            return await self._wait_pinned(req, request_id, tenant_ctx, timeout)
        finally:
            if self._requests.unpin(key) == 0 and self._db_session_factory is not None:
                cached = self._requests.get(key)
                if cached is not None and cached.status != ApprovalStatus.PENDING:
                    self._requests.pop(key, None)

    async def _wait_pinned(
        self,
        req: ApprovalRequest,
        request_id: str,
        tenant_ctx: TenantContext,
        timeout: float | None,
    ) -> ApprovalStatus:
        """The body of :meth:`wait_for_approval` once *req* is pinned in the cache."""
        timeout_s = timeout if timeout is not None else self._timeout
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s

        # Task 1: in-process event (local replica or same-process approve/reject)
        local_task: asyncio.Task[Any] = asyncio.create_task(req._event.wait())

        # Task 2: Redis BLPOP (cross-replica approval delivery)
        redis_task: asyncio.Task[Any] | None = None
        if self._redis is not None:
            redis_task = asyncio.create_task(self._wait_for_result(request_id, timeout=timeout_s))

        tasks: list[asyncio.Task[Any]] = [local_task]
        if redis_task is not None:
            tasks.append(redis_task)

        try:
            if redis_task is None and self._db_session_factory is not None:
                # No Redis channel: a decision taken on another replica only
                # reaches this waiter through Postgres, so poll it alongside
                # the local event (the waiter used to know only its process).
                await self._wait_local_or_db(req, request_id, tenant_ctx, local_task, deadline)
                done: set[asyncio.Task[Any]] = set()
            else:
                done, _pending = await asyncio.wait(
                    tasks,
                    timeout=timeout_s,
                    return_when=asyncio.FIRST_COMPLETED,
                )

            # If Redis BLPOP resolved first, update local status from the payload (C6.2)
            redis_failed = False
            if redis_task is not None and redis_task in done and not local_task.done():
                try:
                    redis_result = redis_task.result()
                except Exception as exc:
                    from app.observability.logging import get_logger

                    get_logger(__name__).warning(
                        "hitl_wait_redis_unavailable", request_id=request_id, error=str(exc)
                    )
                    redis_failed = True
                    redis_result = None
                if redis_result and isinstance(redis_result, dict):
                    action = redis_result.get("action", "")
                    if action == "approved" and req.status == ApprovalStatus.PENDING:
                        req.status = ApprovalStatus.APPROVED
                        req._event.set()
                    elif action == "rejected" and req.status == ApprovalStatus.PENDING:
                        req.status = ApprovalStatus.REJECTED
                        req._event.set()
                    elif (
                        action in ("expired", "timed_out")
                        and req.status == ApprovalStatus.PENDING
                    ):
                        # Expired by the beat on any replica (P5-4).
                        req.status = ApprovalStatus.TIMED_OUT
                        req._event.set()
                elif redis_failed and req.status == ApprovalStatus.PENDING:
                    # Redis is down: keep waiting for a local decision and poll the
                    # DB so a resolution on another replica is still observed.
                    await self._wait_local_or_db(req, request_id, tenant_ctx, local_task, deadline)
        except asyncio.CancelledError:
            local_task.cancel()
            if redis_task is not None:
                redis_task.cancel()
            raise
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await t

        if req.status == ApprovalStatus.PENDING:
            # Timeout (or an undecided wait). HITL-09: the expiry is written to
            # the DB row with the same pending-only compare-and-swap, so an
            # approver can no longer "approve" (and be told approved) an action
            # the agent has abandoned. A decision that won the race first is
            # honoured instead.
            final = ApprovalStatus.TIMED_OUT
            try:
                won = await self._db_update_resolution(
                    request_id, tenant_ctx.tenant_id, "expired", "system:timeout"
                )
                if not won:
                    db_status = await self._db_read_status(request_id, tenant_ctx.tenant_id)
                    mapped = _STATUS_BY_DB_VALUE.get(str(db_status or ""))
                    if mapped is not None and mapped != ApprovalStatus.PENDING:
                        final = mapped
            except Exception as exc:
                from app.observability.logging import get_logger

                get_logger(__name__).error(
                    "hitl_timeout_not_persisted", request_id=request_id, error=str(exc)[:200]
                )
            if req.status == ApprovalStatus.PENDING:  # CAS guard (H17)
                req.status = final
            req._event.set()  # Unblock any other waiters on this request
        return req.status

    _DB_POLL_INTERVAL_S = 2.0

    async def _wait_local_or_db(
        self,
        req: ApprovalRequest,
        request_id: str,
        tenant_ctx: TenantContext,
        local_task: asyncio.Task[Any],
        deadline: float,
    ) -> None:
        """Fallback wait when Redis failed: local event + DB status polling."""
        loop = asyncio.get_running_loop()
        while req.status == ApprovalStatus.PENDING:
            if self._db_session_factory is not None:
                db_status = _STATUS_BY_DB_VALUE.get(
                    await self._db_read_status(request_id, tenant_ctx.tenant_id) or ""
                )
                if db_status is not None and db_status != ApprovalStatus.PENDING:
                    req.status = db_status
                    req._event.set()
                    return
            remaining = deadline - loop.time()
            if remaining <= 0 or local_task.done():
                return
            await asyncio.wait({local_task}, timeout=min(remaining, self._DB_POLL_INTERVAL_S))

    def get_request(self, request_id: str, *, tenant_ctx: TenantContext) -> ApprovalRequest | None:
        """Process-local lookup. Prefer :meth:`aget_request` on any request path.

        This sees only what THIS process created or hydrated at startup, so it
        cannot find an approval another replica raised. It is kept for the
        in-process agent paths that look up a request they just created
        themselves, where a database round-trip would be pure latency.
        """
        return self._requests.get((tenant_ctx.tenant_id, request_id))

    async def aget_request(
        self, request_id: str, *, tenant_ctx: TenantContext
    ) -> ApprovalRequest | None:
        """Look the request up in Postgres, falling back to the local cache.

        ``approval_requests`` is the source of truth; ``self._requests`` is one
        process's view of it. Reading only the dict meant an approval created on
        another replica answered 404 on this one, and one resolved elsewhere
        still read as pending here until restart.
        """
        cached = self._requests.get((tenant_ctx.tenant_id, request_id))
        if self._db_session_factory is None:
            return cached
        row = await self._db_fetch_request(request_id, tenant_ctx.tenant_id)
        if row is None:
            return None
        return self._merge_row(row, tenant_ctx.tenant_id, cached)

    async def alist_pending(
        self,
        *,
        tenant_ctx: TenantContext,
        goal_id: str | None = None,
        limit: int = 200,
    ) -> list[ApprovalRequest]:
        """Pending approvals for the tenant, read from Postgres.

        Scoped by ``tenant_id`` and bounded, so one tenant's queue depth cannot
        become another's latency (or this replica's memory).
        """
        if self._db_session_factory is None:
            return self.list_pending(tenant_ctx=tenant_ctx, goal_id=goal_id)

        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        clause = " AND goal_id = :gid" if goal_id else ""
        params: dict[str, Any] = {
            "tid": tenant_ctx.tenant_id,
            "lim": max(1, min(int(limit), 500)),
        }
        if goal_id:
            params["gid"] = goal_id
        try:
            async with (
                self._db_session_factory() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
                rows = (
                    (
                        await session.execute(
                            text(
                                "SELECT id, tenant_id, goal_id, action, risk_level, status "
                                "FROM approval_requests "
                                "WHERE tenant_id = :tid AND status = 'pending'"
                                f"{clause} "
                                "ORDER BY created_at DESC LIMIT :lim"
                            ),
                            params,
                        )
                    )
                    .mappings()
                    .all()
                )
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("hitl_list_pending_db_failed", error=str(exc))
            return self.list_pending(tenant_ctx=tenant_ctx, goal_id=goal_id)

        return [
            self._merge_row(
                row,
                tenant_ctx.tenant_id,
                self._requests.get((tenant_ctx.tenant_id, row["id"])),
            )
            for row in rows
        ]

    async def approve_async(
        self,
        request_id: str,
        *,
        approver: str,
        note: str = "",
        tenant_ctx: TenantContext,
    ) -> bool:
        """Approve, resolving the request against Postgres, and await the write.

        Two things the sync :meth:`approve` cannot do on a request this replica
        did not raise:

        * find it at all — ``approve`` looks the request up in the process-local
          dict, so an operator whose call is load-balanced to a different replica
          than the one that raised the gate got ``False`` for a live approval;
        * make the decision durable before returning — the DB compare-and-swap is
          scheduled fire-and-forget, so an immediately following read on another
          replica still saw ``pending``.

        This resolves through the database first (populating the cache) and then
        awaits the resolution write, so when it returns the decision is committed
        and visible fleet-wide.
        """
        req = await self.aget_request(request_id, tenant_ctx=tenant_ctx)
        if req is None or req.status != ApprovalStatus.PENDING:
            return False
        if self._db_session_factory is None:
            return bool(
                self.approve(request_id, approver=approver, note=note, tenant_ctx=tenant_ctx)
            )
        required = max(1, int(req.required_approvers or 1))
        if required > 1:
            # Multi-approver gate: the vote is recorded in Postgres (one row per
            # distinct approver) and counted there, so votes cast on different
            # replicas — or before a restart — add up. They used to be counted
            # in this process's memory only. A DB error raises
            # HITLResolutionUnavailableError (fail closed).
            votes = await self._db_cast_vote(request_id, tenant_ctx.tenant_id, approver, note)
            if approver not in req.approvers_list:
                req.approvers_list.append(approver)
            req.approvals_received = max(req.approvals_received, votes)
            req.approver = approver
            req.note = note
            if votes < required:
                return True  # vote recorded; the gate stays closed
        # This vote resolves the gate: win the DB compare-and-swap FIRST, then
        # unblock the waiting agent. Approving locally first let the agent run
        # the gated action even when the decision was never recorded (DB error
        # → raises) or another replica had already rejected it (lost CAS).
        won = await self._db_update_resolution(
            request_id, tenant_ctx.tenant_id, "approved", approver, note
        )
        if not won:
            await self._heal_from_db(request_id, tenant_ctx.tenant_id, "approved")
            return False
        if required > 1:
            # Threshold reached across replicas: mark every counted vote locally
            # so _apply_approval releases the gate.
            req.approvals_received = max(req.approvals_received, required - 1)
            if approver in req.approvers_list:
                req.approvers_list.remove(approver)
        return bool(
            self._apply_approval(
                req, approver=approver, note=note, tenant_ctx=tenant_ctx, persist=False
            )
        )

    async def _db_cast_vote(
        self, request_id: str, tenant_id: str, approver: str, note: str
    ) -> int:
        """Record *approver*'s vote (idempotent) and return the distinct vote count.

        Insert + count run in one transaction under the tenant's RLS context;
        the primary key (tenant_id, request_id, approver) makes a repeated vote
        a no-op, so the count is the number of DISTINCT approvers fleet-wide.

        Raises:
            HITLResolutionUnavailableError: the vote could not be recorded.
        """
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db_session_factory() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                # HITL-08: serialise the votes on this request. Under READ
                # COMMITTED two concurrent final votes could each INSERT and then
                # COUNT before seeing the other's row — both below quorum, nobody
                # resolved, and the gate expired. The row lock makes the second
                # voter count after the first commits.
                await session.execute(
                    text(
                        "SELECT 1 FROM approval_requests "
                        "WHERE id = :rid AND tenant_id = :tid FOR UPDATE"
                    ),
                    {"tid": tenant_id, "rid": request_id},
                )
                await session.execute(
                    text(
                        "INSERT INTO approval_votes (tenant_id, request_id, approver, note) "
                        "VALUES (:tid, :rid, :a, :n) "
                        "ON CONFLICT (tenant_id, request_id, approver) DO NOTHING"
                    ),
                    {"tid": tenant_id, "rid": request_id, "a": approver or "", "n": note or ""},
                )
                count = (
                    await session.execute(
                        text(
                            "SELECT COUNT(*) FROM approval_votes "
                            "WHERE tenant_id = :tid AND request_id = :rid"
                        ),
                        {"tid": tenant_id, "rid": request_id},
                    )
                ).scalar()
            return int(count or 0)
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("hitl_db_vote_failed", error=str(exc))
            raise HITLResolutionUnavailableError(str(exc)) from exc

    async def aresolve_request_tenant(self, request_id: str, system_db: Any) -> str | None:
        """The tenant owning *request_id*: local cache first, then Postgres.

        For signed links (email approve/reject) that carry no tenant: the
        request may have been raised on another replica, so the process-local
        cache is not enough. The lookup is by primary key on the maintenance
        (RLS-bypassing) session; the caller has already verified the link's
        HMAC for this exact request id.
        """
        for (tid, rid) in self._requests:
            if rid == request_id:
                return tid
        if system_db is None:
            return None
        try:
            from sqlalchemy import text

            async with system_db() as session:
                row = (
                    await session.execute(
                        text("SELECT tenant_id FROM approval_requests WHERE id = :id"),
                        {"id": request_id},
                    )
                ).first()
            return str(row[0]) if row else None
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("hitl_resolve_tenant_failed", error=str(exc))
            raise HITLResolutionUnavailableError(str(exc)) from exc

    async def request_approval_async(
        self,
        *,
        goal_id: str,
        action: str = "",
        step_description: str = "",
        risk_level: str = "high",
        tenant_ctx: TenantContext,
        required_approvers: int = 1,
        context: dict[str, Any] | None = None,
        require_persisted: bool = False,
    ) -> str:
        """Create an approval and **await** its persistence; return the request id.

        :meth:`request_approval` persists fire-and-forget, so the row may not
        exist yet when the caller returns — another replica asked about it
        immediately afterwards would legitimately not find it. Callers that need
        the gate to be durable and visible before they proceed use this.

        ``require_persisted``: when the gateway is DB-backed and the row cannot
        be written, drop the request and raise :class:`HITLDeliveryError`
        instead of returning an id nobody else can see.
        """
        req = self._new_request(
            goal_id=goal_id,
            action=step_description or action,
            risk_level=risk_level,
            tenant_ctx=tenant_ctx,
            required_approvers=required_approvers,
        )
        # Persisted exactly once, awaited (request_approval also scheduled a
        # background write, so every async request used to be inserted twice).
        if not require_persisted:
            await self._db_persist_approval_request(req, tenant_ctx.tenant_id)
        else:
            try:
                await self._db_persist_approval_request(
                    req, tenant_ctx.tenant_id, raise_errors=True
                )
            except Exception as exc:
                self._requests.pop((tenant_ctx.tenant_id, req.request_id), None)
                raise HITLDeliveryError(
                    f"approval request could not be persisted: {exc}"
                ) from exc
        # Notify only once the request exists for every replica.
        self._notify_approval_required(req, tenant_ctx.tenant_id)
        return str(req.request_id)

    async def _db_fetch_request(self, request_id: str, tenant_id: str) -> Any:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        try:
            async with (
                self._db_session_factory() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                return (
                    (
                        await session.execute(
                            text(
                                "SELECT id, tenant_id, goal_id, action, risk_level, status, "
                                "required_approvers "
                                "FROM approval_requests WHERE id = :id AND tenant_id = :tid"
                            ),
                            {"id": request_id, "tid": tenant_id},
                        )
                    )
                    .mappings()
                    .first()
                )
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("hitl_db_fetch_failed", error=str(exc))
            return None

    def _merge_row(
        self, row: Any, tenant_id: str, cached: ApprovalRequest | None
    ) -> ApprovalRequest:
        """Return the cached request updated from ``row``, or one built from it.

        Reusing the cached object matters: an in-process ``wait_for_approval``
        holds a reference to it and is waiting on its ``_event``.
        """
        status = _STATUS_BY_DB_VALUE.get(str(row["status"] or ""), ApprovalStatus.PENDING)
        try:
            required = max(1, int(row.get("required_approvers") or 1))
        except (TypeError, ValueError, AttributeError):
            required = 1
        if cached is not None:
            # The DB threshold is authoritative (never lower a local one).
            cached.required_approvers = max(cached.required_approvers, required)
            if cached.status != status:
                cached.status = status
                cached._event.set()
            return cached
        # Not cached (CORE-28): a row that only passes through a read has no
        # in-process waiter, and Postgres stays the source of truth. A waiter
        # adds its request itself (wait_for_approval).
        return ApprovalRequest(
            goal_id=row["goal_id"],
            action=row["action"] or "unknown",
            risk_level=row["risk_level"] or "unknown",
            request_id=row["id"],
            status=status,
            required_approvers=required,
        )

    def approve(
        self,
        request_id: str,
        *,
        approver: str,
        note: str = "",
        tenant_ctx: TenantContext,
    ) -> _AwaitableBool:
        """Approve a request. Returns an _AwaitableBool (truthy on success).

        Works in both sync and async contexts:
        - Sync: ``ok = gateway.approve(...); assert ok``
        - Async: ``ok = await gateway.approve(...)``

        Process-local: it only sees requests this process raised or has already
        read from the DB. Request paths use :meth:`approve_async`, which resolves
        the request in Postgres first (there is no startup warm-up of the cache).
        """
        req = self.get_request(request_id, tenant_ctx=tenant_ctx)
        if req is None:
            return _AwaitableBool(False)
        return self._apply_approval(
            req, approver=approver, note=note, tenant_ctx=tenant_ctx, persist=True
        )

    def _apply_approval(
        self,
        req: ApprovalRequest,
        *,
        approver: str,
        note: str,
        tenant_ctx: TenantContext,
        persist: bool,
    ) -> _AwaitableBool:
        """Record one approver's vote locally (and, when *persist*, schedule the write)."""
        if req.status != ApprovalStatus.PENDING:
            return _AwaitableBool(False)
        # Track approvers (prevent duplicate votes)
        if approver not in req.approvers_list:
            req.approvers_list.append(approver)
            req.approvals_received += 1
        req.approver = approver  # Last approver
        req.note = note
        # Only set APPROVED when threshold reached
        if req.approvals_received >= req.required_approvers:
            req.status = ApprovalStatus.APPROVED
            req._event.set()  # Unblock waiting agent
            # Durably record the resolution so it survives a restart (otherwise
            # startup_restore re-loads the still-'pending' DB row and the approved
            # gate reappears in the inbox).
            if persist:
                self._schedule_db_resolution(
                    req.request_id, tenant_ctx.tenant_id, "approved", approver, note
                )
            # C6.1: Publish cross-replica notification via Redis BLPOP
            if self._redis is not None:
                try:
                    loop = asyncio.get_running_loop()
                    _task = loop.create_task(  # noqa: RUF006
                        self.publish_resolution(
                            request_id=req.request_id,
                            action="approved",
                            approver=approver,
                            note=note,
                        )
                    )
                    _trig = loop.create_task(  # noqa: RUF006
                        self._publish_trigger_event("hitl.approved", req, tenant_ctx)
                    )
                except RuntimeError:
                    pass  # No running loop (sync-only context) — skip Redis publish
        return _AwaitableBool(True)

    async def reject(
        self,
        request_id: str,
        *,
        approver: str = "",
        note: str = "",
        tenant_ctx: TenantContext,
    ) -> bool:
        req = self.get_request(request_id, tenant_ctx=tenant_ctx)
        if req is None and self._db_session_factory is not None:
            # Not raised by (or cached on) this process — e.g. created on another
            # replica, or before a restart. Postgres is the source of truth, so
            # resolve it there (tenant-scoped, under RLS) rather than answering
            # "not found" for a live approval.
            req = await self.aget_request(request_id, tenant_ctx=tenant_ctx)
        if req is None or req.status != ApprovalStatus.PENDING:
            return False

        # Cross-replica CAS guard: gate the *local* transition on the DB write
        # actually winning (rowcount > 0), instead of committing to REJECTED
        # and reporting success before we know whether another replica (or a
        # racing approve() on this one) already resolved the same request with
        # a different outcome. Without this, two operators hitting two
        # different pods — one clicking Approve, one clicking Reject on the
        # same request — could both get a 200 success response with
        # contradictory decisions, even though only one can really be true in
        # the DB. Awaiting it here (reject() is always called with `await`)
        # means the loser correctly reports failure instead of a false
        # success.
        if not await self._db_update_resolution(
            request_id, tenant_ctx.tenant_id, "rejected", approver, note
        ):
            return False

        req.status = ApprovalStatus.REJECTED
        req.approver = approver
        req.note = note
        req._event.set()  # Unblock waiting agent

        # Phase 12: Publish rejection with note so goal_service can forward to planner
        if self._redis is not None:
            try:
                import json
                from datetime import UTC, datetime

                await self._redis.publish(
                    f"hitl_rejected:{req.goal_id}",
                    json.dumps(
                        {
                            "request_id": str(request_id),
                            "goal_id": req.goal_id,
                            "note": note,
                            "rejected_by": getattr(tenant_ctx, "api_key_id", "unknown"),
                            "ts": datetime.now(UTC).isoformat(),
                        }
                    ),
                )
            except Exception:
                pass
            # C6.2: Also publish to the BLPOP result key for cross-replica waiters
            with contextlib.suppress(Exception):
                await self.publish_resolution(
                    request_id=req.request_id,
                    action="rejected",
                    approver=approver,
                    note=note,
                )
            await self._publish_trigger_event("hitl.rejected", req, tenant_ctx)

        return True

    def list_pending(
        self,
        *,
        tenant_ctx: TenantContext,
        goal_id: str | None = None,
    ) -> list[ApprovalRequest]:
        """Return pending approval requests for the tenant, from this process's cache.

        When *goal_id* is given only requests belonging to that goal are
        returned (C6.4 — prevents goal-B's pending approval from pausing goal-A).
        Request paths use :meth:`alist_pending`, which reads Postgres.
        """
        return [
            req
            for (tid, _), req in self._requests.items()
            if tid == tenant_ctx.tenant_id
            and req.status == ApprovalStatus.PENDING
            and (goal_id is None or req.goal_id == goal_id)
        ]

    # ------------------------------------------------------------------
    # Cross-replica HITL delivery (Redis BLPOP)
    # ------------------------------------------------------------------

    async def _wait_for_result(self, request_id: str, timeout: float) -> dict[str, Any] | None:
        """Wait for a HITL result via Redis BLPOP.

        Blocks up to *timeout* seconds by issuing repeated BLPOP calls with a
        maximum window of 5 s each.  Returns the decoded payload dict on
        success, or ``None`` on timeout.

        This is the cross-replica safe alternative to ``wait_for_approval``.
        The approver writes the payload via ``publish_resolution``.
        """
        if self._redis is None:
            return None

        result_key = f"hitl_result:{request_id}"
        deadline = time.time() + timeout

        while time.time() < deadline:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                blpop_timeout = min(max(remaining, 0.1), 5.0)
                result = await self._redis.blpop(result_key, timeout=blpop_timeout)
                if result:
                    _, data = result
                    return cast(
                        "dict[str, Any]",
                        json.loads(data.decode() if isinstance(data, bytes) else data),
                    )
            except Exception as exc:
                from app.observability.logging import get_logger

                get_logger(__name__).warning("hitl_blpop_error", error=str(exc))
                # Fail closed: an error is NOT "no decision yet". Returning None
                # here let wait_for_approval hand back PENDING immediately.
                raise HITLWaitUnavailableError(str(exc)) from exc

        return None

    async def _goal_agent_id(self, goal_id: str, tenant_id: str) -> str | None:
        """The agent that owns *goal_id* (for its ``agent:<id>`` HITL queue), or None.

        Best effort: without a DB, or when the lookup fails, the event still
        carries the ``risk:<tier>`` queue.
        """
        if self._db_session_factory is None or not goal_id:
            return None
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db_session_factory() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                row = (
                    await session.execute(
                        text("SELECT agent_id FROM goals WHERE id = :gid AND tenant_id = :tid"),
                        {"gid": goal_id, "tid": tenant_id},
                    )
                ).fetchone()
            return str(row[0]) if row is not None and row[0] else None
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("hitl_goal_agent_lookup_failed", error=str(exc))
            return None

    async def _publish_trigger_event(
        self, channel: str, req: ApprovalRequest, tenant_ctx: TenantContext
    ) -> None:
        """Publish ``hitl.approved`` / ``hitl.rejected`` for HITLTriggerConsumer.

        The consumer subscribed to these channels but nothing published them, so
        HITL_APPROVED / HITL_REJECTED triggers could never fire.
        """
        if self._redis is None:
            return
        import json

        from app.governance.hitl_queues import queue_ids

        plan = getattr(getattr(tenant_ctx, "plan", None), "value", None) or "free"
        # TRG-23: the request's derived queues (agent:<id>, risk:<tier>). The
        # single-id field used to be "" always, so queue-filtered triggers never fired.
        queues = queue_ids(
            await self._goal_agent_id(req.goal_id, tenant_ctx.tenant_id), req.risk_level
        )
        payload = {
            "tenant_id": tenant_ctx.tenant_id,
            "tenant_plan": plan,
            "request_id": str(req.request_id),
            "goal_id": req.goal_id,
            "action": req.action,
            "risk_level": req.risk_level,
            "approver": req.approver or "",
            "note": req.note,
            "hitl_queue_ids": queues,
            "hitl_queue_id": queues[0] if queues else "",
        }
        try:
            from app.triggers.bus import publish_trigger_event

            # Stream XADD (+ legacy pub/sub while dual publish is on), TRG-18.
            await publish_trigger_event(self._redis, channel, json.dumps(payload))
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning(
                "hitl_trigger_event_publish_failed", channel=channel, error=str(exc)
            )

    async def publish_resolution(
        self,
        request_id: str,
        action: str,
        approver: str,
        note: str = "",
    ) -> None:
        """Publish a HITL resolution to the Redis list consumed by _wait_for_result.

        Sets a 24-hour TTL so the key is garbage-collected automatically.
        Safe to call even when Redis is unavailable — errors are logged only.
        """
        payload = json.dumps({"action": action, "approver": approver, "note": note})
        if self._redis is not None:
            try:
                result_key = f"hitl_result:{request_id}"
                await self._redis.rpush(result_key, payload)
                await self._redis.expire(result_key, 86400)  # 24 h
            except Exception as exc:
                from app.observability.logging import get_logger

                get_logger(__name__).warning("hitl_publish_resolution_error", error=str(exc))

    def expire_timed_out_requests(self) -> list[str]:
        """Check all pending requests and auto-reject those past _expires_at_dt."""
        from datetime import UTC

        expired = []
        now = datetime.now(UTC)
        for (_tenant_id, req_id), req in list(self._requests.items()):
            if req.status != ApprovalStatus.PENDING:
                continue
            expires_at = getattr(req, "_expires_at_dt", None)
            if expires_at and now > expires_at:
                req.status = ApprovalStatus.TIMED_OUT
                req._event.set()
                expired.append(req_id)
                # G-12: fire-and-forget timeout notification
                if self._notification_service is not None:
                    try:
                        import asyncio as _aio

                        _coro = self._notification_service.notify_approval_timeout(
                            request_id=req_id,
                            goal_id=req.goal_id,
                            action=req.action,
                            tenant_id=_tenant_id,
                            auto_rejected=True,
                        )
                        # no running loop — skip notification
                        with contextlib.suppress(RuntimeError):
                            _aio.ensure_future(  # noqa: RUF006  # fire-and-forget by design
                                _coro
                            )
                    except Exception:
                        pass
        return expired

    async def load_pending_from_db(self, db: Any, tenant_id: str) -> int:
        """Cache ONE tenant's pending approvals from the DB. Returns the count.

        Runs inside the tenant's RLS context: ``approval_requests`` is FORCE-RLS,
        so without ``app.tenant_id`` set the API's NOBYPASSRLS role reads zero
        rows and this silently restored nothing.
        """
        if db is None:
            return 0
        try:
            from sqlalchemy import select

            from app.db.models.governance import ApprovalRequest as DBApprovalReq
            from app.db.rls import sqlalchemy_rls_context

            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                result = await session.execute(
                    select(DBApprovalReq).where(
                        DBApprovalReq.tenant_id == tenant_id, DBApprovalReq.status == "pending"
                    )
                )
                rows = result.scalars().all()
            for row in rows:
                req = ApprovalRequest(
                    goal_id=row.goal_id,
                    action=row.action or "unknown",
                    risk_level=row.risk_level or "unknown",
                    request_id=row.id,
                    status=ApprovalStatus.PENDING,
                )
                self._requests[(tenant_id, row.id)] = req
            return len(rows)
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("hitl_load_from_db_failed", error=str(exc))
            return 0

    async def expire_phantom_approvals(self, system_db: Any) -> int:
        """Expire pending approvals whose goal/mission already finished (all tenants).

        This is genuinely cross-tenant maintenance, so ``system_db`` MUST be the
        maintenance session factory (``app.db.session.get_system_session_factory``
        / ``app.state.system_db_session_factory``, a BYPASSRLS role) — never the
        request-serving factory. Under the API's NOBYPASSRLS role
        ``system_session`` fails loudly ("query would be affected by row-level
        security"); that is logged and 0 is returned.

        It only writes; nothing is loaded into this process's cache. The previous
        startup scan also pulled every tenant's pending approvals into memory,
        which was both a privilege problem and unnecessary: the DB-backed reads
        (``aget_request`` / ``alist_pending`` / ``approve_async`` / ``reject``)
        resolve approvals per tenant on demand.

        Bounded to ``_PHANTOM_SWEEP_LIMIT`` rows per call. Returns the number of
        approvals expired.
        """
        if system_db is None:
            return 0
        try:
            from sqlalchemy import text

            from app.db.rls import system_session

            async with (
                system_db() as session,
                session.begin(),
                system_session(session),
            ):
                result = await session.execute(
                    text(_EXPIRE_PHANTOMS_SQL), {"lim": _PHANTOM_SWEEP_LIMIT}
                )
                expired = [(str(r[1]), str(r[0])) for r in result.all()]
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("hitl_expire_phantoms_failed", error=str(exc))
            return 0
        # Keep any copy this process already cached consistent with the DB.
        for key in expired:
            cached = self._requests.get(key)
            if cached is not None and cached.status == ApprovalStatus.PENDING:
                cached.status = ApprovalStatus.TIMED_OUT
                cached._event.set()
        return len(expired)

    async def startup_restore(self, db: Any) -> int:
        """Startup HITL maintenance: expire phantom approvals.

        ``db`` must be the maintenance (system) session factory — see
        :meth:`expire_phantom_approvals`. Pending approvals are NOT hydrated into
        memory any more: Postgres is the source of truth and every request path
        reads it per tenant, so a goal left ``waiting_human`` across a restart is
        still approvable (``approve_async`` / ``reject`` resolve it in the DB).

        Returns: number of phantom approvals expired.
        """
        if db is None:
            return 0
        try:
            count = await self.expire_phantom_approvals(db)
            from app.observability.logging import get_logger

            get_logger(__name__).info("hitl_phantom_approvals_expired", count=count)
            return count
        except Exception as exc:
            from app.observability.logging import get_logger

            get_logger(__name__).warning("hitl_startup_restore_failed", error=str(exc))
            return 0


def wire_hitl_runtime(
    gateway: HITLGateway,
    *,
    redis: Any,
    goal_service: Any = None,
    redis_url: str = "",
) -> None:
    """Bind the shared Redis into *gateway* and start the rejection-note subscriber.

    ``HITLGateway._redis`` was never set in production: ``hitl.approved`` /
    ``hitl.rejected`` trigger events, cross-replica BLPOP delivery to a waiter
    on another replica/worker, and ``hitl_rejected:*`` notes were all no-ops,
    and GoalService's rejection subscriber was never started.
    """
    gateway._redis = redis
    if goal_service is not None and redis_url:
        starter = getattr(goal_service, "start_hitl_rejection_subscriber", None)
        if callable(starter):
            starter(redis_url)
