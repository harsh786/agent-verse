"""Chat API router — 33 endpoints for the chat feature.

All endpoints require a valid tenant (API key auth via TenantMiddleware).

CHAT-SEC-1: a chat session is private to the principal that created it (see
:mod:`app.chat.ownership`). Every session endpoint resolves the session within
the caller's own scope first and answers 404 for anyone else's; the list and the
searches return only the caller's own sessions and messages. Sessions with no
owner (channels, older sessions) are reachable only through the admin routes
under ``/chat/admin``, each durably audited before anything is returned.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field, field_validator
from starlette.responses import Response, StreamingResponse

from app.chat.execution import ChatCodeExecutor
from app.chat.intent import IntentRouter
from app.chat.memory_api import MemoryAPI
from app.chat.ownership import (
    UNOWNED_SCOPE,
    ChatFolderNotFoundError,
    ChatScope,
    principal_of,
    scope_of,
)
from app.chat.search import ChatSearchEngine
from app.chat.service import ChatFolderLimitError, ChatService
from app.chat.services_api import ServicesAPI
from app.chat.stream import (
    stream_clarify,
    stream_goal_progress,
)
from app.chat.templates import BUILT_IN_TEMPLATES, TemplateStore
from app.observability.logging import get_logger
from app.tenancy.context import TenantContext
from app.tenancy.rbac import require_role

logger = get_logger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_intent_router = IntentRouter()
_executor = ChatCodeExecutor()
_search_engine = ChatSearchEngine()
_memory_api = MemoryAPI()
_services_api = ServicesAPI()
_template_store = TemplateStore()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _tenant(request: Request) -> TenantContext:
    ctx: TenantContext | None = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


def _scope(request: Request) -> tuple[TenantContext, ChatScope]:
    """The caller and the chat scope it may act in; 403 without a principal."""
    tenant = _tenant(request)
    scope = scope_of(tenant)
    if scope is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chat needs an identified caller (a signed-in person or an API key).",
        )
    return tenant, scope


async def _owned(
    request: Request, session_id: str
) -> tuple[TenantContext, ChatService, ChatScope, Any]:
    """The caller's own session, or 404 (another principal's session does not exist)."""
    tenant = _tenant(request)
    scope = scope_of(tenant)
    if scope is None:
        raise HTTPException(status_code=404, detail="Session not found")
    svc = _svc(request)
    s = await svc.aget_session(session_id, tenant.tenant_id, scope=scope)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    return tenant, svc, scope, s


def _svc(request: Request) -> ChatService:
    svc: ChatService | None = getattr(request.app.state, "chat_service", None)
    if svc is None:
        # Fallback for tests without lifespan (app without manage_pools)
        if not hasattr(request.app.state, "_chat_service_fallback"):
            request.app.state._chat_service_fallback = ChatService()
        svc = request.app.state._chat_service_fallback
    return svc


# ── Request / Response models ─────────────────────────────────────────────────


class CreateSessionRequest(BaseModel):
    title: str = "New Chat"
    system_prompt: str | None = None
    agent_id: str | None = None
    folder_id: str | None = None
    preferred_model: str | None = None


class UpdateSessionRequest(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    system_prompt: str | None = None
    pinned: bool | None = None
    folder_id: str | None = None
    show_reasoning: bool | None = None
    proactive_suggestions: bool | None = None
    preferred_model: str | None = None
    # CHAT-SEC-3: days of inactivity after which the session (unless pinned) is
    # deleted; an explicit null clears it (never expires).
    ttl_days: int | None = Field(default=None, ge=1, le=3650)


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=32_000)
    model_override: str | None = None
    branch_from_message_id: str | None = None


class EditMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=32_000)


# A folder color is rendered into the UI's style: a hex color only.
_HEX_COLOR = r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$"


class CreateFolderRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    color: str = Field(default="#6366f1", pattern=_HEX_COLOR)


class UpdateFolderRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    color: str | None = Field(default=None, pattern=_HEX_COLOR)


# A saved session artifact (code/text snippet) is capped; generated documents are
# capped by ChatArtifactStore.
_MAX_SNIPPET_CHARS = 1_000_000


class CreateArtifactRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    language: str = Field(default="text", max_length=64)
    content: str = Field(default="", max_length=_MAX_SNIPPET_CHARS)
    message_id: str | None = Field(default=None, max_length=64)


class UpdateArtifactRequest(BaseModel):
    content: str = Field(..., max_length=_MAX_SNIPPET_CHARS)


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    session_id: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


def _session_to_dict(s: Any) -> dict[str, Any]:
    return {
        "id": s.id,
        "tenant_id": s.tenant_id,
        "title": s.title,
        "pinned": s.pinned,
        "ttl_days": s.ttl_days,
        "system_prompt": s.system_prompt,
        "agent_id": s.agent_id,
        "folder_id": s.folder_id,
        "show_reasoning": s.show_reasoning,
        "proactive_suggestions": s.proactive_suggestions,
        "preferred_model": s.preferred_model,
        # The person who created the session (None: an API key or a channel).
        "owner_user_id": getattr(s, "owner_user_id", None),
        # CHAT-SEC-1: the principal the session belongs to (None: no owner).
        "owner_principal": getattr(s, "owner_principal", None),
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


def _message_to_dict(m: Any) -> dict[str, Any]:
    return {
        "id": m.id,
        "session_id": m.session_id,
        "role": m.role,
        "content": m.content,
        "metadata": m.metadata,
        "intent": m.intent,
        "goal_id": m.goal_id,
        "branch_id": m.branch_id,
        "parent_message_id": m.parent_message_id,
        "created_at": m.created_at.isoformat(),
    }


# ── Session endpoints ─────────────────────────────────────────────────────────


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(body: CreateSessionRequest, request: Request) -> dict[str, Any]:
    tenant, scope = _scope(request)
    svc = _svc(request)
    try:
        s = await svc.acreate_session(
            tenant.tenant_id,
            title=body.title,
            system_prompt=body.system_prompt,
            agent_id=body.agent_id,
            folder_id=body.folder_id,
            owner_user_id=tenant.user_id,
            owner_principal=scope.principal,
        )
    except ChatFolderNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Folder not found") from exc
    return _session_to_dict(s)


@router.get("/sessions")
async def list_sessions(request: Request) -> dict[str, Any]:
    """The caller's own sessions only (newest first, bounded)."""
    tenant, scope = _scope(request)
    svc = _svc(request)
    sessions = await svc.alist_sessions(tenant.tenant_id, scope=scope)
    return {
        "sessions": [_session_to_dict(s) for s in sessions],
        "principal": scope.principal,
    }


