"""Goals API router — submit and track autonomous agent goals."""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from app.agent.debate import MAX_DEBATE_ROUNDS
from app.core.errors import NotFoundError
from app.observability.logging import get_logger as _get_logger
from app.orchestration.strategy_contracts import PatternLimits
from app.tenancy.context import TenantContext

_logger = _get_logger(__name__)


def _not_found_response(request: Request, exc: Exception) -> HTTPException:
    """Return a sanitized 404 with correlation_id — avoids leaking internal IDs."""
    correlation_id = getattr(getattr(request, "state", None), "correlation_id", None)
    if not correlation_id:
        correlation_id = str(uuid.uuid4())[:8]
        # Persist so later error helpers in the same request (e.g. a second
        # 404 during retry logic) correlate to the same id, not a new one.
        request.state.correlation_id = correlation_id
    _logger.info("goal_not_found", correlation_id=correlation_id, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Resource not found [{correlation_id}]",
    )


router = APIRouter(prefix="/goals", tags=["goals"])


class PersistenceConfigRequest(BaseModel):
    max_attempts: int = Field(default=10, ge=1, le=100)
    iterations_per_attempt: int = Field(default=15, ge=1, le=50)
    base_backoff_seconds: float = Field(default=30.0, ge=1.0, le=3600.0)
    max_backoff_seconds: float = Field(default=600.0, ge=10.0, le=86400.0)
    strategy_switch_after: int = Field(default=2, ge=1, le=10)
    escalate_after_failures: int = Field(default=6, ge=1, le=50)
    total_timeout_seconds: float = Field(default=0.0, ge=0.0)
    decompose_on_failure: bool = True


class GoalRequest(BaseModel):
    goal: str = Field(..., min_length=1, max_length=10_000)
    priority: str = "normal"
    dry_run: bool = False
    agent_id: str | None = None
    workflow_mode: str = "single_agent"
    # Debate mode: rounds (1 = propose + vote, 2 = + critiques). Bounded by what
    # DebateOrchestrator runs (it used to clamp 4-10 to 3 silently).
    debate_rounds: int = Field(default=2, ge=1, le=MAX_DEBATE_ROUNDS)
    # Persistence mode: keep trying until goal is achieved
    persistence_mode: bool = False
    persistence_config: PersistenceConfigRequest = Field(default_factory=PersistenceConfigRequest)
    # Multi-agent modes
    agent_ids: list[str] = Field(default_factory=list)
    supervisor_max_parallel: int = Field(default=5, ge=1, le=20)
    # Multi-modal inputs (Phase 13.3)
    image_url: str | None = Field(None, description="URL of an image to include in goal context")
    attachment_base64: str | None = Field(None, description="Base64-encoded file (PDF/image)")
    attachment_mime: str | None = Field(None, description="MIME type of attachment")
    # Structured multimodal attachments list (Phase 1 gap fix)
    attachments: list[dict[str, str]] | None = Field(
        None,
        description='[{"type": "image_url", "url": "..."}, {"type": "pdf_base64", "data": "..."}]',
    )
    # Model override — takes priority over tenant default (Gap 1)
    model_override: str | None = Field(
        None, description="Override the tenant's default model for this goal"
    )
    strategy_override: str | None = Field(None, min_length=1, max_length=100)
    auxiliary_strategies: list[str] = Field(default_factory=list, max_length=20)
    pattern_limits: PatternLimits | None = None


def _build_multimodal_goal_text(
    goal: str,
    image_url: str | None,
    attachment_base64: str | None,
    attachment_mime: str | None,
) -> str:
    """Wrap goal with image/attachment context for vision providers."""
    parts = [goal]
    if image_url:
        parts.append(f"\n[IMAGE] {image_url}")
    if attachment_base64 and attachment_mime:
        parts.append(f"\n[ATTACHMENT mime={attachment_mime}] (base64 content provided)")
    return "\n".join(parts)


# D-23: attachment "type" -> the MultimodalPipeline modality it maps to.
# URL-based attachments (e.g. "image_url") are deliberately excluded -- only
# base64-carrying attachments are processed here, to avoid a server-side
# fetch of an arbitrary caller-supplied URL.
_ATTACHMENT_TYPE_MODALITY: dict[str, str] = {
    "image_base64": "image",
    "image": "image",
    "pdf_base64": "pdf",
    "pdf": "pdf",
    "audio_base64": "audio",
    "audio": "audio",
    "video_base64": "video",
    "video": "video",
    "text": "text",
    "code": "code",
    "table": "table",
    "csv": "table",
}


async def _extract_multimodal_context(
    attachments: list[dict[str, str]],
    pipeline: Any,
    tenant_id: str,
) -> str:
    """Run goal attachments through MultimodalPipeline so their extracted
    content reaches the planner (D-23) instead of `attachments` being inert
    metadata that nothing downstream ever reads.

    Best-effort per attachment: one bad/unsupported/oversized attachment must
    never block goal submission.
    """
    parts: list[str] = []
    for attachment in attachments:
        modality = _ATTACHMENT_TYPE_MODALITY.get(attachment.get("type", ""))
        if modality is None:
            continue
        data = attachment.get("data", "")
        if modality != "text" and not data:
            continue
        try:
            if modality == "image":
                job = await pipeline.ingest_image(data, tenant_id)
            elif modality == "pdf":
                job = await pipeline.ingest_pdf(data, tenant_id)
            elif modality == "audio":
                job = await pipeline.ingest_audio(data, tenant_id)
            elif modality == "video":
                job = await pipeline.ingest_video(data, tenant_id)
            elif modality == "code":
                job = await pipeline.ingest_code(data, tenant_id, attachment.get("language"))
            elif modality == "table":
                job = await pipeline.ingest_table(data, tenant_id)
            else:  # text
                job = await pipeline.ingest_text(data or attachment.get("content", ""), tenant_id)
        except Exception as exc:
            _logger.warning("multimodal_attachment_ingest_failed", type=modality, error=str(exc))
            continue
        for span in job.spans:
            if span.content:
                parts.append(f"[{modality} attachment] {span.content}")
    return "\n\n".join(parts)


class ApproveRequest(BaseModel):
    request_id: str
    action: str  # "approve" | "reject"
    approver: str
    note: str = ""


