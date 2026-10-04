"""A2A (Agent-to-Agent) protocol — full implementation with DB persistence, HMAC auth, callbacks."""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.net.ssrf_guard import (
    SSRFError,
    assert_public_url_async,
    public_async_client,
)
from app.observability.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["a2a"])

# In-memory fallback (used when DB not available)
_tasks: dict[str, dict[str, Any]] = {}

_A2A_GOAL_TIMEOUT_S = 300.0
_TERMINAL_EVENT_STATUS = {
    "goal_complete": "complete",
    "goal_failed": "failed",
    "worker_failed": "failed",
    "goal_cancelled": "canceled",
}
_GOAL_STATUS_TO_A2A = {"complete": "complete", "failed": "failed", "cancelled": "canceled"}


async def _goal_result_text(goal_service: Any, goal_id: str, tenant_ctx: Any) -> str:
    """The goal's actual output (its result artifact summary), not a status line."""
    try:
        goal = await goal_service.get_goal(goal_id, tenant_ctx)
    except Exception as exc:
        logger.warning("a2a_goal_result_unavailable", goal_id=goal_id, error=str(exc)[:200])
        return ""
    artifact = goal.get("result_artifact") if isinstance(goal, dict) else None
    if isinstance(artifact, dict):
        return str(artifact.get("summary") or "")
    return ""


async def _await_goal_outcome(
    goal_service: Any,
    goal_id: str,
    tenant_ctx: Any,
    *,
    timeout_s: float = _A2A_GOAL_TIMEOUT_S,
) -> tuple[str, str]:
    """Wait for ``goal_id`` to reach a terminal state; return ``(a2a_status, result)``.

    The status used to be pre-set to ``complete``, so a cancelled goal (whose
    ``goal_cancelled`` event the loop ignored), or a stream that simply ended,
    was reported to the calling agent as completed — with the literal text
    ``"Goal <id> completed"`` instead of the goal's output. Now only an explicit
    terminal event (or the goal's persisted status, when the stream ends early)
    decides the outcome, and a completed task carries the real result.
    """
    import asyncio

    status: str | None = None
    detail = ""
    try:
        async with asyncio.timeout(timeout_s):
            async for evt in goal_service.subscribe_events(goal_id=goal_id, tenant_ctx=tenant_ctx):
                mapped = _TERMINAL_EVENT_STATUS.get(str(evt.get("type", "")))
                if mapped is not None:
                    status = mapped
                    detail = str(evt.get("reason") or "")
                    break
    except TimeoutError:
        return "timeout", f"Goal {goal_id} did not finish within {int(timeout_s)}s"

    if status is None:
        # Stream ended without a terminal event: trust the persisted status only.
        try:
            goal = await goal_service.get_goal(goal_id, tenant_ctx)
            status = _GOAL_STATUS_TO_A2A.get(str(goal.get("status", "")).lower())
        except Exception:
            status = None
        if status is None:
            return "error", f"Goal {goal_id} event stream ended before a terminal state"

    if status == "complete":
        return "complete", await _goal_result_text(goal_service, goal_id, tenant_ctx)
    if status == "canceled":
        return "canceled", detail or f"Goal {goal_id} was cancelled"
    return "failed", detail or f"Goal {goal_id} failed"


# ── startup check: warn loudly when HMAC auth is disabled ─────────────────────
if not os.getenv("A2A_SHARED_SECRET", ""):
    logger.warning(
        "a2a_hmac_disabled",
        message=(
            "A2A_SHARED_SECRET is not set — incoming A2A tasks are accepted without "
            "authentication. Set this env var in production to enable HMAC-SHA256 "
            "request signing."
        ),
    )


def _get_a2a_secret() -> str:
    return os.getenv("A2A_SHARED_SECRET", "")


