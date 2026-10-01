"""Run coordination patterns on a session (ORG-25).

``POST /api/v1/coordination/sessions/{session_id}/patterns/{pattern}/runs`` drives one
of the coordination patterns (Magentic, Mixture-of-Agents, CAMEL, generative agents,
decentralized swarm, sealed-bid market auction) and persists its read model; the
pattern ``GET`` routes (ledger, moa/layers, camel, generative, swarm, auction) serve
what the run wrote. The run is synchronous and bounded; retrying the same
Idempotency-Key resumes an interrupted run or returns the stored outcome.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Header, HTTPException, Request, status
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


@router.post(
    "/{session_id}/patterns/{pattern}/runs",
    operation_id="run_coordination_pattern",
)
async def run_pattern(
    request: Request,
    session_id: str,
    pattern: str,
    body: PatternRunRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
) -> dict[str, Any]:
    tenant = _tenant(request)
    # Running a pattern spends LLM budget and writes session state: operator/admin
    # only, like creating a session.
    if "coordination:create" not in _authorization(tenant).permissions:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "coordination:create permission required")
    try:
        result: dict[str, Any] = await _service(request).run(
            tenant,
            session_id,
            pattern,
            objective=body.objective,
            participants=tuple(body.participants),
            max_rounds=body.max_rounds,
            options=body.options.model_dump(),
            idempotency_key=idempotency_key,
        )
    except Exception as exc:  # mapped to HTTP below; unknown errors re-raise
        _raise(exc)
        raise
    return result


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