def _goal_service(request: Request) -> Any:
    # Re-export from _deps for backward compatibility with existing usages in this file.
    # New code should use: Depends(get_goal_service) from app.api._deps
    from app.api._deps import get_goal_service

    return get_goal_service(request)


def _require_tenant(request: Request) -> TenantContext:
    ctx: TenantContext | None = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


_IDEMPOTENCY_REPLAY_TTL_SECONDS = 3600
# How often a running submission extends its pending Idempotency-Key claim.
_IDEMPOTENCY_HEARTBEAT_SECONDS = 30.0


def _idempotency_body_hash(body: Any) -> str:
    import hashlib
    import json as _json

    payload = _json.dumps(body.model_dump(mode="json"), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


async def _idempotency_heartbeat(
    store: Any, key: str, tenant_id: str, owner: str, body_hash: str
) -> None:
    """Keep this submission's pending claim alive while it runs: a submission
    slower than the pending TTL let a retry claim the key and create a second
    goal."""
    from app.reliability import idempotency as _idem_mod

    while True:
        await asyncio.sleep(_IDEMPOTENCY_HEARTBEAT_SECONDS)
        try:
            await store.extend(
                key,
                tenant_id,
                owner=owner,
                body_hash=body_hash,
                ttl_seconds=_idem_mod._PENDING_TTL_SECONDS,
            )
        except Exception as exc:
            _logger.warning("idempotency_heartbeat_failed", error=str(exc)[:200])


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def submit_goal(request: Request, body: GoalRequest) -> dict[str, Any]:
    tenant = _require_tenant(request)
    svc = _goal_service(request)

    # Idempotency — prevent duplicate goal submissions.
    #
    # The key used to be claimed before validation and never released (so a
    # 422/429 made every retry a 409 with no goal_id for an hour) and store
    # errors were swallowed (fail open). Now: claim → run → on success record the
    # response (a replay returns the same goal_id) / on any failure release the
    # claim (the client may retry). A store error fails closed with 503.
    idempotency_key = request.headers.get("Idempotency-Key")
    _idem = getattr(request.app.state, "idempotency_store", None)
    if not idempotency_key:
        return await _submit_goal_unguarded(request, body, tenant, svc)
    if _idem is None:
        # The header used to be ignored here, running the submission with no
        # duplicate protection at all while the client believed it had some.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "idempotency_unavailable",
                "message": "Idempotency-Key is not available on this deployment; retry later.",
            },
        )

    import secrets as _secrets

    owner = _secrets.token_hex(16)
    body_hash = _idempotency_body_hash(body)
    try:
        existing = await _idem.claim(
            idempotency_key, tenant.tenant_id, owner=owner, body_hash=body_hash
        )
    except Exception as exc:
        _logger.warning("idempotency_claim_failed", error=str(exc)[:200])
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "idempotency_unavailable",
                "message": "Idempotency-Key could not be checked; retry the request.",
            },
        ) from exc
    if existing is not None:
        if existing.get("body") and existing.get("body") != body_hash:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "idempotency_key_reused",
                    "idempotency_key": idempotency_key,
                    "message": "This Idempotency-Key was used for a different request.",
                },
            )
        response = existing.get("response") if existing.get("state") == "done" else None
        if isinstance(response, dict):
            return {**response, "idempotent_replay": True}
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "duplicate_request",
                "idempotency_key": idempotency_key,
                "message": "A request with this Idempotency-Key is still being processed.",
            },
        )

    heartbeat = asyncio.create_task(
        _idempotency_heartbeat(_idem, idempotency_key, tenant.tenant_id, owner, body_hash)
    )
    try:
        result = await _submit_goal_unguarded(request, body, tenant, svc)
    except BaseException:
        heartbeat.cancel()
        await _release_idempotency_claim(
            _idem, idempotency_key, tenant.tenant_id, owner=owner, body_hash=body_hash
        )
        raise
    heartbeat.cancel()
    if not result.get("goal_id"):
        # Nothing was created (e.g. needs_agent_selection): the same key may be
        # used again for the real submission.
        await _release_idempotency_claim(
            _idem, idempotency_key, tenant.tenant_id, owner=owner, body_hash=body_hash
        )
        return result
    try:
        recorded = await _idem.complete(
            idempotency_key,
            tenant.tenant_id,
            result,
            ttl_seconds=_IDEMPOTENCY_REPLAY_TTL_SECONDS,
            owner=owner,
            body_hash=body_hash,
        )
        if recorded is False:
            _logger.error(
                "idempotency_claim_lost",
                goal_id=result.get("goal_id"),
                message="another request re-claimed the key while this one ran",
            )
    except Exception as exc:
        # The goal exists; a replay within the pending window answers 409, after
        # it a retry would create a second goal. Loud, never silent.
        _logger.error(
            "idempotency_complete_failed",
            goal_id=result.get("goal_id"),
            error=str(exc)[:200],
        )
    return result


async def _release_idempotency_claim(
    store: Any, key: str, tenant_id: str, *, owner: str, body_hash: str
) -> None:
    try:
        await store.release(key, tenant_id, owner=owner, body_hash=body_hash)
    except Exception as exc:
        # The pending claim expires on its own (short TTL); log so it is visible.
        _logger.warning("idempotency_release_failed", error=str(exc)[:200])


def _invalid_strategy_detail(registry: Any, requested: str) -> dict[str, Any]:
    """422 detail: why the strategy selection was refused and what CAN run as a goal.

    The requested id is echoed only when it is a registered strategy (no
    reflection of arbitrary client input)."""
    valid: list[str] = []
    known = False
    if registry is not None:
        from app.orchestration.execution_drivers import goal_execution_driver

        # Building the detail must never turn the 422 into a 500: a registry that
        # can't enumerate or look up (partial fakes, store outage) just yields a
        # detail without the list.
        try:
            valid = sorted(
                c.strategy_id for c in registry.list_all() if goal_execution_driver(c) is not None
            )
        except Exception:
            valid = []
        try:
            known = registry.get(requested) is not None
        except Exception:
            known = False
    if known:
        message = (
            f"strategy '{requested}' is registered but has no goal execution driver, "
            "so it cannot run as a goal"
        )
    else:
        message = "the requested strategy (or an auxiliary strategy) is not a registered strategy"
    return {
        "code": "INVALID_STRATEGY",
        "message": message,
        "requested_strategy": requested if known else None,
        "valid_strategies": valid,
    }


