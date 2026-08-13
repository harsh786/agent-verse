"""Versioned tenant-authorized coordination command API."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.coordination.contracts import AuthorizationContext
from app.coordination.read_models import (
    CoordinationSessionRead,
    CoordinationTransitionRead,
)
from app.coordination.service import SessionAdmission
from app.coordination.state_machines import InvalidTransitionError
from app.coordination.store import OptimisticConflictError

router = APIRouter(tags=["coordination"])


class CreateSessionRequest(BaseModel):
    civilization_id: str = Field(min_length=1)
    goal_id: str = Field(min_length=1)
    policy_snapshot: dict[str, Any]
    budget_snapshot: dict[str, Any]


class TransitionRequest(BaseModel):
    expected_version: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=200)


class CanonicalTransitionRequest(BaseModel):
    expected_version: int = Field(gt=0)


def _tenant(request: Request) -> Any:
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")
    return tenant


def _service(request: Request) -> Any:
    service = getattr(request.app.state, "coordination_service", None)
    if service is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Coordination runtime is unavailable",
        )
    return service


@router.post("/coordination/v1/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(request: Request, body: CreateSessionRequest) -> dict[str, Any]:
    tenant = _tenant(request)
    admission = SessionAdmission(
        civilization_id=body.civilization_id,
        goal_id=body.goal_id,
        policy_snapshot=body.policy_snapshot,
        budget_snapshot=body.budget_snapshot,
        authorization=AuthorizationContext(
            actor_id=str(getattr(tenant, "api_key_id", tenant.tenant_id)),
            permissions=frozenset({"coordination:create"}),
        ),
    )
    result = await _service(request).create_session(tenant, admission)
    return cast(dict[str, Any], result.model_dump(mode="json"))


@router.post(
    "/api/v1/coordination/sessions",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CoordinationSessionRead,
    operation_id="create_coordination_session",
)
async def create_coordination_session(
    request: Request,
    response: Response,
    body: CreateSessionRequest,
    idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
) -> Any:
    del idempotency_key
    result = await create_session(request, body)
    response.headers["Location"] = (
        f"/api/v1/coordination/sessions/{result['session_id']}"
    )
    return result


@router.get(
    "/api/v1/coordination/sessions/{session_id}",
    response_model=CoordinationSessionRead,
    operation_id="get_coordination_session",
)
async def get_coordination_session(request: Request, session_id: str) -> Any:
    try:
        return await _service(request).get_session(_tenant(request), session_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found") from exc


async def _canonical_transition(
    request: Request,
    response: Response,
    session_id: str,
    body: CanonicalTransitionRequest,
    idempotency_key: str,
    command: str,
) -> Any:
    try:
        method = getattr(_service(request), command)
        result = await method(
            _tenant(request),
            session_id,
            expected_version=body.expected_version,
            idempotency_key=idempotency_key,
        )
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found") from exc
    except (InvalidTransitionError, OptimisticConflictError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    response.headers["Location"] = f"/api/v1/coordination/sessions/{session_id}"
    return result


@router.post(
    "/api/v1/coordination/sessions/{session_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CoordinationTransitionRead,
    operation_id="cancel_coordination_session",
)
async def cancel_coordination_session(
    request: Request,
    response: Response,
    session_id: str,
    body: CanonicalTransitionRequest,
    idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
) -> Any:
    return await _canonical_transition(
        request, response, session_id, body, idempotency_key, "cancel_session"
    )


@router.post(
    "/api/v1/coordination/sessions/{session_id}/resume",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CoordinationTransitionRead,
    operation_id="resume_coordination_session",
)
async def resume_coordination_session(
    request: Request,
    response: Response,
    session_id: str,
    body: CanonicalTransitionRequest,
    idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
) -> Any:
    return await _canonical_transition(
        request, response, session_id, body, idempotency_key, "resume_session"
    )


@router.post("/coordination/v1/sessions/{session_id}/start")
async def start_session(
    request: Request, session_id: str, body: TransitionRequest
) -> dict[str, Any]:
    result = await _service(request).start_session(
        _tenant(request),
        session_id,
        expected_version=body.expected_version,
        idempotency_key=body.idempotency_key,
    )
    return cast(dict[str, Any], result.model_dump(mode="json"))


@router.post("/coordination/v1/sessions/{session_id}/complete")
async def complete_session(
    request: Request, session_id: str, body: TransitionRequest
) -> dict[str, Any]:
    result = await _service(request).complete_session(
        _tenant(request),
        session_id,
        expected_version=body.expected_version,
        idempotency_key=body.idempotency_key,
    )
    return cast(dict[str, Any], result.model_dump(mode="json"))


__all__ = ["router"]
