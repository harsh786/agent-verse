"""Chat API router — 33 endpoints for the chat feature.

All endpoints require a valid tenant (API key auth via TenantMiddleware).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from app.chat.execution import ChatCodeExecutor
from app.chat.intent import IntentRouter
from app.chat.memory_api import MemoryAPI
from app.chat.search import ChatSearchEngine
from app.chat.service import ChatService
from app.chat.services_api import ServicesAPI
from app.chat.stream import (
    stream_clarify,
    stream_goal_progress,
    stream_qa_response,
    stream_schedule_created,
)
from app.chat.templates import TemplateStore
from app.providers.model_defaults import configured_default_model as _configured_default_model
from app.tenancy.context import TenantContext

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
    title: str | None = None
    system_prompt: str | None = None
    pinned: bool | None = None
    folder_id: str | None = None
    show_reasoning: bool | None = None
    proactive_suggestions: bool | None = None
    preferred_model: str | None = None


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=32_000)
    model_override: str | None = None
    branch_from_message_id: str | None = None


class EditMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=32_000)


class CreateFolderRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    color: str = "#6366f1"


class CreateArtifactRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    language: str = "text"
    content: str = ""
    message_id: str | None = None


class UpdateArtifactRequest(BaseModel):
    content: str


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
    tenant = _tenant(request)
    svc = _svc(request)
    s = svc.create_session(
        tenant.tenant_id,
        title=body.title,
        system_prompt=body.system_prompt,
        agent_id=body.agent_id,
        folder_id=body.folder_id,
    )
    return _session_to_dict(s)


@router.get("/sessions")
async def list_sessions(request: Request) -> dict[str, Any]:
    tenant = _tenant(request)
    svc = _svc(request)
    sessions = svc.list_sessions(tenant.tenant_id)
    return {"sessions": [_session_to_dict(s) for s in sessions]}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request) -> dict[str, Any]:
    tenant = _tenant(request)
    svc = _svc(request)
    s = svc.get_session(session_id, tenant.tenant_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_to_dict(s)


@router.patch("/sessions/{session_id}")
async def update_session(
    session_id: str, body: UpdateSessionRequest, request: Request
) -> dict[str, Any]:
    tenant = _tenant(request)
    svc = _svc(request)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    s = svc.update_session(session_id, tenant.tenant_id, **updates)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_to_dict(s)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: str, request: Request) -> None:
    tenant = _tenant(request)
    svc = _svc(request)
    ok = svc.delete_session(session_id, tenant.tenant_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session not found")


@router.post("/sessions/{session_id}/pin")
async def pin_session(session_id: str, request: Request, pinned: bool = True) -> dict[str, Any]:
    tenant = _tenant(request)
    svc = _svc(request)
    s = svc.pin_session(session_id, tenant.tenant_id, pinned)
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
    tenant = _tenant(request)
    svc = _svc(request)
    s = svc.get_session(session_id, tenant.tenant_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    msgs = svc.list_messages(session_id, tenant.tenant_id, limit=limit)
    return {"messages": [_message_to_dict(m) for m in msgs]}


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str, body: SendMessageRequest, request: Request
) -> dict[str, Any]:
    """Send a message — classifies intent and returns dispatch metadata.

    The actual streaming response comes from the /stream endpoint.
    """
    tenant = _tenant(request)
    svc = _svc(request)
    s = svc.get_session(session_id, tenant.tenant_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    result = svc.dispatch(session_id, tenant.tenant_id, body.content)
    return result


@router.get("/sessions/{session_id}/stream")
async def stream_session(
    session_id: str,
    request: Request,
    message_id: str = Query(...),
) -> StreamingResponse:
    """SSE endpoint — streams the response for a dispatched message."""
    tenant = _tenant(request)
    svc = _svc(request)
    s = svc.get_session(session_id, tenant.tenant_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")

    # Find the message to determine intent
    msgs = svc.list_messages(session_id, tenant.tenant_id)
    msg = next((m for m in msgs if m.id == message_id), None)
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
            sc = _intent_router.generate_schedule_confirmation(content)
            async for chunk in stream_schedule_created(
                session_id, message_id, sc.cron_expression, sc.human_schedule
            ):
                yield chunk
        elif intent == "GOAL":
            if svc.can_run_goals:
                # Real engine: submit to GoalService, stream its real events.
                goal_id = await svc.run_goal(
                    session_id=session_id,
                    tenant_id=tenant.tenant_id,
                    tenant_ctx=tenant,
                    message_id=message_id,
                    user_message=content,
                )
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
            # Legacy simulated QA fallback (no answer generator wired).
            tokens = [w + " " for w in f"Answering: {content}".split()]
            async for chunk in stream_qa_response(
                session_id,
                message_id,
                tokens,
                usage={
                    "tokens_in": len(content),
                    "tokens_out": len(tokens) * 3,
                    "cost_usd": 0.0001,
                    "model": _configured_default_model("gpt-4o"),
                },
                show_reasoning=s.show_reasoning,
            ):
                yield chunk

    return StreamingResponse(_event_gen(), media_type="text/event-stream")


@router.patch("/sessions/{session_id}/messages/{message_id}")
async def edit_message(
    session_id: str, message_id: str, body: EditMessageRequest, request: Request
) -> dict[str, Any]:
    tenant = _tenant(request)
    svc = _svc(request)
    s = svc.get_session(session_id, tenant.tenant_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    msg, pruned = svc.edit_message(message_id, tenant.tenant_id, body.content)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found or not editable")
    return {"message": _message_to_dict(msg), "pruned_message_ids": pruned}


@router.delete(
    "/sessions/{session_id}/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_message(session_id: str, message_id: str, request: Request) -> None:
    tenant = _tenant(request)
    svc = _svc(request)
    ok = svc.delete_message(message_id, tenant.tenant_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Message not found")


# ── Usage endpoints ───────────────────────────────────────────────────────────


@router.get("/sessions/{session_id}/usage")
async def session_usage(session_id: str, request: Request) -> dict[str, Any]:
    tenant = _tenant(request)
    svc = _svc(request)
    s = svc.get_session(session_id, tenant.tenant_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    return svc.session_usage_summary(session_id, tenant.tenant_id)


# ── Summary ───────────────────────────────────────────────────────────────────


@router.post("/sessions/{session_id}/summarize")
async def summarize_session(session_id: str, request: Request) -> dict[str, Any]:
    tenant = _tenant(request)
    svc = _svc(request)
    s = svc.get_session(session_id, tenant.tenant_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    summary = svc.summarize_session(session_id, tenant.tenant_id)
    return {"summary": summary}


# ── Search ────────────────────────────────────────────────────────────────────


@router.post("/search")
async def search_messages(body: SearchRequest, request: Request) -> dict[str, Any]:
    tenant = _tenant(request)
    svc = _svc(request)
    results = svc.search_messages(
        tenant.tenant_id, body.query, session_id=body.session_id, limit=body.limit
    )
    return {"results": [_message_to_dict(m) for m in results], "total": len(results)}


# ── Folder endpoints ──────────────────────────────────────────────────────────


@router.post("/folders", status_code=status.HTTP_201_CREATED)
async def create_folder(body: CreateFolderRequest, request: Request) -> dict[str, Any]:
    tenant = _tenant(request)
    svc = _svc(request)
    f = svc.create_folder(tenant.tenant_id, body.name, body.color)
    return {"id": f.id, "name": f.name, "color": f.color, "tenant_id": f.tenant_id}


@router.get("/folders")
async def list_folders(request: Request) -> dict[str, Any]:
    tenant = _tenant(request)
    svc = _svc(request)
    folders = svc.list_folders(tenant.tenant_id)
    return {"folders": [{"id": f.id, "name": f.name, "color": f.color} for f in folders]}


@router.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(folder_id: str, request: Request) -> None:
    tenant = _tenant(request)
    svc = _svc(request)
    ok = svc.delete_folder(folder_id, tenant.tenant_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Folder not found")


@router.post("/sessions/{session_id}/move")
async def move_to_folder(
    session_id: str,
    request: Request,
    folder_id: str | None = None,
) -> dict[str, Any]:
    tenant = _tenant(request)
    svc = _svc(request)
    s = svc.move_session_to_folder(session_id, tenant.tenant_id, folder_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_to_dict(s)


# ── Artifact endpoints ────────────────────────────────────────────────────────


@router.post("/sessions/{session_id}/artifacts", status_code=status.HTTP_201_CREATED)
async def create_artifact(
    session_id: str, body: CreateArtifactRequest, request: Request
) -> dict[str, Any]:
    tenant = _tenant(request)
    svc = _svc(request)
    s = svc.get_session(session_id, tenant.tenant_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    a = svc.create_artifact(
        session_id, tenant.tenant_id, body.title, body.language, body.content, body.message_id
    )
    return {"id": a.id, "title": a.title, "language": a.language, "content": a.content}


@router.get("/sessions/{session_id}/artifacts")
async def list_artifacts(session_id: str, request: Request) -> dict[str, Any]:
    tenant = _tenant(request)
    svc = _svc(request)
    artifacts = svc.list_artifacts(session_id, tenant.tenant_id)
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
    tenant = _tenant(request)
    svc = _svc(request)
    a = svc.update_artifact(artifact_id, tenant.tenant_id, body.content)
    if not a:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return {"id": a.id, "title": a.title, "language": a.language, "content": a.content}


@router.delete(
    "/sessions/{session_id}/artifacts/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_artifact(session_id: str, artifact_id: str, request: Request) -> None:
    tenant = _tenant(request)
    svc = _svc(request)
    ok = svc.delete_artifact(artifact_id, tenant.tenant_id)
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
    session_id: str, body: ExecuteCodeRequest, request: Request
) -> dict[str, Any]:
    tenant = _tenant(request)
    svc = _svc(request)
    s = svc.get_session(session_id, tenant.tenant_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    result = _executor.execute(body.code, body.language, session_id)
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
    tenant = _tenant(request)
    svc = _svc(request)
    msgs = svc.list_messages(session_id, tenant.tenant_id, limit=500)
    raw = [
        {
            "id": m.id,
            "session_id": m.session_id,
            "tenant_id": m.tenant_id,
            "role": m.role,
            "content": m.content,
            "created_at": m.created_at.isoformat(),
        }
        for m in msgs
    ]
    results = _search_engine.within_session_search(
        q, raw, tenant.tenant_id, session_id, limit=limit
    )
    return {
        "results": [
            {
                "message_id": r.message_id,
                "session_id": r.session_id,
                "role": r.role,
                "snippet": r.snippet,
                "created_at": r.created_at,
            }
            for r in results
        ],
        "total": len(results),
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


@router.get("/memories")
async def list_memories(request: Request) -> dict[str, Any]:
    tenant = _tenant(request)
    memories = _memory_api.list_memories(tenant.tenant_id)
    return {"memories": [_memory_to_dict(m) for m in memories]}


@router.post("/memories", status_code=status.HTTP_201_CREATED)
async def create_memory(body: CreateMemoryRequest, request: Request) -> dict[str, Any]:
    tenant = _tenant(request)
    m = _memory_api.create_memory(tenant.tenant_id, body.content)
    return _memory_to_dict(m)


@router.patch("/memories/{memory_id}")
async def update_memory(
    memory_id: str, body: UpdateMemoryRequest, request: Request
) -> dict[str, Any]:
    tenant = _tenant(request)
    m = _memory_api.update_memory(memory_id, tenant.tenant_id, body.content)
    if not m:
        raise HTTPException(status_code=404, detail="Memory not found")
    return _memory_to_dict(m)


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(memory_id: str, request: Request) -> None:
    tenant = _tenant(request)
    ok = _memory_api.delete_memory(memory_id, tenant.tenant_id)
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
    count = _memory_api.delete_all_memories(tenant.tenant_id)
    return {"deleted": count}


# ── Templates ─────────────────────────────────────────────────────────────────


class CreateTemplateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    system_prompt: str = Field(..., min_length=1, max_length=10_000)


@router.get("/templates")
async def list_templates(request: Request) -> dict[str, Any]:
    tenant = _tenant(request)
    return {"templates": _template_store.list_templates(tenant.tenant_id)}


@router.post("/templates", status_code=status.HTTP_201_CREATED)
async def create_template(body: CreateTemplateRequest, request: Request) -> dict[str, Any]:
    tenant = _tenant(request)
    t = _template_store.create_template(
        tenant.tenant_id, body.name, body.description, body.system_prompt
    )
    return {"id": t.id, "name": t.name, "description": t.description, "builtin": False}


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: str, request: Request) -> None:
    tenant = _tenant(request)
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
    services = _services_api.list_services(tenant.tenant_id)
    return {
        "services": [
            {
                "id": s.id,
                "name": s.name,
                "url": s.url,
                "scopes": s.scopes,
                "status": s.status,
                "connected_at": s.connected_at.isoformat(),
            }
            for s in services
        ]
    }


@router.post("/services", status_code=status.HTTP_201_CREATED)
async def connect_service(body: ConnectServiceRequest, request: Request) -> dict[str, Any]:
    tenant = _tenant(request)
    result = _services_api.initiate_connection(tenant.tenant_id, body.name, body.url, body.scopes)
    return {
        "service_id": result["service_id"],
        "oauth_url": result["oauth_url"],
    }


@router.delete("/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_service(service_id: str, request: Request) -> None:
    tenant = _tenant(request)
    ok = _services_api.disconnect_service(service_id, tenant.tenant_id)
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
    tenant = _tenant(request)
    svc = _svc(request)
    msgs = svc.list_messages(session_id, tenant.tenant_id)
    msg = next((m for m in msgs if m.id == message_id), None)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    # Store feedback in metadata
    msg.metadata["feedback"] = {"rating": body.rating, "comment": body.comment}
    return {"status": "ok", "rating": body.rating}


# ── Export ────────────────────────────────────────────────────────────────────


@router.get("/sessions/{session_id}/export")
async def export_session(session_id: str, request: Request) -> dict[str, Any]:
    """Export session as clean Markdown."""
    tenant = _tenant(request)
    svc = _svc(request)
    s = svc.get_session(session_id, tenant.tenant_id)
    if not s:
        raise HTTPException(status_code=404, detail="Session not found")
    msgs = svc.list_messages(session_id, tenant.tenant_id)
    lines = [f"# {s.title}\n"]
    for m in msgs:
        prefix = "**User**" if m.role == "user" else "**Assistant**"
        lines.append(f"{prefix}: {m.content}\n")
    return {"markdown": "\n".join(lines), "session_id": session_id, "title": s.title}