@router.get("/skills")
async def list_skills(request: Request) -> dict[str, Any]:
    """Discover the chat command-surface skills available to this tenant (Phase 5)."""
    _tenant(request)
    svc = _svc(request)
    return {"skills": svc.list_skills()}


@router.post("/sessions/{session_id}/attachments", status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    session_id: str, request: Request, file: UploadFile = File(...)
) -> dict[str, Any]:
    """Upload a file; it's parsed and added to the conversation as context (Phase 4)."""
    tenant, svc, scope, _s = await _owned(request, session_id)
    data = await file.read()
    msg = await svc.attach_file(
        session_id=session_id,
        tenant_id=tenant.tenant_id,
        content_bytes=data,
        filename=file.filename or "document",
        author_user_id=tenant.user_id,
        scope=scope,
    )
    if msg is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return _message_to_dict(msg)


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(artifact_id: str, request: Request) -> Response:
    """Download a chat-generated document (Phase 4), tenant-scoped.

    A document generated in a chat session is the session owner's: anyone else
    gets 404 (CHAT-SEC-1).
    """
    tenant = _tenant(request)
    scope = scope_of(tenant)
    if scope is None:
        raise HTTPException(status_code=404, detail="Artifact not found or expired")
    store = getattr(request.app.state, "chat_artifact_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Artifact storage is not available")
    try:
        art = await store.get(artifact_id, tenant.tenant_id, scope=scope)
        if art is not None and art.session_id and not await _svc(request).aget_session(
            art.session_id, tenant.tenant_id, scope=scope
        ):
            art = None
    except Exception as exc:
        # A storage outage is not "not found" (and never leaks driver text).
        logger.warning("chat_artifact_read_failed", error=type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="Artifact storage is unavailable; retry"
        ) from exc
    if art is None:
        raise HTTPException(status_code=404, detail="Artifact not found or expired")
    return Response(
        content=art.content,
        media_type=art.mime,
        headers={"Content-Disposition": f'attachment; filename="{art.filename}"'},
    )


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request) -> dict[str, Any]:
    _tc, _sv, _sc, s = await _owned(request, session_id)
    return _session_to_dict(s)


@router.patch("/sessions/{session_id}")
async def update_session(
    session_id: str, body: UpdateSessionRequest, request: Request
) -> dict[str, Any]:
    tenant, svc, scope, _s = await _owned(request, session_id)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if "ttl_days" in body.model_fields_set and body.ttl_days is None:
        updates["ttl_days"] = None  # an explicit null: the session never expires
    try:
        s = await svc.aupdate_session(session_id, tenant.tenant_id, scope=scope, **updates)
    except ChatFolderNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Folder not found") from exc
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_to_dict(s)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: str, request: Request) -> None:
    tenant, svc, scope, existing = await _owned(request, session_id)
    ok = await svc.adelete_session(session_id, tenant.tenant_id, scope=scope)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")
    owner = getattr(existing, "owner_user_id", None)
    if owner:  # only an owned session can have been indexed as knowledge
        await _remove_session_transcript(request, tenant.tenant_id, owner, session_id)


async def _remove_session_transcript(
    request: Request, tenant_id: str, owner: str, session_id: str, *, what: str = "chat"
) -> None:
    """CHAT-KB: a deleted chat leaves the knowledge index too (held: kept).

    When the removal cannot run now it is queued; when it cannot be queued
    either, the caller is told (the chat itself is already deleted).
    """
    import asyncio

    from app.services import chat_knowledge

    try:
        await chat_knowledge.remove_transcripts(
            request.app.state, tenant_id, user_id=owner, session_ids=[session_id]
        )
        return
    except chat_knowledge.ChatKnowledgeUnavailableError as exc:
        logger.warning("chat_transcript_removal_deferred", session=session_id, error=str(exc))
        reason = str(exc)
    try:
        await asyncio.to_thread(
            chat_knowledge.enqueue_purge_continuation, tenant_id, user_id=owner,
            session_ids=[session_id], unconsented_only=False,
        )
    except Exception as exc:
        logger.warning("chat_transcript_removal_not_queued", session=session_id,
                       error=type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail=(
                f"The {what} was deleted, but the chat's transcript in the knowledge base could "
                f"not be removed yet ({reason}); the knowledge Source's next reconciliation "
                "removes it."
            ),
        ) from exc


@router.post("/sessions/{session_id}/pin")
async def pin_session(session_id: str, request: Request, pinned: bool = True) -> dict[str, Any]:
    tenant, svc, scope, _s = await _owned(request, session_id)
    s = await svc.aupdate_session(session_id, tenant.tenant_id, scope=scope, pinned=pinned)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_to_dict(s)


# ── Message endpoints ─────────────────────────────────────────────────────────