async def _submit_goal_unguarded(
    request: Request, body: GoalRequest, tenant: TenantContext, svc: Any
) -> dict[str, Any]:
    # Build execution_context with persistence settings when enabled
    exec_ctx: dict[str, Any] = {}
    if body.strategy_override is not None or body.auxiliary_strategies or body.pattern_limits:
        registry = getattr(request.app.state, "strategy_registry", None)
        try:
            primary = body.strategy_override or "react"
            if registry is not None:
                from app.orchestration.execution_drivers import goal_execution_driver

                primary_resolution = registry.resolve(primary)
                if primary_resolution.capability.adapter_descriptor is None:
                    raise LookupError(primary)
                # Registered but nothing can run it as a goal (ReWOO, CodeAct, …): refuse
                # rather than accept a strategy the goal would silently not run.
                if goal_execution_driver(primary_resolution.capability) is None:
                    raise LookupError(primary)
                for auxiliary in body.auxiliary_strategies:
                    registry.resolve(auxiliary)
            exec_ctx["strategy_runtime"] = {
                "primary_strategy": primary,
                "auxiliary_strategies": body.auxiliary_strategies,
                "limits": (
                    body.pattern_limits.model_dump(mode="json")
                    if body.pattern_limits is not None
                    else None
                ),
            }
        except (LookupError, ValueError) as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                _invalid_strategy_detail(registry, body.strategy_override or "react"),
            ) from exc
    if body.persistence_mode:
        exec_ctx["persistence_mode"] = True
        exec_ctx["persistence_config"] = body.persistence_config.model_dump()

    # Gap 2: Multimodal attachments → propagate to execution context
    if body.attachments:
        exec_ctx["attachments"] = body.attachments
        # D-23: also run attachments through MultimodalPipeline so their
        # extracted content reaches the planner via agent_state.context
        # (see app.agent.nodes.planner_mixin._node_plan), not just inert
        # metadata that nothing downstream reads.
        try:
            pipeline = getattr(request.app.state, "multimodal_pipeline", None)
            if pipeline is not None:
                multimodal_context = await _extract_multimodal_context(
                    body.attachments, pipeline, tenant.tenant_id
                )
                if multimodal_context:
                    exec_ctx["multimodal_context"] = multimodal_context
        except Exception as exc:
            _logger.warning("multimodal_attachment_extraction_failed", error=str(exc))
    if body.image_url:
        exec_ctx["image_url"] = body.image_url

    # Gap 1: Model override → execution context (provider picks it up)
    if body.model_override:
        exec_ctx["model_override"] = body.model_override

    # Debate / supervisor goals make many LLM calls once they run: refuse a
    # tenant whose budget is already exhausted now (429), before a goal exists.
    if body.workflow_mode in ("debate", "supervisor"):
        preflight = getattr(svc, "_check_budget_preflight", None)
        if preflight is not None:
            pending = preflight(tenant)
            if inspect.isawaitable(pending):
                await pending

    # ── Debate mode: a goal whose own graph runs the debate ───────────────────
    # It used to run up to 3 rounds of N-agent proposal/critique/vote LLM calls
    # HERE, inside the request, before the goal existed — bounded by proxy
    # timeouts, holding API capacity, and its paid result lost on a disconnect or
    # restart (CORE-30). Now the goal is submitted like any other
    # (workflow_mode="debate" compiles the in-graph debate node, which runs on
    # the worker with the goal's own charged, BYOK-resolved planner and emits
    # debate_* goal events); its id comes back at once.
    if body.workflow_mode == "debate":
        exec_ctx["debate_rounds"] = body.debate_rounds

    # ── Supervisor mode: a parent goal whose own graph runs the fan-out ───────
    # It used to run SupervisorAgent inside this request — awaiting every
    # sub-goal (up to 300 s each) — create no parent goal and answer goal_id "".
    # Now the parent is submitted like any goal (workflow_mode="supervisor"
    # compiles the in-graph supervisor node, which decomposes the goal with the
    # goal's own charged, BYOK-resolved planner, dispatches the sub-goals — on a
    # worker, to the dedicated sub-goal pool (CORE-09) — and synthesizes their
    # results); its id comes back at once and the goal page follows it.
    if body.workflow_mode == "supervisor":
        exec_ctx["supervisor_max_parallel"] = body.supervisor_max_parallel

    # ── Multi-agent mode: same goal dispatched to N agents in parallel ────────
    if body.workflow_mode == "multi_agent" and body.agent_ids:
        goal_svc = _goal_service(request)
        tasks = [
            goal_svc.submit_goal(
                goal=body.goal,
                tenant_ctx=tenant,
                agent_id=agent_id,
                priority=body.priority,
                dry_run=body.dry_run,
            )
            for agent_id in body.agent_ids[:5]  # cap at 5
        ]
        dispatched_ids = body.agent_ids[:5]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid = [r for r in results if isinstance(r, dict) and "goal_id" in r]
        # Failed submissions used to be dropped silently; surface each one.
        def _failure(aid: str, r: Any) -> dict[str, Any]:
            if isinstance(r, HTTPException):
                # A deliberate client-facing refusal (429, 404, ...): as is.
                return {"agent_id": aid, "error": str(r.detail), "status_code": r.status_code}
            if isinstance(r, BaseException):
                # CORE-35: internal exception text (SQL, DSN fragments) never
                # reaches the client — class + a correlation id for the logs.
                cid = uuid.uuid4().hex
                _logger.warning(
                    "multi_agent_submission_failed",
                    correlation_id=cid,
                    agent_id=aid,
                    error_type=type(r).__name__,
                    error=str(r)[:500],
                )
                return {
                    "agent_id": aid,
                    "error": "submission failed",
                    "error_type": type(r).__name__,
                    "correlation_id": cid,
                }
            return {"agent_id": aid, "error": "submission returned no goal_id"}

        failed_submissions = [
            _failure(aid, r)
            for aid, r in zip(dispatched_ids, results, strict=True)
            if not (isinstance(r, dict) and "goal_id" in r)
        ]
        if not valid:
            first = next((r for r in results if isinstance(r, HTTPException)), None)
            raise HTTPException(
                first.status_code if first is not None else status.HTTP_502_BAD_GATEWAY,
                {"message": "No agent accepted the goal", "failed_submissions": failed_submissions},
            )
        return {
            "id": valid[0]["goal_id"],
            "goal_id": valid[0]["goal_id"],
            "status": "multi_agent",
            "mode": "multi_agent",
            "success": not failed_submissions,
            "sub_goal_ids": [r["goal_id"] for r in valid],
            "failed_submissions": failed_submissions,
            "goal": body.goal,
        }

    # ── Auto-routing: call AgentRouter when agent_id is not specified ─────────
    agent_id = body.agent_id
    if not agent_id and tenant.agent_key is not None:
        # An agent-scoped key runs its own agent; never auto-route it elsewhere.
        agent_id = tenant.agent_key.agent_id
    if not agent_id:
        agent_router = getattr(request.app.state, "agent_router", None)
        if agent_router is not None:
            agent_store = getattr(request.app.state, "agent_store", None)
            if agent_store is not None:
                try:
                    # The router fetches its own bounded, relevance-ranked
                    # candidates (CORE-33) — never the tenant's whole table.
                    decision = await agent_router.route(goal=body.goal, tenant_ctx=tenant)
                    agent_id = decision.agent_id
                    # Inject routing decision into execution context
                    exec_ctx["routing_decision"] = decision.to_dict()
                    # For needs_human_choice mode, return immediately with the decision
                    if decision.mode == "needs_human_choice":
                        return {
                            "status": "needs_agent_selection",
                            "routing": decision.to_dict(),
                            "message": (
                                "Multiple agents could handle this goal. Please select one."
                            ),
                        }
                except Exception as _re:
                    import logging

                    logging.getLogger(__name__).warning("agent_router_failed: %s", _re)

    result: dict[str, Any] = await svc.submit_goal(
        goal=body.goal,
        priority=body.priority,
        dry_run=body.dry_run,
        tenant_ctx=tenant,
        agent_id=agent_id,
        workflow_mode=body.workflow_mode,
        execution_context=exec_ctx,
    )
    if body.workflow_mode in ("supervisor", "debate"):
        result["mode"] = body.workflow_mode
    # Gap 5: Dry-run vs simulation — surface execution mode in response
    result["execution_mode"] = "preview" if body.dry_run else "live"
    result["execution_mode_description"] = (
        "Plan generated but no tools executed" if body.dry_run else "Fully autonomous execution"
    )
    return result


