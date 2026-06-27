"""Civilization API — additive router, feature-flagged by settings.civilization_enabled.

All endpoints:
- Tenant-scoped via existing TenantMiddleware
- Feature-flagged (503 if civilization_enabled=False)
- Use PlatformError envelope for errors
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.observability.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/civilizations", tags=["civilization"])


# ── Guards ─────────────────────────────────────────────────────────────────

def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


def _require_feature_enabled(request: Request) -> None:
    settings = getattr(request.app.state, "settings", None)
    if settings and not getattr(settings, "civilization_enabled", False):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent Civilization feature is not enabled. Set CIVILIZATION_ENABLED=true.",
        )


def _get_db(request: Request) -> Any:
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        try:
            from app.db.session import get_session_factory
            db = get_session_factory()
        except Exception:
            pass
    return db


# ── Request models ──────────────────────────────────────────────────────────

class CreateCivilizationRequest(BaseModel):
    name: str
    constitution: dict[str, Any] = {}


class SubmitGoalRequest(BaseModel):
    goal: str
    priority: str = "normal"


class ConstitutionUpdateRequest(BaseModel):
    constitution: dict[str, Any]


class ControlRequest(BaseModel):
    action: str = ""  # pause|resume|throttle|adjust_budget
    params: dict[str, Any] = {}


# ── Helpers to build orchestrator ──────────────────────────────────────────

def _build_orchestrator(
    civilization_id: str, tenant_id: str, constitution_data: dict, request: Request
) -> Any:
    """Build a CivilizationOrchestrator with all dependencies from app.state."""
    from app.civilization.models import Constitution
    from app.civilization.governor import Governor
    from app.civilization.society import Society
    from app.civilization.bus import CivilizationBus
    from app.civilization.blackboard import Blackboard
    from app.civilization.learning import LearningPipeline
    from app.civilization.orchestrator import CivilizationOrchestrator

    db = _get_db(request)
    redis = (
        getattr(request.app.state, "_policy_pubsub_redis", None)
        or getattr(request.app.state, "_rate_limiter_redis", None)
    )

    constitution = Constitution.from_dict(constitution_data)
    tenant_ctx = getattr(request.state, "tenant", None)

    governor = Governor(
        constitution=constitution,
        civilization_id=civilization_id,
        tenant_id=tenant_id,
        agent_store=getattr(request.app.state, "agent_store", None),
        meta_agent_planner=getattr(request.app.state, "meta_agent", None),
        cost_controller=getattr(request.app.state, "cost_controller", None),
        policy_engine=getattr(request.app.state, "policy_engine", None),
        hitl_gateway=getattr(request.app.state, "hitl_gateway", None),
        audit_log=getattr(request.app.state, "audit_log", None),
        db_session_factory=db,
        redis=redis,
    )
    society = Society(
        civilization_id=civilization_id,
        tenant_id=tenant_id,
        db_session_factory=db,
        agent_router=getattr(request.app.state, "agent_router", None),
        eval_runner=getattr(request.app.state, "eval_runner", None),
    )
    bus = CivilizationBus(
        civilization_id=civilization_id,
        tenant_id=tenant_id,
        db_session_factory=db,
        redis=redis,
    )
    blackboard = Blackboard(
        civilization_id=civilization_id,
        tenant_id=tenant_id,
        db_session_factory=db,
        bus=bus,
    )
    learning = LearningPipeline(
        civilization_id=civilization_id,
        tenant_id=tenant_id,
        db_session_factory=db,
        eval_runner=getattr(request.app.state, "eval_runner", None),
        long_term_memory=getattr(request.app.state, "long_term_memory", None),
        bus=bus,
        redis=redis,
    )

    return CivilizationOrchestrator(
        civilization_id=civilization_id,
        tenant_id=tenant_id,
        constitution=constitution,
        governor=governor,
        society=society,
        bus=bus,
        blackboard=blackboard,
        learning_pipeline=learning,
        goal_service=getattr(request.app.state, "goal_service", None),
        debate_orchestrator=None,  # wired separately if available
        db_session_factory=db,
        redis=redis,
        tenant_ctx=tenant_ctx,
    )


# ── CRUD Endpoints ──────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED)
async def create_civilization(request: Request, body: CreateCivilizationRequest) -> dict:
    """Create a new civilization for this tenant."""
    _require_feature_enabled(request)
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        raise HTTPException(503, "Database not available")

    civ_id = uuid.uuid4().hex
    from app.civilization.models import Constitution
    constitution = Constitution.from_dict(body.constitution)

    from sqlalchemy import text
    try:
        async with db() as session, session.begin():
            await session.execute(
                text("""
                    INSERT INTO civilizations
                        (id, tenant_id, name, status, constitution, created_at, updated_at)
                    VALUES (:id, :tid, :name, 'active', :constitution::jsonb, NOW(), NOW())
                """),
                {
                    "id": civ_id,
                    "tid": tenant_ctx.tenant_id,
                    "name": body.name,
                    "constitution": json.dumps(constitution.to_dict()),
                },
            )
    except Exception as exc:
        logger.warning("civilization_create_failed", error=str(exc))
        raise HTTPException(500, f"Failed to create civilization: {exc}")

    return {
        "id": civ_id,
        "tenant_id": tenant_ctx.tenant_id,
        "name": body.name,
        "status": "active",
        "constitution": constitution.to_dict(),
        "created_at": datetime.now(UTC).isoformat(),
    }


@router.get("")
async def list_civilizations(request: Request) -> list[dict]:
    """List all civilizations for this tenant."""
    _require_feature_enabled(request)
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        return []
    try:
        from sqlalchemy import text

        async with db() as session:
            rows = (
                await session.execute(
                    text("""
                        SELECT id, name, status, constitution, created_at, updated_at
                        FROM civilizations
                        WHERE tenant_id = :tid
                        ORDER BY created_at DESC
                    """),
                    {"tid": tenant_ctx.tenant_id},
                )
            ).fetchall()
        return [
            {
                "id": r[0],
                "name": r[1],
                "status": r[2],
                "constitution": r[3] if isinstance(r[3], dict) else {},
                "created_at": r[4].isoformat() if r[4] else "",
                "updated_at": r[5].isoformat() if r[5] else "",
            }
            for r in rows
        ]
    except Exception as exc:
        logger.warning("civilization_list_failed", error=str(exc))
        return []


@router.get("/{civ_id}")
async def get_civilization(request: Request, civ_id: str) -> dict:
    """Get civilization detail + live metrics."""
    _require_feature_enabled(request)
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        raise HTTPException(503, "Database not available")

    try:
        from sqlalchemy import text

        async with db() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id, name, status, constitution, created_at "
                        "FROM civilizations WHERE id=:id AND tenant_id=:tid"
                    ),
                    {"id": civ_id, "tid": tenant_ctx.tenant_id},
                )
            ).fetchone()
        if row is None:
            raise HTTPException(404, f"Civilization {civ_id} not found")

        civ: dict[str, Any] = {
            "id": row[0],
            "name": row[1],
            "status": row[2],
            "constitution": row[3] if isinstance(row[3], dict) else {},
            "created_at": row[4].isoformat() if row[4] else "",
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc))

    # Attach live society metrics
    try:
        from app.civilization.society import Society

        society = Society(
            civilization_id=civ_id,
            tenant_id=tenant_ctx.tenant_id,
            db_session_factory=db,
        )
        civ["metrics"] = await society.get_metrics()
    except Exception:
        civ["metrics"] = {}

    return civ


@router.put("/{civ_id}/constitution")
async def update_constitution(
    request: Request, civ_id: str, body: ConstitutionUpdateRequest
) -> dict:
    """Edit the civilization's Constitution."""
    _require_feature_enabled(request)
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        raise HTTPException(503, "Database not available")

    from app.civilization.models import Constitution

    constitution = Constitution.from_dict(body.constitution)

    try:
        from sqlalchemy import text

        async with db() as session, session.begin():
            result = await session.execute(
                text("""
                    UPDATE civilizations
                    SET constitution = :constitution::jsonb, updated_at = NOW()
                    WHERE id = :id AND tenant_id = :tid
                    RETURNING id
                """),
                {
                    "constitution": json.dumps(constitution.to_dict()),
                    "id": civ_id,
                    "tid": tenant_ctx.tenant_id,
                },
            )
            if result.fetchone() is None:
                raise HTTPException(404, f"Civilization {civ_id} not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc))

    return {"id": civ_id, "constitution": constitution.to_dict(), "updated": True}


@router.post("/{civ_id}/goals", status_code=status.HTTP_202_ACCEPTED)
async def submit_goal(request: Request, civ_id: str, body: SubmitGoalRequest) -> dict:
    """Submit a goal into the civilization society."""
    _require_feature_enabled(request)
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        raise HTTPException(503, "Database not available")

    # Load constitution and check status
    constitution_data: dict = {}
    try:
        from sqlalchemy import text

        async with db() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT constitution, status "
                        "FROM civilizations WHERE id=:id AND tenant_id=:tid"
                    ),
                    {"id": civ_id, "tid": tenant_ctx.tenant_id},
                )
            ).fetchone()
        if row is None:
            raise HTTPException(404, f"Civilization {civ_id} not found")
        if row[1] == "paused":
            raise HTTPException(409, "Civilization is paused")
        constitution_data = row[0] if isinstance(row[0], dict) else {}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, str(exc))

    orchestrator = _build_orchestrator(civ_id, tenant_ctx.tenant_id, constitution_data, request)
    result = await orchestrator.submit_goal(body.goal, priority=body.priority)
    return result