@router.get("/sessions/{session_id}/messages")
async def list_messages(
    session_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    tenant, svc, scope, _s = await _owned(request, session_id)
    msgs = await svc.alist_messages(session_id, tenant.tenant_id, limit=limit, scope=scope)
    return {"messages": [_message_to_dict(m) for m in msgs]}


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str, body: SendMessageRequest, request: Request
) -> dict[str, Any]:
    """Send a message — classifies intent and returns dispatch metadata.

    The actual streaming response comes from the /stream endpoint.
    """
    tenant, svc, scope, _s = await _owned(request, session_id)
    try:
        result = await svc.adispatch(
            session_id, tenant.tenant_id, body.content, author_user_id=tenant.user_id,
            scope=scope,
        )
    except (ValueError, LookupError) as exc:  # gone (deleted) since the check above
        raise HTTPException(status_code=404, detail="Session not found") from exc
    return result


@router.get("/sessions/{session_id}/stream")
async def stream_session(
    session_id: str,
    request: Request,
    message_id: str = Query(...),
) -> StreamingResponse:
    """SSE endpoint — streams the response for a dispatched message."""
    tenant, svc, scope, s = await _owned(request, session_id)

    # Find the message to determine intent (one indexed lookup, owner-scoped).
    msg = await svc.aget_message(session_id, message_id, tenant.tenant_id, scope=scope)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    intent = msg.intent or "QA"
    content = msg.content

    async def _event_gen() -> AsyncGenerator[str, None]:
        if intent == "CLARIFY":
            clarify_req = _intent_router.generate_clarifying_question(content)
            async for chunk in stream_clarify(
                session_id, message_id, clarify_req.question, clarify_req.options
            ):
                yield chunk
        elif intent == "SCHEDULE":
            from app.chat.events import ChatEventType, sse_event

            sc = _intent_router.generate_schedule_confirmation(content)
            schedule_ids: list[str] = []
            error: str | None = None
            if not svc.can_schedule:
                error = "Scheduling is not enabled for this workspace."
            else:
                try:
                    schedule_ids = await svc.create_schedule(
                        tenant_ctx=tenant, message=content, agent_id=s.agent_id
                    )
                except Exception as exc:
                    # Room for the full reason (unsupported type, missing time,
                    # quota, budget) — TRG-10.
                    error = f"Could not create the schedule: {str(exc)[:600]}"
            if error:
                # Do NOT claim the schedule was created when it wasn't — surface it.
                yield sse_event(
                    ChatEventType.ERROR,
                    session_id=session_id,
                    message_id=message_id,
                    message=error,
                )
            else:
                yield sse_event(
                    ChatEventType.SCHEDULE_CREATED,
                    session_id=session_id,
                    message_id=message_id,
                    schedule_ids=schedule_ids,
                    cron_expression=sc.cron_expression,
                    human_schedule=sc.human_schedule,
                )
            yield sse_event(ChatEventType.DONE, session_id=session_id, message_id=message_id)
        elif intent == "GOAL":
            if svc.can_run_goals:
                # Real engine: submit to GoalService with the session's agent
                # (CHAT-D-3), stream its real events.
                try:
                    goal_id = await svc.run_goal(
                        session_id=session_id,
                        tenant_id=tenant.tenant_id,
                        tenant_ctx=tenant,
                        message_id=message_id,
                        user_message=content,
                    )
                except LookupError:
                    from app.chat.events import ChatEventType, sse_event

                    yield sse_event(
                        ChatEventType.ERROR, session_id=session_id, message_id=message_id,
                        message="This chat no longer exists.",
                    )
                    yield sse_event(
                        ChatEventType.DONE, session_id=session_id, message_id=message_id
                    )
                    return
                async for chunk in svc.stream_goal(
                    goal_id=goal_id,
                    tenant_ctx=tenant,
                    session_id=session_id,
                    message_id=message_id,
                ):
                    yield chunk
            else:
                # Legacy simulated fallback (no GoalService wired, e.g. unit tests).
                async for chunk in stream_goal_progress(
                    session_id,
                    message_id,
                    f"goal_{message_id}",
                    steps=[
                        {"name": "planning", "result": "Done"},
                        {"name": "execution", "result": "Done"},
                    ],
                    suggestions=[
                        "You can refine this goal further",
                        "Check the output in the artifacts panel",
                    ],
                ):
                    yield chunk
        elif svc.can_generate_answers:
            # Real QA answer via the LLM + ConversationContext.
            async for chunk in svc.run_qa(
                session_id=session_id,
                tenant_id=tenant.tenant_id,
                message_id=message_id,
                user_message=content,
            ):
                yield chunk
        else:
            # No LLM answer generator is wired. Be honest instead of echoing the
            # question back with a fabricated cost — that misleads the operator into
            # thinking chat works when no provider is configured.
            from app.chat.events import ChatEventType, sse_event

            yield sse_event(
                ChatEventType.ERROR,
                session_id=session_id,
                message_id=message_id,
                message=(
                    "No language model is configured for this workspace, so I can't "
                    "answer yet. Configure an LLM provider/API key to enable chat answers."
                ),
            )
            yield sse_event(ChatEventType.DONE, session_id=session_id, message_id=message_id)

    return StreamingResponse(_event_gen(), media_type="text/event-stream")


@router.patch("/sessions/{session_id}/messages/{message_id}")
async def edit_message(
    session_id: str, message_id: str, body: EditMessageRequest, request: Request
) -> dict[str, Any]:
    tenant, svc, scope, _s = await _owned(request, session_id)
    if await svc.aget_message(session_id, message_id, tenant.tenant_id, scope=scope) is None:
        raise HTTPException(status_code=404, detail="Message not found or not editable")
    msg, pruned = await svc.aedit_message(
        message_id, tenant.tenant_id, body.content, scope=scope
    )
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found or not editable")
    return {"message": _message_to_dict(msg), "pruned_message_ids": pruned}