@router.get("")
async def list_goals(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, list[dict[str, Any]]]:
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    result: dict[str, list[dict[str, Any]]] = await svc.list_goals(
        tenant_ctx=tenant, limit=limit, offset=offset
    )
    return result


@router.get("/metrics")
async def get_goal_metrics(request: Request) -> dict[str, Any]:
    """Return aggregated metrics for the authenticated tenant's goals."""
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    result: dict[str, Any] = await svc.get_metrics(tenant_ctx=tenant)
    return result


@router.get("/cost-metrics")
async def get_cost_metrics(request: Request) -> dict[str, Any]:
    """Return cost metrics for dashboard chart."""
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    metrics = await svc.get_metrics(tenant_ctx=tenant)
    # Get budget config
    from app.governance.cost import BudgetConfig

    # The budget the cost controllers enforce (budget_configs), not a per-replica dict.
    budget_cfg: BudgetConfig = getattr(request.app.state, "_budget_config", {}).get(
        tenant.tenant_id, BudgetConfig()
    )
    for _cc_name in ("redis_cost_controller", "cost_controller"):
        _cc = getattr(request.app.state, _cc_name, None)
        if _cc is not None and hasattr(_cc, "resolve_config"):
            try:
                budget_cfg = await _cc.resolve_config(tenant.tenant_id)
            except Exception as _bexc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Budget store unavailable",
                ) from _bexc
            break
    return {
        **metrics,
        "daily_budget_usd": budget_cfg.per_tenant_daily_usd,
        "per_goal_budget_usd": budget_cfg.per_goal_usd,
        "budget_utilization": (
            metrics["cost_today_usd"] / budget_cfg.per_tenant_daily_usd
            if budget_cfg.per_tenant_daily_usd > 0
            else 0.0
        ),
    }


@router.get("/route")
async def preview_routing(
    request: Request,
    goal: str = Query(..., description="Goal text to route"),
) -> dict:
    """Preview routing decision for a goal without executing it."""
    tenant = _require_tenant(request)
    agent_router = getattr(request.app.state, "agent_router", None)
    if agent_router is None:
        return {"mode": "no_router", "reason": "Agent router not configured"}
    # Same bounded candidate pre-filter as submission (CORE-33).
    decision = await agent_router.route(goal=goal, tenant_ctx=tenant)
    return decision.to_dict()


@router.get("/{goal_id}")
async def get_goal(request: Request, goal_id: str) -> dict[str, Any]:
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    try:
        result: dict[str, Any] = await svc.get_goal(goal_id=goal_id, tenant_ctx=tenant)
    except NotFoundError as exc:
        raise _not_found_response(request, exc) from exc
    return result


@router.get("/{goal_id}/explain")
async def explain_goal(request: Request, goal_id: str) -> dict[str, Any]:
    tenant = _require_tenant(request)
    try:
        goal = await _goal_service(request).get_goal(goal_id=goal_id, tenant_ctx=tenant)
    except NotFoundError as exc:
        raise _not_found_response(request, exc) from exc
    context = goal.get("execution_context") or {}
    if not isinstance(context, dict):
        context = {}
    profile = context.get("runtime_profile") or {}
    if not isinstance(profile, dict):
        profile = {}
    # One handler for GET /goals/{id}/explain. A second, later registration of
    # the same path (the decision-trace view GoalExplainPanel reads) was
    # unreachable — FastAPI serves the first match — so the panel never got
    # decision_traces / model_selections / rag_citations / plan.
    return {
        "goal_id": goal_id,
        "status": goal.get("status"),
        "profile_version": profile.get("profile_version"),
        "selected_strategy": profile.get("primary_strategy"),
        "auxiliary_strategies": profile.get("auxiliary_strategies", []),
        "rejected_strategies": profile.get("rejected_alternatives", []),
        "readiness": profile.get("readiness_snapshot_ref"),
        "limits": profile.get("effective_limits", {}),
        "safe_trace": context.get("safe_trace", {}),
        "cost_usd": context.get("cost_usd", 0.0),
        "decision_traces": context.get("decision_traces", []),
        "model_selections": context.get("model_selections", {}),
        "rag_citations": context.get("rag_citations", []),
        "tool_reasoning": context.get("tool_reasoning", []),
        "plan": goal.get("plan", []),
    }