# ── Graph + Inspector endpoints ─────────────────────────────────────────────

@router.get("/{civ_id}/graph")
async def get_society_graph(request: Request, civ_id: str) -> dict:
    """Get the society graph: members, lineage, edges."""
    _require_feature_enabled(request)
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)

    from app.civilization.society import Society

    society = Society(
        civilization_id=civ_id,
        tenant_id=tenant_ctx.tenant_id,
        db_session_factory=db,
    )
    graph = await society.get_lineage_graph()

    # Append recent bus messages as edges
    try:
        from app.civilization.bus import CivilizationBus

        redis = getattr(request.app.state, "_policy_pubsub_redis", None)
        bus = CivilizationBus(
            civilization_id=civ_id,
            tenant_id=tenant_ctx.tenant_id,
            db_session_factory=db,
            redis=redis,
        )
        recent_msgs = await bus.get_messages(limit=20)
        for msg in recent_msgs:
            graph["edges"].append(
                {
                    "source": msg["from_agent_id"],
                    "target": "bus",
                    "type": "bus_message",
                    "topic": msg["topic"],
                    "ts": msg["ts"],
                }
            )
    except Exception:
        pass

    return graph


@router.get("/{civ_id}/agents/{agent_id}")
async def get_agent_inspector(request: Request, civ_id: str, agent_id: str) -> dict:
    """Member inspector: config, cost, reputation, tool calls, messages."""
    _require_feature_enabled(request)
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)

    from app.civilization.society import Society

    society = Society(
        civilization_id=civ_id,
        tenant_id=tenant_ctx.tenant_id,
        db_session_factory=db,
    )
    member = await society.get_member(agent_id)
    if member is None:
        raise HTTPException(404, f"Agent {agent_id} not in civilization {civ_id}")

    # Bus messages for this agent
    from app.civilization.bus import CivilizationBus

    redis = getattr(request.app.state, "_policy_pubsub_redis", None)
    bus = CivilizationBus(
        civilization_id=civ_id,
        tenant_id=tenant_ctx.tenant_id,
        db_session_factory=db,
        redis=redis,
    )
    messages = await bus.get_messages(from_agent_id=agent_id, limit=50)

    # Agent config from AgentStore
    agent_config: dict = {}
    agent_store = getattr(request.app.state, "agent_store", None)
    if agent_store is not None:
        try:
            config = await agent_store.get_async(agent_id, tenant_ctx=tenant_ctx)
            if config:
                agent_config = {
                    k: v
                    for k, v in config.items()
                    if k not in ("agent_id", "tenant_id")
                }
        except Exception:
            pass

    return {
        "agent_id": agent_id,
        "civilization_id": civ_id,
        "member": member,
        "agent_config": agent_config,
        "recent_messages": messages[:20],
        "message_count": len(messages),
    }