@router.delete(
    "/sessions/{session_id}/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_message(session_id: str, message_id: str, request: Request) -> None:
    """Delete one of the caller's own messages (CHAT-SEC-2).

    The message is removed from the database, not hidden. The session's
    transcript leaves the knowledge index at once (a held one is kept), and the
    next sync re-indexes the session without it.
    """
    tenant, svc, scope, s = await _owned(request, session_id)
    ok = await svc.adelete_message(session_id, message_id, tenant.tenant_id, scope=scope)
    if not ok:
        raise HTTPException(status_code=404, detail="Message not found")
    owner = getattr(s, "owner_user_id", None)
    if owner:  # only an owned session can have been indexed as knowledge
        await _remove_session_transcript(
            request, tenant.tenant_id, owner, session_id, what="message"
        )


# ── Chat transcripts as knowledge: the person's own opt-in (owner decision 7) ──


class ChatKnowledgeOptIn(BaseModel):
    opted_in: bool


def _person(request: Request) -> tuple[TenantContext, str]:
    tenant = _tenant(request)
    if not tenant.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Only a signed-in person can opt their own chats in or out of knowledge "
                "(an API key has no chats of its own)."
            ),
        )
    return tenant, tenant.user_id


@router.get("/settings/knowledge")
async def get_chat_knowledge_opt_in(request: Request) -> dict[str, Any]:
    """Whether the caller's own chats are indexed as knowledge (off by default)."""
    from app.services.chat_knowledge import ChatKnowledgeUnavailableError, chat_knowledge_for

    tenant, user_id = _person(request)
    settings = chat_knowledge_for(request.app.state)
    try:
        enabled = await settings.tenant_enabled(tenant.tenant_id)
        state = await settings.consent(tenant.tenant_id, user_id)
    except ChatKnowledgeUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"tenant_enabled": enabled, **state.as_dict()}


@router.put("/settings/knowledge")
async def set_chat_knowledge_opt_in(
    body: ChatKnowledgeOptIn, request: Request
) -> dict[str, Any]:
    """Opt the caller's own chats in or out of the workspace's knowledge.

    Opting in needs the workspace switch (an admin's) to be on; only the chat
    sessions the caller created are indexed, under the caller's id, with PII and
    secrets redacted. Revoking stops indexing at once and removes the caller's
    transcripts already indexed (a document under legal hold is kept and
    reported), never anyone else's.
    """
    from app.services.chat_knowledge import (
        KIND_CHAT_TRANSCRIPT,
        ChatKnowledgeUnavailableError,
        chat_knowledge_for,
        remove_transcripts,
    )

    tenant, user_id = _person(request)
    settings = chat_knowledge_for(request.app.state)
    try:
        if body.opted_in and not await settings.tenant_enabled(tenant.tenant_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Chat transcripts are not enabled for this workspace: a workspace admin "
                    "turns them on in Settings first."
                ),
            )
        state = await settings.set_consent(tenant.tenant_id, user_id, body.opted_in)
    except ChatKnowledgeUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if body.opted_in:
        from app.ingestion.agent_generated_events import notify_agent_generated

        await notify_agent_generated(
            tenant.tenant_id, KIND_CHAT_TRANSCRIPT,
            db_factory=getattr(request.app.state, "db_session_factory", None),
        )
        return state.as_dict()
    try:
        report = await remove_transcripts(request.app.state, tenant.tenant_id, user_id=user_id)
    except ChatKnowledgeUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Your opt-in is revoked (nothing more is indexed), but removing your "
                f"transcripts already indexed failed: {exc}. Retry to finish."
            ),
        ) from exc
    return {**state.as_dict(), **report}


# ── Usage endpoints ───────────────────────────────────────────────────────────


@router.get("/sessions/{session_id}/usage")
async def session_usage(session_id: str, request: Request) -> dict[str, Any]:
    tenant, svc, _sc, _s = await _owned(request, session_id)
    return svc.session_usage_summary(session_id, tenant.tenant_id)


# ── Summary ───────────────────────────────────────────────────────────────────


@router.post("/sessions/{session_id}/summarize")
async def summarize_session(session_id: str, request: Request) -> dict[str, Any]:
    tenant, svc, scope, _s = await _owned(request, session_id)
    summary = await svc.asummarize_session(session_id, tenant.tenant_id, scope=scope)
    return {"summary": summary}


# ── Search ────────────────────────────────────────────────────────────────────


@router.post("/search")
async def search_messages(body: SearchRequest, request: Request) -> dict[str, Any]:
    """Full-text search over the caller's own sessions only."""
    tenant, scope = _scope(request)
    svc = _svc(request)
    results = await svc.asearch_messages(
        tenant.tenant_id, body.query, session_id=body.session_id, limit=body.limit,
        scope=scope,
    )
    return {"results": [_message_to_dict(m) for m in results], "total": len(results)}


# ── Folder endpoints ──────────────────────────────────────────────────────────


def _folder_to_dict(f: Any) -> dict[str, Any]:
    return {
        "id": f.id,
        "tenant_id": f.tenant_id,
        "name": f.name,
        "color": f.color,
        "position": f.position,
        "created_at": f.created_at.isoformat(),
        "updated_at": f.updated_at.isoformat(),
    }


# CHAT-D-1: folders are durable (Postgres when wired) and private to their owner,
# like sessions: another principal's folder does not exist (404).


@router.post("/folders", status_code=status.HTTP_201_CREATED)
async def create_folder(body: CreateFolderRequest, request: Request) -> dict[str, Any]:
    tenant, scope = _scope(request)
    try:
        f = await _svc(request).acreate_folder(
            tenant.tenant_id, body.name, body.color, scope=scope
        )
    except ChatFolderLimitError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _folder_to_dict(f)