@router.get("/{goal_id}/pattern-selection")
async def get_goal_pattern_selection(request: Request, goal_id: str) -> dict[str, Any]:
    """Return the agent pattern this goal was routed to, and why.

    Surfaces the ONE selector's decision for the frontend selection UX: the
    auto-selected (or explicitly overridden) primary pattern, the reasoning /
    multi-agent topology, plain-language rationale, and the registry-driven catalog
    of patterns available to override with. Falls back to computing the summary
    on demand for goals persisted before the record existed.
    """
    tenant = _require_tenant(request)
    try:
        return await _goal_service(request).get_pattern_selection(
            goal_id=goal_id, tenant_ctx=tenant
        )
    except NotFoundError as exc:
        raise _not_found_response(request, exc) from exc


@router.post("/{goal_id}/cancel")
async def cancel_goal(
    request: Request,
    goal_id: str,
    rollback: bool = Query(
        False,
        description=(
            "Also undo the goal's executed side effects (created issues, sent "
            "messages, ...) through their registered inverses. Off by default: a "
            "cancel keeps what was done. The request is audited."
        ),
    ),
) -> dict[str, Any]:
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    result: dict[str, Any] = await svc.cancel_goal(
        goal_id=goal_id, tenant_ctx=tenant, rollback=rollback
    )
    if rollback:
        result = {**result, "rollback_requested": True}
    return result


@router.get(
    "/{goal_id}/stream",
    responses={
        200: {
            "description": "Server-Sent Events stream of goal execution lifecycle events.",
            "content": {
                "text/event-stream": {
                    "schema": {
                        "type": "string",
                        "example": (
                            'data: {"type": "goal_started", "goal": "..."}\n\n'
                            'data: {"type": "plan_ready", "steps": ["..."]}\n\n'
                            'data: {"type": "step_started", "step": "..."}\n\n'
                            'data: {"type": "step_complete", "step": "...", "output": "..."}\n\n'
                            'data: {"type": "verification_done", "success": true}\n\n'
                            'data: {"type": "goal_complete"}\n\n'
                        ),
                    }
                }
            },
        },
        401: {"description": "Missing or invalid API key."},
        404: {"description": "Goal not found."},
    },
)
async def stream_goal(request: Request, goal_id: str) -> StreamingResponse:
    """Stream goal execution events as Server-Sent Events (SSE).

    Returns a continuous `text/event-stream` of JSON events for the given goal.
    Each event is a JSON object on a `data:` line followed by two newlines.

    **Event types emitted:**
    - `goal_started` — goal accepted, execution beginning
    - `plan_ready` — planner produced a step list
    - `step_started` — a single step is being executed
    - `step_complete` — step finished with output
    - `waiting_approval` — supervised mode: HITL approval required
    - `approval_granted` — HITL approved, execution resuming
    - `sub_goals_complete` — goal-tree sub-agents finished
    - `verification_done` — verifier evaluated the run
    - `goal_complete` — goal reached `complete` status
    - `goal_failed` — goal reached `failed` status
    - `goal_cancelled` — goal was cancelled via the cancel endpoint
    - `replanning` — verifier returned false; planner will retry

    Connect with `EventSource` (browser) or `httpx` streaming (server-side).
    The stream ends when the goal reaches a terminal status
    (`complete`, `failed`, or `cancelled`).
    """
    tenant_ctx = _require_tenant(request)
    svc = _goal_service(request)

    # Validate the goal exists and belongs to the calling tenant *before* opening
    # the stream.  If we deferred this check to the async generator, the 200
    # response header would already be sent when the error was discovered.
    try:
        record = await svc.get_goal(goal_id=goal_id, tenant_ctx=tenant_ctx)
    except NotFoundError as exc:
        raise _not_found_response(request, exc) from exc

    if record.get("tenant_id") and record["tenant_id"] != tenant_ctx.tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    last_event_id = request.headers.get("Last-Event-ID", "0")
    since_sequence = int(last_event_id) if last_event_id.isdigit() else 0

    async def event_generator() -> AsyncGenerator[str, None]:
        from app.core.errors import ServiceUnavailableError
        from app.services.goal_service import SSE_HEARTBEAT_TYPE

        try:
            async for event in svc.subscribe_events(
                goal_id=goal_id,
                tenant_ctx=tenant_ctx,
                since_sequence=since_sequence,
            ):
                # Stop on a client that went away (heartbeats make sure this is
                # checked even while the goal is idle, e.g. waiting for approval).
                if await request.is_disconnected():
                    break
                if event.get("type") == SSE_HEARTBEAT_TYPE:
                    yield ": ping\n\n"
                    continue
                # SVC-05: the id is the event's durable sequence only. A
                # per-connection counter fallback made Last-Event-ID drift on
                # every reconnect; an event without one (not stored) gets no id,
                # so the client keeps resuming from the last real sequence.
                event_seq = event.get("_seq")
                has_seq = isinstance(event_seq, int) and not isinstance(event_seq, bool)
                id_line = f"id: {event_seq}\n" if has_seq else ""
                yield f"{id_line}data: {json.dumps(event)}\n\n"
        except ServiceUnavailableError as exc:
            # The headers are already sent: report a retryable stream error the
            # client can reconnect on, instead of an empty / silently ended stream.
            payload = {
                "type": "stream_error",
                "code": exc.code,
                "retryable": True,
                "message": "Goal event history is temporarily unavailable; reconnecting.",
            }
            yield f"retry: 5000\nevent: error\ndata: {json.dumps(payload)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering for SSE
            "Connection": "keep-alive",
        },
    )


@router.get("/{goal_id}/audit")
async def get_audit_log(request: Request, goal_id: str) -> list[dict[str, Any]]:
    """Return audit log entries for this goal."""
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    try:
        entries: list[dict[str, Any]] = await svc.get_audit_entries(
            goal_id=goal_id, tenant_ctx=tenant
        )
    except NotFoundError as exc:
        raise _not_found_response(request, exc) from exc
    return entries