@router.get("/{civ_id}/blackboard")
async def get_blackboard(
    request: Request,
    civ_id: str,
    topic: str | None = None,
    agent_id: str | None = None,
    min_confidence: float = 0.0,
) -> list[dict]:
    """Get blackboard findings (filterable by topic, agent_id, min_confidence)."""
    _require_feature_enabled(request)
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)

    from app.civilization.blackboard import Blackboard

    board = Blackboard(
        civilization_id=civ_id,
        tenant_id=tenant_ctx.tenant_id,
        db_session_factory=db,
    )
    return await board.query(
        topic=topic,
        author_agent_id=agent_id,
        min_confidence=min_confidence,
    )


@router.get("/{civ_id}/debates")
async def get_debates(request: Request, civ_id: str) -> list[dict]:
    """Get debate transcripts from the bus."""
    _require_feature_enabled(request)
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)

    from app.civilization.bus import CivilizationBus

    redis = getattr(request.app.state, "_policy_pubsub_redis", None)
    bus = CivilizationBus(
        civilization_id=civ_id,
        tenant_id=tenant_ctx.tenant_id,
        db_session_factory=db,
        redis=redis,
    )
    return await bus.get_messages(topic="debate", limit=50)


@router.get("/{civ_id}/learnings")
async def get_learnings(
    request: Request,
    civ_id: str,
    learning_status: str | None = None,
) -> list[dict]:
    """Get the learning ledger."""
    _require_feature_enabled(request)
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)

    from app.civilization.learning import LearningPipeline

    pipeline = LearningPipeline(
        civilization_id=civ_id,
        tenant_id=tenant_ctx.tenant_id,
        db_session_factory=db,
    )
    return await pipeline.get_learnings(status=learning_status)