@router.get("/folders")
async def list_folders(request: Request) -> dict[str, Any]:
    """The caller's own folders (bounded)."""
    tenant, scope = _scope(request)
    folders = await _svc(request).alist_folders(tenant.tenant_id, scope=scope)
    return {"folders": [_folder_to_dict(f) for f in folders]}


@router.patch("/folders/{folder_id}")
async def update_folder(
    folder_id: str, body: UpdateFolderRequest, request: Request
) -> dict[str, Any]:
    """Rename and/or recolor one of the caller's folders."""
    tenant, scope = _scope(request)
    f = await _svc(request).aupdate_folder(
        folder_id, tenant.tenant_id, scope=scope, name=body.name, color=body.color
    )
    if f is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    return _folder_to_dict(f)


@router.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(folder_id: str, request: Request) -> None:
    """Delete one of the caller's folders; its sessions stay, unfiled."""
    tenant, scope = _scope(request)
    if not await _svc(request).adelete_folder(folder_id, tenant.tenant_id, scope=scope):
        raise HTTPException(status_code=404, detail="Folder not found")


@router.post("/sessions/{session_id}/move")
async def move_to_folder(
    session_id: str,
    request: Request,
    folder_id: str | None = Query(default=None, max_length=64),
) -> dict[str, Any]:
    """File the caller's session into one of the caller's folders (none: unfile)."""
    tenant, svc, scope, _s = await _owned(request, session_id)
    try:
        s = await svc.amove_session_to_folder(
            session_id, tenant.tenant_id, folder_id, scope=scope
        )
    except ChatFolderNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Folder not found") from exc
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_to_dict(s)


# ── Artifact endpoints ────────────────────────────────────────────────────────


@router.post("/sessions/{session_id}/artifacts", status_code=status.HTTP_201_CREATED)
async def create_artifact(
    session_id: str, body: CreateArtifactRequest, request: Request
) -> dict[str, Any]:
    tenant, svc, scope, _s = await _owned(request, session_id)
    a = await svc.acreate_artifact(
        session_id, tenant.tenant_id, body.title, body.language, body.content, body.message_id,
        scope=scope,
    )
    return {"id": a.id, "title": a.title, "language": a.language, "content": a.content}


@router.get("/sessions/{session_id}/artifacts")
async def list_artifacts(session_id: str, request: Request) -> dict[str, Any]:
    tenant, svc, scope, _s = await _owned(request, session_id)
    artifacts = await svc.alist_artifacts(session_id, tenant.tenant_id, scope=scope)
    return {
        "artifacts": [
            {"id": a.id, "title": a.title, "language": a.language, "content": a.content}
            for a in artifacts
        ]
    }


@router.patch("/sessions/{session_id}/artifacts/{artifact_id}")
async def update_artifact(
    session_id: str, artifact_id: str, body: UpdateArtifactRequest, request: Request
) -> dict[str, Any]:
    tenant, svc, scope, _s = await _owned(request, session_id)
    a = await svc.aupdate_artifact(
        artifact_id, session_id, tenant.tenant_id, body.content, scope=scope
    )
    if not a:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return {"id": a.id, "title": a.title, "language": a.language, "content": a.content}


@router.delete(
    "/sessions/{session_id}/artifacts/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_artifact(session_id: str, artifact_id: str, request: Request) -> None:
    tenant, svc, scope, _s = await _owned(request, session_id)
    ok = await svc.adelete_artifact(artifact_id, session_id, tenant.tenant_id, scope=scope)
    if not ok:
        raise HTTPException(status_code=404, detail="Artifact not found")


# ── Models endpoint ───────────────────────────────────────────────────────────


@router.get("/models")
async def list_models(request: Request) -> dict[str, Any]:
    """Return available LLM models for the model selector."""
    _tenant(request)  # require auth
    return {"models": _intent_router.available_models()}


# ── Inline code execution ─────────────────────────────────────────────────────


class ExecuteCodeRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=50_000)
    language: str = Field(default="python")


@router.post("/sessions/{session_id}/execute")
async def execute_code(
    session_id: str,
    body: ExecuteCodeRequest,
    request: Request,
    # Running code is an operator action; a viewer key used to be enough.
    _rbac: None = Depends(require_role("operator")),
) -> dict[str, Any]:
    tenant, _sv, _sc, _s = await _owned(request, session_id)
    from app.tools.code_execution import AuditPersistenceError, CodeExecutionBusyError

    try:
        result = await _executor.execute(
            body.code,
            body.language,
            session_id,
            tenant_ctx=tenant,
            audit_log=getattr(request.app.state, "audit_log", None),
            redis=getattr(request.app.state, "_redis", None),
        )
    except CodeExecutionBusyError as exc:
        raise HTTPException(
            status_code=429,
            detail=f"Too many concurrent code executions ({exc.scope} limit {exc.limit}).",
            headers={"Retry-After": "5"},
        ) from exc
    except AuditPersistenceError as exc:
        # Never an unaudited 200 for arbitrary code execution.
        raise HTTPException(
            status_code=503, detail="Code execution could not be audited; result withheld."
        ) from exc
    return {
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "language": result.language,
        "duration_ms": result.duration_ms,
        "truncated": result.truncated,
        "error": result.error,
    }


# ── Within-session search ─────────────────────────────────────────────────────