@router.get("/{goal_id}/eval")
async def get_goal_eval(request: Request, goal_id: str) -> dict[str, Any]:
    """Return the eval scorecard for a completed goal, or a not-evaluated response."""
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    try:
        result: dict[str, Any] = await svc.get_eval(goal_id=goal_id, tenant_ctx=tenant)
    except NotFoundError as exc:
        raise _not_found_response(request, exc) from exc
    return result


@router.get("/{goal_id}/eval/suggestions")
async def get_goal_eval_suggestions(request: Request, goal_id: str) -> dict[str, Any]:
    """Auto-suggested improvement actions derived from the goal's real eval scores.

    Each dimension below the config-driven pass threshold yields one actionable
    suggestion (worst first). Honest empty when unevaluated or all pass.
    """
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    try:
        return await svc.get_eval_suggestions(goal_id=goal_id, tenant_ctx=tenant)
    except NotFoundError as exc:
        raise _not_found_response(request, exc) from exc


@router.post("/{goal_id}/eval", status_code=status.HTTP_200_OK)
async def trigger_goal_eval(request: Request, goal_id: str) -> dict[str, Any]:
    """Trigger on-demand evaluation for a completed goal.

    Runs EvalRunner with LLM-based accuracy and coherence scoring,
    caches the result, and returns the full 7-dimension scorecard.
    """
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    try:
        result: dict[str, Any] = await svc.run_eval(goal_id=goal_id, tenant_ctx=tenant)
    except NotFoundError as exc:
        raise _not_found_response(request, exc) from exc
    return result


@router.post("/{goal_id}/approve")
async def approve_goal(request: Request, goal_id: str, body: ApproveRequest) -> dict[str, Any]:
    """Approve or reject a pending HITL request for this goal."""
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    try:
        result: dict[str, Any] = await svc.handle_approval(
            goal_id=goal_id,
            request_id=body.request_id,
            action=body.action,
            approver=body.approver,
            note=body.note,
            tenant_ctx=tenant,
        )
    except NotFoundError as exc:
        raise _not_found_response(request, exc) from exc
    return result


@router.post("/{goal_id}/pause")
async def pause_goal(request: Request, goal_id: str) -> dict[str, Any]:
    """Pause a running goal. Can be resumed with POST /goals/{id}/resume."""
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    try:
        result: dict[str, Any] = await svc.pause_goal(goal_id=goal_id, tenant_ctx=tenant)
    except NotFoundError as exc:
        raise _not_found_response(request, exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return result


@router.post("/{goal_id}/resume")
async def resume_goal(request: Request, goal_id: str) -> dict[str, Any]:
    """Resume a paused goal."""
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    try:
        result: dict[str, Any] = await svc.resume_goal(goal_id=goal_id, tenant_ctx=tenant)
    except NotFoundError as exc:
        raise _not_found_response(request, exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return result


class GhostRunStrategyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    workflow_mode: str = "single_agent"
    priority: str = "normal"
    agent_id: str | None = None
    model_override: str | None = None


class GhostRunRequest(BaseModel):
    goal: str = Field(..., min_length=1, max_length=10_000)
    strategies: list[GhostRunStrategyRequest] = Field(default_factory=list)


_DEFAULT_GHOST_STRATEGIES: list[dict[str, Any]] = [
    {"name": "Standard", "workflow_mode": "single_agent", "priority": "normal"},
    {"name": "Multi-Agent", "workflow_mode": "multi_agent", "priority": "normal"},
    {"name": "High-Priority", "workflow_mode": "single_agent", "priority": "high"},
]


@router.post("/ghost-run", status_code=status.HTTP_202_ACCEPTED)
async def ghost_run(request: Request, body: GhostRunRequest) -> dict[str, Any]:
    """Submit the same goal with multiple strategies in parallel for A/B comparison.

    Each strategy gets a unique goal_id so callers can track them individually via
    ``GET /goals/{id}`` or ``GET /goals/{id}/stream``.

    Failed strategies are reported inline and the others still execute (202 with
    a ``ghost_run_id``); when every strategy fails to submit the answer is 503.
    """
    tenant = _require_tenant(request)
    svc = _goal_service(request)

    strategies = body.strategies
    if not strategies:
        strategies = [GhostRunStrategyRequest(**s) for s in _DEFAULT_GHOST_STRATEGIES]

    ghost_run_id = uuid.uuid4().hex

    async def _submit_one(strategy: GhostRunStrategyRequest) -> dict[str, Any]:
        try:
            result = await svc.submit_goal(
                goal=body.goal,
                priority=strategy.priority,
                dry_run=False,
                tenant_ctx=tenant,
                agent_id=strategy.agent_id,
                workflow_mode=strategy.workflow_mode,
                execution_context={
                    "ghost_run_id": ghost_run_id,
                    "strategy_name": strategy.name,
                },
            )
            return {
                "name": strategy.name,
                "goal_id": result.get("goal_id") or result.get("id") or "",
                "error": None,
                "status": "queued",
            }
        except Exception as exc:
            return {
                "name": strategy.name,
                "goal_id": None,
                "error": _public_error(exc),
                "status": "failed",
            }

    strategy_results: list[dict[str, Any]] = list(
        await asyncio.gather(*[_submit_one(s) for s in strategies])
    )

    goal_ids: dict[str, str] = {
        r["name"]: r["goal_id"] for r in strategy_results if r.get("goal_id")
    }
    if not goal_ids:
        # Every strategy failed: nothing is running, so do not answer 202.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"message": "No ghost-run strategy could be submitted",
                    "strategies": strategy_results},
        )

    return {
        "ghost_run_id": ghost_run_id,
        "goal_ids": goal_ids,
        "strategies": strategy_results,
    }


def _public_error(exc: Exception) -> str:
    """Client-safe error text: an HTTPException's detail, else the error type
    (``str(exc)`` leaked internals such as DSNs and SQL)."""
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    return type(exc).__name__


class BatchGoalRequest(BaseModel):
    goals: list[str] = Field(..., min_length=1, max_length=100)
    priority: str = "normal"
    agent_id: str | None = None
    max_parallel: int = Field(default=10, ge=1, le=50)