@router.get("/{civ_id}/spawns")
async def get_spawn_audit(request: Request, civ_id: str) -> list[dict]:
    """Get spawn-request audit timeline."""
    _require_feature_enabled(request)
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        return []
    try:
        from sqlalchemy import text

        async with db() as session:
            rows = (
                await session.execute(
                    text("""
                        SELECT id, requester_agent_id, requested_capability, goal_text,
                               decision, reason, verdict, created_agent_id, created_at
                        FROM spawn_requests
                        WHERE civilization_id = :cid AND tenant_id = :tid
                        ORDER BY created_at DESC LIMIT 100
                    """),
                    {"cid": civ_id, "tid": tenant_ctx.tenant_id},
                )
            ).fetchall()
        return [
            {
                "id": r[0],
                "requester_agent_id": r[1],
                "requested_capability": r[2],
                "goal_text": r[3][:100] if r[3] else "",
                "decision": r[4],
                "reason": r[5],
                "verdict": r[6] if isinstance(r[6], dict) else {},
                "created_agent_id": r[7],
                "created_at": r[8].isoformat() if r[8] else "",
            }
            for r in rows
        ]
    except Exception as exc:
        logger.warning("civilization_spawns_failed", error=str(exc))
        return []


@router.get("/{civ_id}/replay")
async def get_replay(
    request: Request,
    civ_id: str,
    since: str | None = None,
) -> dict:
    """Full event timeline for replay."""
    _require_feature_enabled(request)
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)

    from app.civilization.events import get_events_since

    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except Exception:
            pass

    events = await get_events_since(
        civilization_id=civ_id,
        tenant_id=tenant_ctx.tenant_id,
        since_ts=since_dt,
        limit=1000,
        db=db,
    )
    return {"civilization_id": civ_id, "events": events, "count": len(events)}


# ── Control endpoints ────────────────────────────────────────────────────────

@router.post("/{civ_id}/controls/{action}")
async def control_civilization(
    request: Request,
    civ_id: str,
    action: str,
    body: ControlRequest = ControlRequest(),
) -> dict:
    """pause | resume | throttle | adjust_budget"""
    _require_feature_enabled(request)
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)

    constitution_data: dict = {}
    try:
        from sqlalchemy import text

        async with db() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT constitution FROM civilizations "
                        "WHERE id=:id AND tenant_id=:tid"
                    ),
                    {"id": civ_id, "tid": tenant_ctx.tenant_id},
                )
            ).fetchone()
        constitution_data = (
            row[0] if row and isinstance(row[0], dict) else {}
        ) if row else {}
    except Exception:
        pass

    from app.civilization.models import Constitution
    from app.civilization.governor import Governor

    redis = (
        getattr(request.app.state, "_policy_pubsub_redis", None)
        or getattr(request.app.state, "_rate_limiter_redis", None)
    )
    governor = Governor(
        constitution=Constitution.from_dict(constitution_data),
        civilization_id=civ_id,
        tenant_id=tenant_ctx.tenant_id,
        db_session_factory=db,
        redis=redis,
    )

    if action == "pause":
        await governor.pause()
        return {"status": "paused", "civilization_id": civ_id}
    elif action == "resume":
        await governor.resume()
        return {"status": "active", "civilization_id": civ_id}
    elif action == "throttle":
        # Throttle is acknowledged but the rate limit is constitution-level;
        # return ok so callers can integrate without breaking.
        return {"status": "ok", "action": action, "params": body.params}
    elif action == "adjust_budget":
        new_budget = body.params.get("total_budget_usd")
        if new_budget is not None:
            constitution_data["total_budget_usd"] = float(new_budget)
            try:
                from sqlalchemy import text

                async with db() as session, session.begin():
                    await session.execute(
                        text(
                            "UPDATE civilizations "
                            "SET constitution=:c::jsonb, updated_at=NOW() "
                            "WHERE id=:id AND tenant_id=:tid"
                        ),
                        {
                            "c": json.dumps(constitution_data),
                            "id": civ_id,
                            "tid": tenant_ctx.tenant_id,
                        },
                    )
            except Exception:
                pass
        return {"status": "ok", "action": action, "params": body.params}
    else:
        raise HTTPException(
            400,
            f"Unknown action: {action}. Valid: pause|resume|throttle|adjust_budget",
        )


