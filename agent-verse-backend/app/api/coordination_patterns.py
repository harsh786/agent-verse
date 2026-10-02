"""Run coordination patterns on a session (ORG-25).

``POST /api/v1/coordination/sessions/{session_id}/patterns/{pattern}/runs`` drives one
of the coordination patterns (Magentic, Mixture-of-Agents, CAMEL, generative agents,
decentralized swarm, sealed-bid market auction) and persists its read model; the
pattern ``GET`` routes (ledger, moa/layers, camel, generative, swarm, auction) serve
what the run wrote. The POST admits the run and queues it on a worker (202);
retrying the same Idempotency-Key re-queues an unfinished run or returns the
stored outcome.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.coordination import _authorization
from app.coordination.pattern_runs.service import PATTERNS, PatternRunError
from app.coordination.store import OptimisticConflictError

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/coordination/sessions", tags=["coordination-patterns"])


class PatternRunOptions(BaseModel):
    layers: int = Field(default=2, ge=1, le=3)
    max_cost_usd: float = Field(default=1.0, gt=0, le=25)
    allow_degraded: bool = False
    timeout_seconds: int = Field(default=300, ge=10, le=900)
    max_tokens: int = Field(default=40_000, ge=1_000, le=200_000)
    max_resets: int = Field(default=1, ge=0, le=3)
    # Mixture-of-agents: models requested through the configured provider, one per
    # proposer (for model-routing backends); configured providers are used too.
    proposer_models: list[str] = Field(default_factory=list, max_length=6)


class PatternRunRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=4_000)
    participants: list[str] = Field(default_factory=list, max_length=6)
    max_rounds: int = Field(default=6, ge=1, le=12)
    options: PatternRunOptions = Field(default_factory=PatternRunOptions)


def _tenant(request: Request) -> Any:
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")
    return tenant


def _service(request: Request) -> Any:
    service = getattr(request.app.state, "pattern_run_service", None)
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Pattern runtime unavailable")
    return service


def _raise(exc: Exception) -> None:
    if isinstance(exc, PatternRunError):
        raise HTTPException(exc.status_code, str(exc)) from exc
    if isinstance(exc, KeyError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found") from exc
    if isinstance(exc, OptimisticConflictError):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "run is already in progress; retry later"
        ) from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    logger.exception("coordination_pattern_run_interrupted")
    raise HTTPException(
        status.HTTP_502_BAD_GATEWAY,
        "pattern run interrupted; retry with the same Idempotency-Key to resume",
    ) from exc


@router.get("/{session_id}/patterns", operation_id="list_coordination_patterns")
async def list_patterns(request: Request, session_id: str) -> dict[str, Any]:
    _tenant(request)
    return {
        "items": [
            {
                "pattern": name,
                "default_participants": list(spec.default_participants),
                "min_participants": spec.min_participants,
                "run_path": f"/api/v1/coordination/sessions/{session_id}/patterns/{name}/runs",
            }
            for name, spec in PATTERNS.items()
        ]
    }


def _enqueue(tenant: Any, session_id: str, pattern: str, execution_id: str) -> None:
    """Hand the admitted run to a worker on the tenant's plan queue."""
    from app.coordination.pattern_runs.tasks import run_pattern, tenant_payload
    from app.scaling.celery_app import goal_queue_for

    payload = tenant_payload(tenant)
    run_pattern.apply_async(
        args=[payload, session_id, pattern, execution_id],
        queue=goal_queue_for(payload["plan"]),
    )


@router.post(
    "/{session_id}/patterns/{pattern}/runs",
    operation_id="run_coordination_pattern",
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_pattern(
    request: Request,
    session_id: str,
    pattern: str,
    body: PatternRunRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
) -> Any:
    """Admit the run and execute it on a worker (ORG-39).

    The run used to be awaited inside this request (up to 40 LLM calls, 300 s):
    proxies answered 504 while it continued and a restart stranded it. Now the run
    document is persisted, the execution is queued, and 202 returns the
    ``execution_id``; progress arrives on the live bus and ``GET .../runs``. A
    retry with the same Idempotency-Key re-queues an unfinished run (the worker
    resumes from its checkpoint) or returns the stored outcome (200) once it is
    terminal or awaiting human review.
    """
    tenant = _tenant(request)
    # Running a pattern spends LLM budget and writes session state: operator/admin
    # only, like creating a session.
    if "coordination:create" not in _authorization(tenant).permissions:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "coordination:create permission required")
    service = _service(request)
    try:
        document, pending = await service.admit(
            tenant,
            session_id,
            pattern,
            objective=body.objective,
            participants=tuple(body.participants),
            max_rounds=body.max_rounds,
            options=body.options.model_dump(),
            idempotency_key=idempotency_key,
        )
        if not pending:
            done: dict[str, Any] = await service.admitted_result(tenant, pattern, document)
            return JSONResponse(done, status_code=status.HTTP_200_OK)
    except Exception as exc:  # mapped to HTTP below; unknown errors re-raise
        _raise(exc)
        raise
    try:
        _enqueue(tenant, session_id, pattern, document.execution_id)
    except Exception as exc:
        # Admitted but not queued: say so; the same Idempotency-Key re-queues it.
        logger.warning("coordination_pattern_run_enqueue_failed", error=type(exc).__name__)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "the run could not be queued; retry with the same Idempotency-Key",
        ) from exc
    return {
        "pattern": pattern,
        "session_id": session_id,
        "execution_id": document.execution_id,
        "phase": document.view.get("phase") or "queued",
        "status": "queued",
        "runs_path": f"/api/v1/coordination/sessions/{session_id}/patterns/{pattern}/runs",
    }


@router.get(
    "/{session_id}/patterns/{pattern}/runs",
    operation_id="list_coordination_pattern_runs",
)
async def list_pattern_runs(request: Request, session_id: str, pattern: str) -> dict[str, Any]:
    tenant = _tenant(request)
    try:
        items = await _service(request).list_runs(str(tenant.tenant_id), session_id, pattern)
    except PatternRunError as exc:
        _raise(exc)
        raise
    return {"items": items}


__all__ = ["PatternRunOptions", "PatternRunRequest", "router"]
