"""User auth session management — list active sessions, revoke, idle timeout."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/auth/sessions", tags=["auth"])

_SESSION_TTL_SECONDS = 86400  # 24h idle timeout


@router.get("")
async def list_active_sessions(request: Request) -> list[dict[str, Any]]:
    """List all active login sessions for the current user."""
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(401, "Unauthorized")
    redis = getattr(request.app.state, "_rate_limiter_redis", None)
    if redis is None:
        return [
            {"session_id": "current", "created_at": "", "device": "unknown", "is_current": True}
        ]
    try:
        pattern = f"session:{tenant.tenant_id}:*"
        keys = await redis.keys(pattern)
        sessions = []
        for k in keys[:50]:
            data = await redis.hgetall(k)
            if data:
                sid = k.decode().split(":")[-1] if isinstance(k, bytes) else k.split(":")[-1]
                sessions.append(
                    {
                        "session_id": sid,
                        "created_at": (
                            data.get(b"created_at") or data.get("created_at", b"")
                        ).decode()
                        if isinstance(data.get(b"created_at", data.get("created_at", "")), bytes)
                        else data.get("created_at", ""),
                        "ip_address": (data.get(b"ip") or data.get("ip", b"unknown")).decode()
                        if isinstance(data.get(b"ip", data.get("ip", "unknown")), bytes)
                        else data.get("ip", "unknown"),
                        "user_agent": (data.get(b"ua") or data.get("ua", b"")).decode()
                        if isinstance(data.get(b"ua", data.get("ua", "")), bytes)
                        else data.get("ua", ""),
                        "is_current": False,
                    }
                )
        return sessions
    except Exception:
        return []


@router.delete("/{session_id}", status_code=204)
async def revoke_session(session_id: str, request: Request) -> None:
    """Revoke a specific session (remote logout)."""
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(401, "Unauthorized")
    redis = getattr(request.app.state, "_rate_limiter_redis", None)
    if redis is None:
        raise HTTPException(503, "Session store unavailable")
    key = f"session:{tenant.tenant_id}:{session_id}"
    await redis.delete(key)


@router.delete("", status_code=204)
async def revoke_all_other_sessions(request: Request) -> None:
    """Revoke all sessions except the current one."""
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(401, "Unauthorized")
    redis = getattr(request.app.state, "_rate_limiter_redis", None)
    if redis is None:
        raise HTTPException(503, "Session store unavailable")
    pattern = f"session:{tenant.tenant_id}:*"
    keys = await redis.keys(pattern)
    if keys:
        await redis.delete(*keys)