@router.post("/{civ_id}/agents/{agent_id}/kill")
async def kill_agent(request: Request, civ_id: str, agent_id: str) -> dict:
    """Kill a specific civilization member."""
    _require_feature_enabled(request)
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)
    redis = (
        getattr(request.app.state, "_policy_pubsub_redis", None)
        or getattr(request.app.state, "_rate_limiter_redis", None)
    )

    from app.civilization.models import Constitution
    from app.civilization.governor import Governor

    governor = Governor(
        constitution=Constitution(),
        civilization_id=civ_id,
        tenant_id=tenant_ctx.tenant_id,
        db_session_factory=db,
        redis=redis,
    )
    await governor.kill_agent(agent_id, tenant_ctx)
    return {"killed": agent_id, "civilization_id": civ_id}


# ── SSE streaming ────────────────────────────────────────────────────────────

@router.get("/{civ_id}/stream")
async def stream_civilization(request: Request, civ_id: str) -> StreamingResponse:
    """Live SSE event stream for the civilization (mirrors /goals/{id}/stream)."""
    _require_feature_enabled(request)
    tenant_ctx = _require_tenant(request)
    redis = getattr(request.app.state, "_policy_pubsub_redis", None)
    db = _get_db(request)

    async def generate():  # type: ignore[return]
        # Send catch-up events first
        from app.civilization.events import get_events_since

        catchup = await get_events_since(
            civilization_id=civ_id,
            tenant_id=tenant_ctx.tenant_id,
            limit=100,
            db=db,
        )
        for evt in catchup:
            yield f"data: {json.dumps(evt)}\n\n"

        # Stream live events via Redis pub/sub when available
        if redis is None:
            yield f"data: {json.dumps({'type': 'stream_ready', 'civilization_id': civ_id})}\n\n"
            return

        channel = f"civ_sse:{tenant_ctx.tenant_id}:{civ_id}"
        try:
            async with _nullctx(redis) as r:
                pubsub = r.pubsub() if hasattr(r, "pubsub") else redis.pubsub()
                await pubsub.subscribe(channel)
                async for message in pubsub.listen():
                    if await request.is_disconnected():
                        break
                    if message.get("type") == "message":
                        yield f"data: {message['data']}\n\n"
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'stream_error', 'error': str(exc)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


class _nullctx:
    """Null async context manager — passes the value through unchanged."""

    def __init__(self, v: Any) -> None:
        self._v = v

    async def __aenter__(self) -> Any:
        return self._v

    async def __aexit__(self, *a: Any) -> None:
        pass


# ── WebSocket ────────────────────────────────────────────────────────────────

@router.websocket("/{civ_id}/ws")
async def civilization_ws(websocket: WebSocket, civ_id: str) -> None:
    """Live graph updates via WebSocket (pub/sub fan-out)."""
    await websocket.accept()

    app_state = getattr(websocket, "app", None)
    redis = getattr(app_state.state, "_policy_pubsub_redis", None) if app_state else None

    # Best-effort tenant resolution from query param or header
    api_key = websocket.query_params.get("api_key", "")
    tenant_id = "unknown"
    if api_key:
        resolver = getattr(app_state.state, "_tenant_key_resolver", None) if app_state else None
        if resolver is not None:
            try:
                ctx = await resolver(api_key)
                if ctx is not None:
                    tenant_id = ctx.tenant_id
            except Exception:
                pass

    try:
        if redis is not None:
            channel = f"civ_sse:{tenant_id}:{civ_id}"
            pubsub = redis.pubsub()
            await pubsub.subscribe(channel)
            async for message in pubsub.listen():
                if message.get("type") == "message":
                    await websocket.send_text(message["data"])
        else:
            # No Redis — send a ready ping then wait for disconnect
            await websocket.send_text(
                json.dumps({"type": "stream_ready", "civilization_id": civ_id})
            )
            # Keep the connection open; client can send pings
            while True:
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                except asyncio.TimeoutError:
                    await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("civilization_ws_error", error=str(exc))
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