@router.post("/batch", status_code=status.HTTP_202_ACCEPTED)
async def submit_batch_goals(request: Request, body: BatchGoalRequest) -> dict[str, Any]:
    """Submit multiple goals as a batch for parallel processing."""
    tenant = _require_tenant(request)
    svc = _goal_service(request)

    batch_id = uuid.uuid4().hex
    submitted = []

    for goal_text in body.goals:
        try:
            result = await svc.submit_goal(
                goal=goal_text,
                priority=body.priority,
                dry_run=False,
                tenant_ctx=tenant,
                agent_id=body.agent_id,
            )
            submitted.append(
                {
                    "goal_id": result.get("goal_id"),
                    "goal": goal_text[:100],
                    "status": "queued",
                }
            )
        except Exception as exc:
            submitted.append(
                {
                    "goal_id": None,
                    "goal": goal_text[:100],
                    "status": "error",
                    "error": _public_error(exc),
                }
            )

    return {
        "batch_id": batch_id,
        "total": len(body.goals),
        "queued": sum(1 for s in submitted if s["status"] == "queued"),
        "errors": sum(1 for s in submitted if s["status"] == "error"),
        "goals": submitted,
    }


@router.get("/batch/{batch_id}/status")
async def get_batch_status(request: Request, batch_id: str) -> dict[str, Any]:
    """Get status summary for a batch submission."""
    tenant_ctx = _require_tenant(request)
    svc = _goal_service(request)

    # batch_id is a comma-separated list of goal_ids
    goal_ids = [g.strip() for g in batch_id.split(",") if g.strip()]

    statuses = []
    for gid in goal_ids[:50]:  # cap at 50 to prevent abuse
        try:
            goal = await svc.get_goal(goal_id=gid, tenant_ctx=tenant_ctx)
            statuses.append({"goal_id": gid, "status": goal.get("status"), "error": None})
        except NotFoundError:
            statuses.append({"goal_id": gid, "status": "not_found", "error": "not found"})
        except Exception as exc:
            # A lookup failure is NOT "not found": it used to be, and counted
            # toward all_complete=true while the goal might still be running.
            statuses.append({"goal_id": gid, "status": "unknown", "error": _public_error(exc)})

    all_done = bool(statuses) and all(
        s["status"] in ("complete", "failed", "cancelled", "not_found") for s in statuses
    )
    return {
        "batch_id": batch_id,
        "goals": statuses,
        "all_complete": all_done,
        "total": len(statuses),
    }


@router.get("/{goal_id}/traces")
async def get_goal_traces(request: Request, goal_id: str) -> list[dict[str, Any]]:
    """Return decision trace records for this goal."""
    tenant = _require_tenant(request)
    svc = _goal_service(request)
    # Verify goal exists and belongs to tenant
    try:
        await svc.get_goal(goal_id=goal_id, tenant_ctx=tenant)
    except NotFoundError as exc:
        raise _not_found_response(request, exc) from exc
    # Query DB for traces
    # db_session_factory is not on app.state — get it from the session module
    from app.db.session import get_session_factory

    db = get_session_factory()
    if db is None:
        # Fall back to in-memory context
        return []
    try:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        async with db() as session, sqlalchemy_rls_context(session, tenant.tenant_id):
            result = await session.execute(
                text(
                    """SELECT id, action, reasoning, confidence, created_at
                        FROM decision_traces
                        WHERE goal_id = :gid AND tenant_id = :tid
                        ORDER BY created_at"""
                ),
                {"gid": goal_id, "tid": tenant.tenant_id},
            )
            rows = result.fetchall()
        return [
            {
                "trace_id": r[0],
                "action": r[1],
                "reasoning": r[2],
                "confidence": float(r[3]) if r[3] else 0.5,
                "at": r[4].isoformat() if r[4] else "",
            }
            for r in rows
        ]
    except Exception:
        return []


@router.get("/{goal_id}/lineage")
async def get_goal_lineage(request: Request, goal_id: str) -> dict[str, Any]:
    """Return the parent→child spawn tree for a goal."""
    tenant = _require_tenant(request)
    try:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context
        from app.db.session import get_session_factory

        db = get_session_factory()
        if db is None:
            return {
                "root_goal_id": goal_id,
                "nodes": [{"goal_id": goal_id, "depth": 0}],
                "edges": [],
            }

        async with db() as session, sqlalchemy_rls_context(session, tenant.tenant_id):
            rows = (
                await session.execute(
                    text("""
                SELECT
                    gl.id, gl.root_goal_id, gl.parent_goal_id, gl.child_goal_id,
                    gl.parent_agent_id, gl.child_agent_id, gl.civilization_id,
                    gl.spawn_reason, gl.depth, gl.spawned_at, gl.tenant_id
                FROM goal_lineage gl
                WHERE gl.root_goal_id = :root_id AND gl.tenant_id = :tid
                UNION ALL
                SELECT
                    'root' AS id,
                    :root_id AS root_goal_id,
                    NULL AS parent_goal_id,
                    :root_id AS child_goal_id,
                    NULL AS parent_agent_id,
                    NULL AS child_agent_id,
                    NULL AS civilization_id,
                    '' AS spawn_reason,
                    0 AS depth,
                    NOW() AS spawned_at,
                    :tid AS tenant_id
                ORDER BY depth ASC
            """),
                    {"root_id": goal_id, "tid": tenant.tenant_id},
                )
            ).fetchall()
    except Exception:
        return {"root_goal_id": goal_id, "nodes": [{"goal_id": goal_id, "depth": 0}], "edges": []}

    nodes = []
    edges = []
    seen_goals: set[str] = set()
    for row in rows:
        (
            row_id,
            _root_id,
            parent_gid,
            child_gid,
            _parent_aid,
            child_aid,
            _civ_id,
            reason,
            depth,
            spawned_at,
            _,
        ) = row
        if row_id == "root":
            continue
        if child_gid not in seen_goals:
            seen_goals.add(child_gid)
            nodes.append(
                {
                    "goal_id": child_gid,
                    "parent_goal_id": parent_gid,
                    "agent_id": child_aid,
                    "depth": depth,
                    "spawn_reason": reason,
                    "spawned_at": spawned_at.isoformat() if spawned_at else "",
                }
            )
        if parent_gid:
            edges.append({"parent": parent_gid, "child": child_gid})

    # Ensure root node is always present
    if goal_id not in seen_goals:
        nodes.insert(
            0,
            {
                "goal_id": goal_id,
                "parent_goal_id": None,
                "agent_id": None,
                "depth": 0,
                "spawn_reason": "",
                "spawned_at": "",
            },
        )

    return {"root_goal_id": goal_id, "nodes": nodes, "edges": edges}