@router.get("/sessions/{session_id}/search")
async def within_session_search(
    session_id: str,
    request: Request,
    q: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    tenant, svc, scope, _s = await _owned(request, session_id)
    # a08-F186-01: the whole session's history through the Postgres full-text
    # index (in DB mode), not a substring scan of the most recent 500 messages.
    msgs = await svc.asearch_messages(
        tenant.tenant_id, q, session_id=session_id, limit=limit, scope=scope
    )
    return {
        "results": [
            {
                "message_id": m.id,
                "session_id": m.session_id,
                "role": m.role,
                "snippet": _search_engine.snippet(m.content, q),
                "created_at": m.created_at.isoformat(),
            }
            for m in msgs
        ],
        "total": len(msgs),
    }


# ── Memory management ─────────────────────────────────────────────────────────


class CreateMemoryRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5_000)


class UpdateMemoryRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5_000)


def _memory_to_dict(m: Any) -> dict[str, Any]:
    return {
        "id": m.id,
        "content": m.content,
        "source": m.source,
        "created_at": m.created_at.isoformat(),
        "updated_at": m.updated_at.isoformat(),
    }


def _ltm(request: Request) -> Any:
    """The DB-backed LongTermMemoryStore from app.state (None in lifespan-less tests)."""
    return getattr(request.app.state, "long_term_memory", None)


async def _ltm_call(awaitable: Any) -> Any:
    """Await a LongTermMemoryStore DB call, mapping store failure to 503.

    The store used to swallow DB errors into ``[]`` / ``False`` / ``0`` so a
    failed GDPR erasure answered ``{"deleted": 0}`` with a 200.
    """
    from app.memory.long_term import LongTermMemoryBlockedError, LongTermMemoryUnavailableError

    try:
        return await awaitable
    except LongTermMemoryBlockedError as exc:
        # MEM-03: a guardrail block is the caller's content, not an outage.
        raise HTTPException(
            status_code=422,
            detail="Memory content rejected by the memory-write guardrail",
        ) from exc
    except LongTermMemoryUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Memory store unavailable; nothing was changed. Retry later.",
        ) from exc


def _ltm_to_dict(m: Any) -> dict[str, Any]:
    return {
        "id": m.memory_id,
        "content": m.content,
        "source": getattr(m, "memory_type", "chat"),
        "confidence": getattr(m, "confidence", 1.0),
        "created_at": getattr(m, "created_at", ""),
    }


@router.get("/memories")
async def list_memories(request: Request) -> dict[str, Any]:
    tenant = _tenant(request)
    ltm = _ltm(request)
    if ltm is not None:
        mems = await _ltm_call(ltm.list_all_async(tenant_ctx=tenant))
        return {"memories": [_ltm_to_dict(m) for m in mems]}
    return {"memories": [_memory_to_dict(m) for m in _memory_api.list_memories(tenant.tenant_id)]}


@router.post("/memories", status_code=status.HTTP_201_CREATED)
async def create_memory(body: CreateMemoryRequest, request: Request) -> dict[str, Any]:
    tenant = _tenant(request)
    ltm = _ltm(request)
    if ltm is not None:
        embedder = getattr(request.app.state, "embedder", None)
        m = await _ltm_call(
            ltm.create_user_memory_async(
                content=body.content, tenant_ctx=tenant, embedder=embedder
            )
        )
        return _ltm_to_dict(m)
    return _memory_to_dict(_memory_api.create_memory(tenant.tenant_id, body.content))


@router.patch("/memories/{memory_id}")
async def update_memory(
    memory_id: str, body: UpdateMemoryRequest, request: Request
) -> dict[str, Any]:
    tenant = _tenant(request)
    ltm = _ltm(request)
    if ltm is not None:
        m = await _ltm_call(
            ltm.update_content_async(
                memory_id=memory_id,
                content=body.content,
                tenant_ctx=tenant,
                embedder=getattr(request.app.state, "embedder", None),
            )
        )
        if not m:
            raise HTTPException(status_code=404, detail="Memory not found")
        return _ltm_to_dict(m)
    m = _memory_api.update_memory(memory_id, tenant.tenant_id, body.content)
    if not m:
        raise HTTPException(status_code=404, detail="Memory not found")
    return _memory_to_dict(m)


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: str, request: Request) -> None:
    tenant = _tenant(request)
    ltm = _ltm(request)
    ok = (
        await _ltm_call(ltm.delete_async(memory_id=memory_id, tenant_ctx=tenant))
        if ltm is not None
        else _memory_api.delete_memory(memory_id, tenant.tenant_id)
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Memory not found")


@router.delete("/memories", status_code=status.HTTP_200_OK)
async def delete_all_memories(
    request: Request,
    x_confirm_gdpr_delete: str | None = Header(default=None),
) -> dict[str, Any]:
    """GDPR right-to-erasure — requires X-Confirm-Gdpr-Delete: yes header."""
    if x_confirm_gdpr_delete != "yes":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Set header X-Confirm-Gdpr-Delete: yes to confirm bulk deletion",
        )
    tenant = _tenant(request)
    ltm = _ltm(request)
    count = (
        await _ltm_call(ltm.delete_all_async(tenant_ctx=tenant))
        if ltm is not None
        else _memory_api.delete_all_memories(tenant.tenant_id)
    )
    return {"deleted": count}


# ── Templates ─────────────────────────────────────────────────────────────────


class CreateTemplateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    # Same bound as the goal-template API (app/api/templates.py) whose table
    # this persona is stored in.
    description: str = Field(default="", max_length=2000)
    system_prompt: str = Field(..., min_length=1, max_length=10_000)

    @field_validator("name", "description", "system_prompt")
    @classmethod
    def _storable_text(cls, value: str) -> str:
        # Postgres TEXT cannot store U+0000, and a lone UTF-16 surrogate (JSON
        # "\ud800", which json.loads accepts) cannot be encoded as UTF-8 by
        # asyncpg. Both used to pass validation and then fail at INSERT time,
        # answering 500 for schema-valid JSON. Reject them as a 422 instead.
        if "\x00" in value:
            raise ValueError("must not contain NUL (\\u0000) characters")
        if any("\ud800" <= ch <= "\udfff" for ch in value):
            raise ValueError("must not contain unpaired UTF-16 surrogates")
        return value


