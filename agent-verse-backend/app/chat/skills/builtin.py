"""Built-in chat skills — thin adapters over existing platform services (Phase 5).

Each builder closes over a real service and returns a ChatSkill. register_builtin_
skills wires the ones whose services are available onto a registry. No new business
logic lives here — skills just translate an NL command into an existing call.
"""

from __future__ import annotations

from typing import Any

from app.chat.skills.registry import ChatSkill, SkillRegistry


def build_list_connected_services_skill(services_api: Any) -> ChatSkill:
    async def handler(tenant_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": getattr(s, "id", None),
                "name": getattr(s, "name", None),
                "status": getattr(s, "status", None),
            }
            for s in services_api.list_services(tenant_id)
        ]

    return ChatSkill(
        name="list_connected_services",
        description="List the external services/connectors this tenant has connected.",
        handler=handler,
        args={"tenant_id": "the tenant id"},
        scope="connectors:read",
    )


def build_submit_goal_skill(goal_service: Any) -> ChatSkill:
    async def handler(
        tenant_ctx: Any, goal: str, agent_id: str | None = None
    ) -> dict[str, Any]:
        return await goal_service.submit_goal(
            goal=goal,
            priority="normal",
            dry_run=False,
            tenant_ctx=tenant_ctx,
            agent_id=agent_id,
        )

    return ChatSkill(
        name="submit_goal",
        description="Run an autonomous goal (plan→execute→verify with tools/connectors).",
        handler=handler,
        args={"goal": "what to accomplish", "agent_id": "optional agent to route to"},
        scope="goals:write",
    )


def build_list_schedules_skill(schedule_store: Any) -> ChatSkill:
    async def handler(tenant_ctx: Any) -> list[dict[str, Any]]:
        return list(schedule_store.list_all(tenant_ctx=tenant_ctx))

    return ChatSkill(
        name="list_schedules",
        description="List the tenant's recurring/scheduled triggers.",
        handler=handler,
        args={},
        scope="triggers:read",
    )


def build_generate_document_skill(artifact_store: Any) -> ChatSkill:
    async def handler(
        tenant_id: str, content: str, fmt: str = "pdf", filename: str | None = None
    ) -> dict[str, Any]:
        from app.chat.documents import generate_document, mime_for

        data = generate_document(content, fmt)
        name = filename or f"document.{fmt.lower()}"
        artifact_id = artifact_store.put(
            tenant_id=tenant_id, content=data, mime=mime_for(fmt), filename=name
        )
        return {
            "artifact_id": artifact_id,
            "filename": name,
            "mime": mime_for(fmt),
            "download_url": f"/chat/artifacts/{artifact_id}/download",
        }

    return ChatSkill(
        name="generate_document",
        description="Generate a downloadable document (pdf/md/csv/txt/json) from content.",
        handler=handler,
        args={
            "content": "the document body",
            "fmt": "pdf|md|csv|txt|json",
            "filename": "optional filename",
        },
        scope="documents:write",
    )


def build_list_pending_approvals_skill(hitl_gateway: Any) -> ChatSkill:
    async def handler(tenant_ctx: Any, goal_id: str | None = None) -> list[dict[str, Any]]:
        reqs = hitl_gateway.list_pending(tenant_ctx=tenant_ctx, goal_id=goal_id)
        return [
            {
                "request_id": getattr(r, "request_id", None) or getattr(r, "id", None),
                "goal_id": getattr(r, "goal_id", None),
                "action": getattr(r, "action", None) or getattr(r, "step", None),
                "risk": getattr(r, "risk_level", None),
                "status": str(getattr(r, "status", "")),
            }
            for r in reqs
        ]

    return ChatSkill(
        name="list_pending_approvals",
        description="List pending human-approval (HITL) requests awaiting a decision.",
        handler=handler,
        args={"goal_id": "optional goal id to filter by"},
        scope="governance:read",
    )


def build_resolve_approval_skill(hitl_gateway: Any) -> ChatSkill:
    async def handler(
        tenant_ctx: Any, request_id: str, decision: str, note: str = ""
    ) -> dict[str, Any]:
        d = decision.strip().lower()
        if d in ("approve", "approved", "yes"):
            ok = await hitl_gateway.approve(
                request_id, approver="chat", note=note, tenant_ctx=tenant_ctx
            )
        elif d in ("reject", "rejected", "no", "deny", "denied"):
            ok = await hitl_gateway.reject(
                request_id, approver="chat", note=note, tenant_ctx=tenant_ctx
            )
        else:
            raise ValueError(f"decision must be approve or reject, got {decision!r}")
        return {"request_id": request_id, "decision": d, "ok": bool(ok)}

    return ChatSkill(
        name="resolve_approval",
        description="Approve or reject a pending human-approval (HITL) request from chat.",
        handler=handler,
        args={
            "request_id": "the approval request id",
            "decision": "approve or reject",
            "note": "optional note",
        },
        scope="governance:write",
    )


def build_list_workflows_skill(workflow_service: Any) -> ChatSkill:
    async def handler(tenant_id: str) -> list[dict[str, Any]]:
        rows = await workflow_service.list(tenant_id)
        return [
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "status": r.get("status"),
                "description": r.get("description"),
            }
            for r in (rows or [])
        ]

    return ChatSkill(
        name="list_workflows",
        description="List the tenant's workflows (name, status).",
        handler=handler,
        args={},
        scope="workflows:read",
    )