@router.get("/{goal_id}/attempts")
async def get_goal_attempts(request: Request, goal_id: str) -> list[dict[str, Any]]:
    """Return persistence attempt history for a goal."""
    tenant = _require_tenant(request)
    try:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context
        from app.db.session import get_session_factory

        db = get_session_factory()
        if db is None:
            return []

        async with db() as session, sqlalchemy_rls_context(session, tenant.tenant_id):
            rows = (
                await session.execute(
                    text("""
                SELECT id, attempt_number, strategy, enriched_goal, started_at,
                       ended_at, succeeded, failure_reason, iterations_used,
                       cost_usd, backoff_seconds
                FROM goal_attempts
                WHERE goal_id = :gid AND tenant_id = :tid
                ORDER BY attempt_number ASC
            """),
                    {"gid": goal_id, "tid": tenant.tenant_id},
                )
            ).fetchall()
    except Exception:
        return []

    return [
        {
            "id": r[0],
            "attempt_number": r[1],
            "strategy": r[2],
            "enriched_goal": r[3],
            "started_at": r[4].isoformat() if r[4] else "",
            "ended_at": r[5].isoformat() if r[5] else "",
            "succeeded": r[6],
            "failure_reason": r[7] or "",
            "iterations_used": r[8],
            "cost_usd": float(r[9]) if r[9] else 0.0,
            "backoff_seconds": r[10],
        }
        for r in rows
    ]


@router.post("/{goal_id}/persistence/abort")
async def abort_persistence(request: Request, goal_id: str) -> dict[str, Any]:
    """Abort the active persistence loop for a goal."""
    tenant = _require_tenant(request)
    redis = getattr(request.app.state, "_policy_pubsub_redis", None)
    if redis is None:
        raise HTTPException(503, "Redis not available")
    await redis.setex(f"persistence_abort:{tenant.tenant_id}:{goal_id}", 3600, "1")
    return {"goal_id": goal_id, "status": "abort_requested"}


@router.post("/{goal_id}/persistence/skip-strategy")
async def skip_persistence_strategy(request: Request, goal_id: str) -> dict[str, Any]:
    """Advance to the next retry strategy in the persistence loop."""
    tenant = _require_tenant(request)
    redis = getattr(request.app.state, "_policy_pubsub_redis", None)
    if redis is None:
        raise HTTPException(503, "Redis not available")
    await redis.setex(f"persistence_skip_strategy:{tenant.tenant_id}:{goal_id}", 3600, "1")
    return {"goal_id": goal_id, "status": "skip_strategy_requested"}


class PersistenceGuidanceRequest(BaseModel):
    guidance: str = Field(..., min_length=1, max_length=5000)


class FeedbackRequest(BaseModel):
    rating: int  # 1-5 or thumbs: 1=up, -1=down
    comment: str = ""
    is_correct: bool | None = None  # was the goal result correct?


@router.post("/{goal_id}/feedback")
async def submit_goal_feedback(
    goal_id: str,
    body: FeedbackRequest,
    request: Request,
) -> dict:
    """Submit human feedback on a goal result (RLHF-lite)."""
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Auth required")
    # Feedback on a goal that does not exist (or belongs to another tenant) used
    # to be accepted and stored.
    try:
        await _goal_service(request).get_goal(goal_id=goal_id, tenant_ctx=tenant_ctx)
    except NotFoundError as exc:
        raise _not_found_response(request, exc) from exc

    # The human verdict is the verifier's ground truth (calibration). Written in
    # Postgres by goal, so it reaches a verdict recorded by any replica or worker
    # (it used to scan this process's in-memory buffer and swallow errors).
    if body.is_correct is not None:
        from app.intelligence.verifier_calibration import _default_calibration_store

        cal_store = getattr(request.app.state, "calibration_store", None) or (
            _default_calibration_store
        )
        try:
            await cal_store.record_actual_outcome_by_goal(
                goal_id=goal_id, tenant_id=tenant_ctx.tenant_id, actual_success=body.is_correct
            )
        except Exception as cal_exc:
            import logging

            # The feedback itself is still stored below; only calibration lags.
            logging.getLogger(__name__).warning(
                "verifier_calibration_feedback_failed goal_id=%s: %s", goal_id, cal_exc
            )

    # Persist to goal_feedback table (migration 0082)
    try:
        import uuid as _uuid

        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context
        from app.db.session import get_session_factory

        _db = get_session_factory()
        if _db is None:
            raise RuntimeError("no database configured")
        async with (
            _db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            await session.execute(
                text("""
                    INSERT INTO goal_feedback
                        (id, goal_id, tenant_id, rating, correction, created_at)
                    VALUES
                        (:id, :goal_id, :tenant_id, :rating, :correction, NOW())
                """),
                {
                    "id": str(_uuid.uuid4()),
                    "goal_id": goal_id,
                    "tenant_id": tenant_ctx.tenant_id,
                    "rating": body.rating,
                    "correction": body.comment or None,
                },
            )
    except Exception as _exc:
        import logging

        # Was a warning followed by "feedback_recorded" for feedback never stored.
        logging.getLogger(__name__).error("goal_feedback_persist_failed: %s", _exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Feedback could not be recorded; retry",
        ) from _exc

    return {
        "goal_id": goal_id,
        "status": "feedback_recorded",
        "rating": body.rating,
    }


@router.post("/{goal_id}/persistence/inject-guidance")
async def inject_persistence_guidance(
    request: Request, goal_id: str, body: PersistenceGuidanceRequest
) -> dict[str, Any]:
    """Inject human guidance text into the active persistence loop."""
    tenant = _require_tenant(request)
    redis = getattr(request.app.state, "_policy_pubsub_redis", None)
    if redis is None:
        raise HTTPException(503, "Redis not available")
    await redis.setex(
        f"persistence_guidance:{tenant.tenant_id}:{goal_id}",
        3600,
        body.guidance[:5000],
    )
    return {
        "goal_id": goal_id,
        "status": "guidance_injected",
        "guidance_length": len(body.guidance),
    }

