"""Paginated transcript and cursor-based coordination event replay."""

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.coordination.streaming import encode_heartbeat, encode_sse_event

router = APIRouter(prefix="/api/v1/coordination/sessions", tags=["coordination-transcript"])


@router.get("/{session_id}/messages")
async def list_messages(
    request: Request,
    session_id: str,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")
    service = getattr(request.app.state, "transcript_service", None)
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Transcript unavailable")
    messages = await service.page(
        str(tenant.tenant_id),
        session_id,
        after_sequence=after_sequence,
        limit=limit,
    )
    next_cursor = messages[-1].sequence if messages else after_sequence
    return {
        "items": [item.model_dump(mode="json") for item in messages],
        "next_sequence": next_cursor,
        "has_more": len(messages) == limit,
    }


@router.get(
    "/{session_id}/events",
    name="replay_coordination_events",
    operation_id="replay_coordination_events",
    responses={
        200: {
            "description": "Ordered coordination events followed by heartbeat frames.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
            "headers": {
                "Cache-Control": {"schema": {"type": "string"}},
                "X-Accel-Buffering": {"schema": {"type": "string"}},
            },
        }
    },
)
async def replay_events(
    request: Request,
    session_id: str,
    after_sequence: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")
    replay = getattr(request.app.state, "coordination_replay", None)
    if replay is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Replay unavailable")
    cursor = after_sequence
    if last_event_id is not None:
        try:
            cursor = max(cursor, int(last_event_id))
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Last-Event-ID must be a sequence number",
            ) from exc

    async def stream() -> AsyncIterator[str]:
        events = await replay.page(
            tenant_id=str(tenant.tenant_id),
            session_id=session_id,
            after_sequence=cursor,
            limit=500,
        )
        for event in events:
            yield encode_sse_event(event)
        yield encode_heartbeat()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router"]