def build_run_workflow_skill(workflow_runner: Any) -> ChatSkill:
    async def handler(
        tenant_id: str, workflow_id: str, inputs: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return await workflow_runner.trigger(
            workflow_id=workflow_id, tenant_id=tenant_id, inputs=inputs or {}
        )

    return ChatSkill(
        name="run_workflow",
        description="Run a workflow by id with optional inputs; returns the run record.",
        handler=handler,
        args={"workflow_id": "the workflow id to run", "inputs": "optional input mapping"},
        scope="workflows:write",
    )


def build_search_knowledge_skill(knowledge_store: Any) -> ChatSkill:
    async def handler(
        tenant_ctx: Any, query: str, collection_id: str, top_k: int = 5
    ) -> list[dict[str, Any]]:
        results = await knowledge_store.search(
            query, collection_id, top_k=top_k, tenant_ctx=tenant_ctx
        )
        return list(results or [])

    return ChatSkill(
        name="search_knowledge",
        description="Search a knowledge-base collection and return matching passages with sources.",
        handler=handler,
        args={
            "query": "what to search for",
            "collection_id": "the collection to search",
            "top_k": "max results (default 5)",
        },
        scope="knowledge:read",
    )


def build_ingest_knowledge_skill(knowledge_store: Any) -> ChatSkill:
    async def handler(
        tenant_ctx: Any,
        collection_id: str,
        content: str,
        source_url: str = "",
        source_type: str = "text",
    ) -> dict[str, Any]:
        doc_id = await knowledge_store.ingest_document(
            collection_id=collection_id,
            content=content,
            tenant_ctx=tenant_ctx,
            source_url=source_url,
            source_type=source_type,
        )
        return {"ingested": True, "collection_id": collection_id, "doc_id": str(doc_id)}

    return ChatSkill(
        name="ingest_knowledge",
        description="Ingest text into a knowledge-base collection so it's searchable later.",
        handler=handler,
        args={
            "collection_id": "the target collection",
            "content": "the text to ingest",
            "source_url": "optional origin URL",
            "source_type": "optional source type (default text)",
        },
        scope="knowledge:write",
    )


def build_set_conversation_model_skill(chat_service: Any) -> ChatSkill:
    async def handler(session_id: str, tenant_id: str, model: str) -> dict[str, Any]:
        updated = await chat_service.aupdate_session(
            session_id, tenant_id, preferred_model=model
        )
        return {
            "session_id": session_id,
            "preferred_model": getattr(updated, "preferred_model", model) if updated else None,
            "ok": updated is not None,
        }

    return ChatSkill(
        name="set_conversation_model",
        description="Switch the model for THIS conversation ('use the fast/cheap model', "
        "'answer with Opus'); persists on the session.",
        handler=handler,
        args={
            "session_id": "the conversation to change",
            "model": "the model id to use for this conversation",
        },
        scope="models:write",
    )


def register_builtin_skills(
    registry: SkillRegistry,
    *,
    services_api: Any | None = None,
    goal_service: Any | None = None,
    schedule_store: Any | None = None,
    artifact_store: Any | None = None,
    hitl_gateway: Any | None = None,
    workflow_service: Any | None = None,
    workflow_runner: Any | None = None,
    knowledge_store: Any | None = None,
    chat_service: Any | None = None,
) -> None:
    """Register the built-in skills whose backing services are available."""
    if services_api is not None:
        registry.register(build_list_connected_services_skill(services_api))
    if goal_service is not None:
        registry.register(build_submit_goal_skill(goal_service))
    if schedule_store is not None:
        registry.register(build_list_schedules_skill(schedule_store))
    if artifact_store is not None:
        registry.register(build_generate_document_skill(artifact_store))
    if hitl_gateway is not None:
        registry.register(build_list_pending_approvals_skill(hitl_gateway))
        registry.register(build_resolve_approval_skill(hitl_gateway))
    if workflow_service is not None:
        registry.register(build_list_workflows_skill(workflow_service))
    if workflow_runner is not None:
        registry.register(build_run_workflow_skill(workflow_runner))
    if knowledge_store is not None:
        registry.register(build_search_knowledge_skill(knowledge_store))
        registry.register(build_ingest_knowledge_skill(knowledge_store))
    if chat_service is not None:
        registry.register(build_set_conversation_model_skill(chat_service))


def build_registry_from_app_state(app_state: Any) -> SkillRegistry:
    """Assemble a SkillRegistry from whatever services are wired on app.state.

    Unwraps a Starlette app to its .state, then registers each built-in skill
    whose backing service is present. Missing services are simply skipped.
    """
    aps: Any = app_state
    try:
        from starlette.applications import Starlette

        if isinstance(aps, Starlette):
            aps = aps.state
    except Exception:
        pass
    registry = SkillRegistry()
    register_builtin_skills(
        registry,
        services_api=getattr(aps, "services_api", None),
        goal_service=getattr(aps, "goal_service", None),
        schedule_store=getattr(aps, "schedule_store", None),
        artifact_store=getattr(aps, "chat_artifact_store", None),
        hitl_gateway=getattr(aps, "hitl_gateway", None),
        workflow_service=getattr(aps, "workflow_service", None),
        workflow_runner=getattr(aps, "workflow_runner", None),
        knowledge_store=getattr(aps, "knowledge_store", None),
        chat_service=getattr(aps, "chat_service", None),
    )
    return registry