def _verify_hmac(payload: bytes, signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 signature of incoming A2A task.

    With no ``A2A_SHARED_SECRET`` the check is skipped — but only outside
    production; :func:`receive_a2a_task` refuses to accept tasks at all in
    production without a secret (fail closed), rather than silently running
    unsigned.
    """
    if not secret:
        return True  # dev only — production is gated in receive_a2a_task
    if not signature:
        return False
    expected = _hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return _hmac.compare_digest(f"sha256={expected}", signature)


A2A_SIGNATURE_MAX_SKEW_SECONDS = 300
_seen_signatures: dict[str, float] = {}  # no-Redis replay cache (single process)


async def _check_signed_request(request: Request, raw_body: bytes, secret: str) -> None:
    """Verify a timestamped, single-use A2A signature (raise 401/503).

    The HMAC used to cover the body only, with no timestamp or nonce: a captured
    request could be replayed forever. Now the signer sends ``X-A2A-Timestamp``
    (unix seconds) and signs ``f"{timestamp}.".encode() + body``; the timestamp
    must be within ±5 min and each signature is accepted once (Redis SET NX,
    in-process without Redis; a Redis error refuses the request).
    """
    import time

    signature = request.headers.get("X-A2A-Signature", "")
    ts_raw = request.headers.get("X-A2A-Timestamp", "")
    try:
        ts = int(ts_raw)
    except ValueError:
        raise HTTPException(401, "Missing or invalid X-A2A-Timestamp") from None
    now = time.time()
    if abs(now - ts) > A2A_SIGNATURE_MAX_SKEW_SECONDS:
        raise HTTPException(401, "A2A request timestamp outside the allowed window")
    if not _verify_hmac(f"{ts}.".encode() + raw_body, signature, secret):
        raise HTTPException(401, "Invalid A2A signature")

    ttl = 2 * A2A_SIGNATURE_MAX_SKEW_SECONDS
    redis = getattr(request.app.state, "_redis", None)
    if redis is not None:
        try:
            fresh = await redis.set(f"a2a_sig:{signature}", "1", nx=True, ex=ttl)
        except Exception as exc:
            raise HTTPException(503, "A2A replay protection unavailable; retry") from exc
        if not fresh:
            raise HTTPException(401, "A2A request replayed")
        return
    for sig, seen_at in list(_seen_signatures.items()):
        if now - seen_at > ttl:
            _seen_signatures.pop(sig, None)
    if signature in _seen_signatures:
        raise HTTPException(401, "A2A request replayed")
    _seen_signatures[signature] = now


def _is_production() -> bool:
    try:
        from app.core.config import get_settings

        return get_settings().environment == "production"
    except Exception:  # pragma: no cover - settings unavailable: assume the strict case
        return True


# Every a2a_tasks statement runs under the caller's RLS scope AND carries an
# explicit tenant predicate. The table is RLS-protected; without the GUC these
# statements match nothing under a least-privilege role, and the old code then
# fell back to a process-local dict — so tasks silently lived in one replica's
# memory and vanished on restart.


async def _persist_task(task_id: str, data: dict[str, Any], db: Any) -> None:
    """Write A2A task to DB (dev/no-DB: process-local dict)."""
    if db is None:
        _tasks[task_id] = data
        return
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    tenant_id = str(data["tenant_id"])
    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        await session.execute(
            text("""INSERT INTO a2a_tasks
                (id, tenant_id, goal_text, status, callback_url, requester_id, created_at)
                VALUES (:id, :tid, :goal, :status, :cb, :req, NOW())
                ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status"""),
            {
                "id": task_id,
                "tid": tenant_id,
                "goal": data.get("goal", ""),
                "status": data.get("status", "pending"),
                "cb": data.get("callback_url", ""),
                "req": data.get("requester_agent_id", ""),
            },
        )


async def _update_task_status(
    task_id: str, tenant_id: str, status: str, result: str, db: Any
) -> None:
    """Update A2A task status in DB (scoped to the owning tenant)."""
    if db is None:
        if task_id in _tasks and _tasks[task_id].get("tenant_id") == tenant_id:
            _tasks[task_id]["status"] = status
            _tasks[task_id]["result"] = result
        return
    try:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
            await session.execute(
                text(
                    "UPDATE a2a_tasks SET status=:status, result=:result, updated_at=NOW() "
                    "WHERE id=:id AND tenant_id=:tid"
                ),
                {
                    "id": task_id,
                    "tid": tenant_id,
                    "status": status,
                    "result": result[:10000] if result else "",
                },
            )
    except Exception as exc:
        logger.warning("a2a_task_update_failed", error=str(exc))


async def _get_task(task_id: str, db: Any, tenant_id: str) -> dict[str, Any] | None:
    """Fetch one A2A task owned by *tenant_id* (never another tenant's)."""
    if db is None:
        task = _tasks.get(task_id)
        return task if task is not None and task.get("tenant_id") == tenant_id else None
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        row = (
            await session.execute(
                text(
                    "SELECT id, goal_text, status, result, callback_url, created_at "
                    "FROM a2a_tasks WHERE id=:id AND tenant_id=:tid"
                ),
                {"id": task_id, "tid": tenant_id},
            )
        ).fetchone()
    if row is None:
        return None
    return {
        "task_id": row[0],
        "goal": row[1],
        "status": row[2],
        "result": row[3],
        "callback_url": row[4],
        "created_at": row[5].isoformat() if row[5] else "",
    }


def sign_a2a_payload(raw_body: bytes, secret: str, timestamp: int | None = None) -> dict[str, str]:
    """``X-A2A-Timestamp`` / ``X-A2A-Signature`` headers for *raw_body*.

    Same scheme the platform verifies on inbound tasks: HMAC-SHA256 over
    ``f"{timestamp}.".encode() + raw_body``, hex, prefixed ``sha256=``.
    """
    import time

    ts = int(time.time()) if timestamp is None else int(timestamp)
    digest = _hmac.new(secret.encode(), f"{ts}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return {"X-A2A-Timestamp": str(ts), "X-A2A-Signature": f"sha256={digest}"}


async def _send_callback(callback_url: str, task_id: str, status: str, result: str) -> bool:
    """POST the task outcome to *callback_url*, signed (A2A-04). True only on a 2xx.

    The body is signed with ``A2A_SHARED_SECRET`` so the receiver can verify it
    came from this platform (it used to be an unsigned POST anyone could forge).
    Production never sends an unsigned callback. Never raises.
    """
    if not callback_url:
        return False
    secret = _get_a2a_secret()
    if not secret and _is_production():
        logger.warning("a2a_callback_unsigned_refused", task_id=task_id)
        return False
    try:
        # Re-checked at send time, not just at submission: the goal can run for
        # minutes, and a hostname validated then can resolve somewhere internal
        # now. The pinned client re-checks at connect (DNS rebinding) and
        # redirects are not followed.
        await assert_public_url_async(callback_url, context="A2A callback (send)")
    except SSRFError as exc:
        logger.warning("a2a_callback_blocked", task_id=task_id, error=str(exc)[:200])
        return False
    raw = json.dumps(
        {
            "task_id": task_id,
            "status": status,
            "result": result,
            "completed_at": datetime.now(UTC).isoformat(),
        },
        separators=(",", ":"),
    ).encode()
    headers = {"Content-Type": "application/json"}
    if secret:
        headers.update(sign_a2a_payload(raw, secret))
    try:
        async with public_async_client(timeout=10.0) as client:
            resp = await client.post(callback_url, content=raw, headers=headers)
    except Exception as exc:
        logger.warning("a2a_callback_failed", task_id=task_id, error=str(exc)[:200])
        return False
    if not 200 <= int(resp.status_code) < 300:
        logger.warning("a2a_callback_rejected", task_id=task_id, status_code=resp.status_code)
        return False
    logger.info("a2a_callback_sent", task_id=task_id, url=callback_url)
    return True


class A2ATaskRequest(BaseModel):
    goal: str
    context: dict[str, Any] = {}
    callback_url: str | None = None
    requester_agent_id: str | None = None
    priority: str = "normal"


@router.get("/.well-known/agent.json")
async def agent_card(request: Request) -> dict[str, Any]:
    """Return this agent's capability card for A2A discovery."""
    return {
        "agent_id": "agentverse-platform",
        "name": "AgentVerse Platform",
        "version": "0.1.0",
        "description": "World-class Agentic OS with goal execution, connectors, and governance",
        "endpoint": str(request.base_url).rstrip("/") + "/a2a",
        # A2A-02: everything a peer needs to sign a request (and verify our
        # callbacks), not just the header name.
        "authentication": {
            "scheme": "hmac-sha256",
            "header": "X-A2A-Signature",
            "signature_header": "X-A2A-Signature",
            "timestamp_header": "X-A2A-Timestamp",
            "timestamp_format": "unix epoch seconds (integer)",
            "signed_string": "{timestamp}.{raw_body}",
            "signature_format": "sha256=<lowercase hex HMAC-SHA256>",
            "key": "shared secret (A2A_SHARED_SECRET), exchanged out of band",
            "max_clock_skew_seconds": A2A_SIGNATURE_MAX_SKEW_SECONDS,
            "replay_protection": "each signature is accepted once within the skew window",
            "callbacks": {
                "signed": True,
                "scheme": "same headers and signed string, over the callback body",
            },
            "note": "Production refuses inbound tasks when no shared secret is configured.",
        },
        "capabilities": [
            "goal_execution",
            "multi_agent",
            "rag_search",
            "connector_tools",
            "hitl_approval",
            "audit_log",
            "persistence",
            "streaming",
        ],
        "supported_task_types": ["goal", "query", "action"],
    }


@router.post("/a2a/tasks", status_code=202)
async def receive_a2a_task(
    request: Request,
    body: A2ATaskRequest,
) -> dict[str, Any]:
    """Receive a task from another agent via A2A protocol."""
    # The task runs as the AUTHENTICATED caller — never as a fixed tenant.
    # This handler used to ignore request.state.tenant and execute every inbound
    # goal as the A2A_TENANT_ID tenant on the PROFESSIONAL plan, so any tenant
    # holding a valid API key (including a free one) could run goals inside
    # another tenant's context — its knowledge, tools, connectors and budget —
    # at an escalated plan. It could not even see its own task afterwards.
    caller = getattr(request.state, "tenant", None)
    if caller is None:
        raise HTTPException(401, "Not authenticated")

    raw_body = await request.body()
    secret = _get_a2a_secret()
    if not secret and _is_production():
        # Fail closed: an unsigned A2A inbound is a dev convenience, never a
        # production posture.
        raise HTTPException(503, "A2A inbound is disabled: A2A_SHARED_SECRET is not configured")
    if secret:
        await _check_signed_request(request, raw_body, secret)

    # SSRF guard — validate callback URL before accepting the task
    if body.callback_url:
        try:
            await assert_public_url_async(body.callback_url, context="A2A callback")
        except SSRFError as exc:
            raise HTTPException(status_code=400, detail="Callback URL is not permitted") from exc

    goal_service = getattr(request.app.state, "goal_service", None)
    if goal_service is None:
        # A2A-05: the task used to be stored and answered 202 "accepted" with
        # nothing able to run it — it stayed "accepted" forever.
        raise HTTPException(503, "Goal execution is unavailable; the task was not accepted")

    task_id = uuid.uuid4().hex
    db = getattr(request.app.state, "db_session_factory", None)

    tenant_ctx = caller
    a2a_tenant_id = str(caller.tenant_id)

    task_data = {
        "task_id": task_id,
        "goal": body.goal,
        "status": "accepted",
        "callback_url": body.callback_url or "",
        "requester_agent_id": body.requester_agent_id or "",
        "tenant_id": a2a_tenant_id,
        "created_at": datetime.now(UTC).isoformat(),
    }

    await _persist_task(task_id, task_data, db)

    # Execute goal asynchronously
    if goal_service:  # always set (checked above)
        import asyncio

        async def execute_and_callback() -> None:
            try:
                result = await goal_service.submit_goal(
                    goal=body.goal,
                    priority=body.priority,
                    dry_run=False,
                    tenant_ctx=tenant_ctx,
                )
                final_status, final_result = await _await_goal_outcome(
                    goal_service, str(result["goal_id"]), tenant_ctx
                )
            except Exception as exc:
                final_status = "error"
                final_result = str(exc)

            await _update_task_status(task_id, a2a_tenant_id, final_status, final_result, db)
            await _send_callback(body.callback_url or "", task_id, final_status, final_result)

        asyncio.create_task(execute_and_callback())  # noqa: RUF006  # fire-and-forget by design: intentionally not awaited/cancelled

    return {
        "task_id": task_id,
        "status": "accepted",
        "message": f"Task accepted. Track at /a2a/tasks/{task_id}",
    }


@router.get("/a2a/tasks")
async def list_a2a_tasks(request: Request, limit: int = 50) -> list[dict[str, Any]]:
    """List recent A2A tasks for the authenticated tenant.

    Always tenant-scoped: the previous version dropped the WHERE clause entirely
    when no tenant was resolved, and fell back to a process-local dict on any DB
    error.
    """
    caller = getattr(request.state, "tenant", None)
    if caller is None:
        raise HTTPException(401, "Not authenticated")
    tid = str(caller.tenant_id)
    limit = max(1, min(int(limit), 200))
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        return [t for t in _tasks.values() if t.get("tenant_id") == tid][-limit:][::-1]

    from sqlalchemy import text as _t

    from app.db.rls import sqlalchemy_rls_context

    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tid):
        rows = (
            await session.execute(
                _t(
                    "SELECT id, goal_text, status, callback_url, requester_id, created_at, "
                    "result FROM a2a_tasks WHERE tenant_id = :tid "
                    "ORDER BY created_at DESC LIMIT :lim"
                ),
                {"tid": tid, "lim": limit},
            )
        ).fetchall()
    return [
        {
            "task_id": r[0],
            "goal": r[1],
            "status": r[2],
            "callback_url": r[3],
            "requester_agent_id": r[4],
            "created_at": r[5].isoformat() if r[5] else "",
            "result": r[6],
        }
        for r in rows
    ]


@router.get("/a2a/tasks/{task_id}")
async def get_a2a_task(request: Request, task_id: str) -> dict[str, Any]:
    """Get A2A task status and result (the caller's own tasks only)."""
    caller = getattr(request.state, "tenant", None)
    if caller is None:
        raise HTTPException(401, "Not authenticated")
    db = getattr(request.app.state, "db_session_factory", None)
    task = await _get_task(task_id, db, tenant_id=str(caller.tenant_id))
    if task is None:
        raise HTTPException(404, f"Task {task_id} not found")
    return task