# Chat personas are persisted in the shared DB-backed goal-template store under a
# reserved domain so they survive restarts and stay isolated from goal templates.
CHAT_PERSONA_DOMAIN = "chat_persona"


def _tmpl_store(request: Request) -> Any:
    """The DB-backed template store (app.api.templates), or None in tests w/o pools."""
    return getattr(request.app.state, "template_store", None)


def _persona_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Map a goal_templates row onto the chat persona shape (system_prompt<-goal_text)."""
    created = row.get("created_at", "")
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row.get("description", ""),
        "system_prompt": row.get("goal_text", ""),
        "builtin": False,
        "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
    }


@router.get("/templates")
async def list_templates(request: Request) -> dict[str, Any]:
    tenant = _tenant(request)
    store = _tmpl_store(request)
    if store is not None:
        rows = await store.list(tenant.tenant_id, domain=CHAT_PERSONA_DOMAIN)
        user = [_persona_from_row(r) for r in rows]
        return {"templates": BUILT_IN_TEMPLATES + user}
    return {"templates": _template_store.list_templates(tenant.tenant_id)}


@router.post("/templates", status_code=status.HTTP_201_CREATED)
async def create_template(body: CreateTemplateRequest, request: Request) -> dict[str, Any]:
    tenant = _tenant(request)
    store = _tmpl_store(request)
    if store is not None:
        row = await store.create(
            tenant.tenant_id,
            name=body.name,
            description=body.description,
            goal_text=body.system_prompt,
            domain=CHAT_PERSONA_DOMAIN,
            parameters=[],
        )
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row.get("description", ""),
            "builtin": False,
        }
    t = _template_store.create_template(
        tenant.tenant_id, body.name, body.description, body.system_prompt
    )
    return {"id": t.id, "name": t.name, "description": t.description, "builtin": False}


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: str, request: Request) -> None:
    tenant = _tenant(request)
    # Built-ins are constants (id "builtin_*"), never in the DB — reject them the
    # same way regardless of backend.
    if template_id.startswith("builtin_"):
        raise HTTPException(status_code=404, detail="Template not found or is built-in")
    store = _tmpl_store(request)
    if store is not None:
        ok = await store.delete(tenant.tenant_id, template_id)
    else:
        ok = _template_store.delete_template(template_id, tenant.tenant_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Template not found or is built-in")


# ── Connected services panel ──────────────────────────────────────────────────


class ConnectServiceRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    url: str = Field(..., min_length=1, max_length=2_000)
    scopes: list[str] = Field(default_factory=list)


@router.get("/services")
async def list_services(request: Request) -> dict[str, Any]:
    tenant = _tenant(request)
    services = await _services_api.list_services_async(tenant.tenant_id)
    return {
        "services": [
            {
                "id": s.id,
                "name": s.name,
                "url": s.url,
                "scopes": s.scopes,
                "status": s.status,
                "created_at": s.created_at.isoformat(),
                # None while the OAuth flow is still pending — honest "not connected yet".
                "connected_at": s.connected_at.isoformat() if s.connected_at else None,
            }
            for s in services
        ]
    }


@router.post("/services", status_code=status.HTTP_201_CREATED)
async def connect_service(body: ConnectServiceRequest, request: Request) -> dict[str, Any]:
    tenant = _tenant(request)
    result = await _services_api.initiate_connection_async(
        tenant.tenant_id, body.name, body.url, body.scopes
    )
    # status is "pending": this panel has no OAuth client, so there is no
    # authorization URL to open (it used to be a made-up agentverse.app link).
    return {
        "service_id": result["service_id"],
        "oauth_url": result["oauth_url"],
        "authorize_via": result["authorize_via"],
        "status": "pending",
    }


@router.post("/services/{service_id}/complete")
async def complete_service(service_id: str, request: Request) -> dict[str, Any]:
    """Never marks a service connected without an OAuth code exchange (ORG-35).

    It used to flip status to ``connected`` with no code and no token. There is no
    OAuth client behind this panel, so there is nothing to exchange: 501, and the
    service stays ``pending``.
    """
    _tenant(request)
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "Connected services cannot be authorized here. Authorize the connector "
            "through the Connectors OAuth flow (POST /connectors/{server_id}/oauth/start)."
        ),
    )


@router.delete("/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_service(service_id: str, request: Request) -> None:
    tenant = _tenant(request)
    ok = await _services_api.disconnect_service_async(service_id, tenant.tenant_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Service not found")


# ── Feedback ──────────────────────────────────────────────────────────────────


class FeedbackRequest(BaseModel):
    rating: int = Field(..., ge=-1, le=1)
    comment: str | None = None


@router.post("/sessions/{session_id}/messages/{message_id}/feedback")
async def submit_feedback(
    session_id: str, message_id: str, body: FeedbackRequest, request: Request
) -> dict[str, Any]:
    tenant, svc, scope, _s = await _owned(request, session_id)
    msg = await svc.aget_message(session_id, message_id, tenant.tenant_id, scope=scope)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    # Store feedback in metadata
    msg.metadata["feedback"] = {"rating": body.rating, "comment": body.comment}
    return {"status": "ok", "rating": body.rating}


# ── Export ────────────────────────────────────────────────────────────────────


@router.get("/sessions/{session_id}/export")
async def export_session(session_id: str, request: Request) -> dict[str, Any]:
    """Export session as clean Markdown."""
    tenant, svc, scope, s = await _owned(request, session_id)
    msgs = await svc.alist_messages(session_id, tenant.tenant_id, scope=scope)
    lines = [f"# {s.title}\n"]
    for m in msgs:
        prefix = "**User**" if m.role == "user" else "**Assistant**"
        lines.append(f"{prefix}: {m.content}\n")
    return {"markdown": "\n".join(lines), "session_id": session_id, "title": s.title}


# ── Admin: chat sessions with no owner (CHAT-SEC-1) ───────────────────────────
# Sessions created by a channel (Telegram, Slack, voice ...) or before session
# ownership existed have no owner. No ordinary route reaches them. A tenant admin
# can list them, read one, or assign one to a person (after which it is that
# person's private session and leaves this view). Every call is durably audited
# BEFORE anything is returned or changed; an audit failure is a 503 and nothing
# is read or changed. These routes never reach a session that has an owner.


class AssignSessionRequest(BaseModel):
    owner_user_id: str = Field(..., min_length=1, max_length=64)


async def _audit_admin(request: Request, tenant: TenantContext, action: str, note: str) -> None:
    from app.governance.audit import AuditEvent, AuditWriteError
    from app.governance.permissions import ActionLevel

    audit_log = getattr(request.app.state, "audit_log", None)
    if audit_log is None:
        raise HTTPException(status_code=503, detail="Audit log unavailable; nothing was done.")
    try:
        await audit_log.record_durable(
            AuditEvent(
                goal_id="chat.admin",
                tool_name=f"chat.admin.{action}",
                action_level=ActionLevel.ALLOW_LOG,
                outcome="allowed",
                api_key_id=tenant.api_key_id,
                note=note[:500],
            ),
            tenant_ctx=tenant,
        )
    except AuditWriteError as exc:
        logger.warning("chat_admin_audit_failed", action=action, error=type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="The access could not be audited; nothing was done."
        ) from exc


async def _unowned_session(request: Request, session_id: str) -> tuple[TenantContext, Any]:
    tenant = _tenant(request)
    s = await _svc(request).aget_session(session_id, tenant.tenant_id, scope=UNOWNED_SCOPE)
    if s is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return tenant, s


@router.get("/admin/sessions/unowned", dependencies=[Depends(require_role("admin"))])
async def admin_list_unowned_sessions(request: Request) -> dict[str, Any]:
    """Sessions with no owner (channels, older sessions); admin only, audited."""
    tenant = _tenant(request)
    await _audit_admin(
        request, tenant, "list_unowned",
        f"admin {principal_of(tenant)} listed the chat sessions with no owner",
    )
    sessions = await _svc(request).alist_sessions(tenant.tenant_id, scope=UNOWNED_SCOPE)
    return {"sessions": [_session_to_dict(s) for s in sessions]}


@router.get(
    "/admin/sessions/{session_id}/messages", dependencies=[Depends(require_role("admin"))]
)
async def admin_read_unowned_session(
    session_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Read a session with no owner; admin only, audited before it is read."""
    tenant, _s = await _unowned_session(request, session_id)
    await _audit_admin(
        request, tenant, "read_unowned",
        f"admin {principal_of(tenant)} read chat session {session_id} (no owner)",
    )
    msgs = await _svc(request).alist_messages(
        session_id, tenant.tenant_id, limit=limit, scope=UNOWNED_SCOPE
    )
    return {"messages": [_message_to_dict(m) for m in msgs]}


@router.post(
    "/admin/sessions/{session_id}/assign", dependencies=[Depends(require_role("admin"))]
)
async def admin_assign_unowned_session(
    session_id: str, body: AssignSessionRequest, request: Request
) -> dict[str, Any]:
    """Give a session with no owner to a person (it becomes theirs, privately)."""
    tenant, _s = await _unowned_session(request, session_id)
    await _audit_admin(
        request, tenant, "assign",
        f"admin {principal_of(tenant)} assigned chat session {session_id} "
        f"to user {body.owner_user_id}",
    )
    s = await _svc(request).aassign_unowned_session(
        session_id, tenant.tenant_id, owner_user_id=body.owner_user_id
    )
    if s is None:  # assigned or deleted meanwhile
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_to_dict(s)


@router.delete(
    "/admin/sessions/{session_id}/messages/{message_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role("admin"))],
)
async def admin_delete_message(session_id: str, message_id: str, request: Request) -> None:
    """A tenant admin deletes a message from any session of the tenant (CHAT-SEC-2).

    Moderation without reading: nothing of the message is returned. Audited
    durably before the delete (audit failure: 503, nothing deleted). The
    session's transcript leaves the knowledge index like an owner's delete.
    """
    from app.chat.ownership import SYSTEM_SCOPE

    tenant = _tenant(request)
    svc = _svc(request)
    s = await svc.aget_session(session_id, tenant.tenant_id, scope=SYSTEM_SCOPE)
    if s is None or await svc.aget_message(
        session_id, message_id, tenant.tenant_id, scope=SYSTEM_SCOPE
    ) is None:
        raise HTTPException(status_code=404, detail="Message not found")
    await _audit_admin(
        request, tenant, "delete_message",
        f"admin {principal_of(tenant)} deleted message {message_id} of chat session "
        f"{session_id} (owner {getattr(s, 'owner_principal', None) or 'none'})",
    )
    if not await svc.adelete_message(
        session_id, message_id, tenant.tenant_id, scope=SYSTEM_SCOPE
    ):
        raise HTTPException(status_code=404, detail="Message not found")
    owner = getattr(s, "owner_user_id", None)
    if owner:
        await _remove_session_transcript(
            request, tenant.tenant_id, owner, session_id, what="message"
        )
