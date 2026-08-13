"""Tenant-authorized REST commands for durable handoffs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.coordination.contracts import Classification
from app.coordination.handoffs.models import HandoffState
from app.coordination.store import OptimisticConflictError

router = APIRouter(prefix="/api/v1/coordination/sessions", tags=["coordination-handoffs"])


class CreateHandoffRequest(BaseModel):
    civilization_id: str = Field(min_length=1)
    source_agent_id: str = Field(min_length=1)
    target_agent_id: str = Field(min_length=1)
    task_summary: str = Field(min_length=1, max_length=2_000)
    remaining_budget_usd: float = Field(ge=0)
    deadline: datetime
    acceptance_token: str = Field(min_length=16)
    context_message_ids: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    classification: Classification = Classification.INTERNAL


class HandoffTransitionRequest(BaseModel):
    expected_version: int = Field(gt=0)
    acceptance_token: str | None = None
    result_reference: str | None = None


def _tenant_id(request: Request) -> str:
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unauthorized")
    return str(tenant.tenant_id)


def _service(request: Request) -> Any:
    service = getattr(request.app.state, "handoff_service", None)
    if service is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Handoff runtime unavailable")
    return service


def _public(record: Any) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        record.model_dump(mode="json", exclude={"acceptance_token_digest"}),
    )


@router.post("/{session_id}/handoffs", status_code=status.HTTP_201_CREATED)
async def create_handoff(
    request: Request,
    session_id: str,
    body: CreateHandoffRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
) -> dict[str, Any]:
    record = await _service(request).request(
        tenant_id=_tenant_id(request),
        session_id=session_id,
        civilization_id=body.civilization_id,
        source_agent_id=body.source_agent_id,
        target_agent_id=body.target_agent_id,
        task_summary=body.task_summary,
        remaining_budget_usd=body.remaining_budget_usd,
        deadline=body.deadline,
        acceptance_token=body.acceptance_token,
        idempotency_key=idempotency_key,
        context_message_ids=body.context_message_ids,
        artifact_refs=body.artifact_refs,
        classification=body.classification,
    )
    return _public(record)


async def _transition(
    request: Request,
    session_id: str,
    handoff_id: str,
    body: HandoffTransitionRequest,
    idempotency_key: str,
    target: HandoffState,
) -> dict[str, Any]:
    try:
        current = await _service(request).get(_tenant_id(request), handoff_id)
        if current is None or current.session_id != session_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Handoff not found")
        if target is HandoffState.ACCEPTED:
            if body.acceptance_token is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Token required")
            record = await _service(request).accept(
                _tenant_id(request),
                handoff_id,
                token=body.acceptance_token,
                expected_version=body.expected_version,
                idempotency_key=idempotency_key,
            )
        else:
            record = await _service(request).transition(
                _tenant_id(request),
                handoff_id,
                target=target,
                expected_version=body.expected_version,
                idempotency_key=idempotency_key,
                result_reference=body.result_reference,
            )
    except OptimisticConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Handoff not found") from exc
    except PermissionError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return _public(record)


@router.post("/{session_id}/handoffs/{handoff_id}/accept")
async def accept_handoff(
    request: Request,
    session_id: str,
    handoff_id: str,
    body: HandoffTransitionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
) -> dict[str, Any]:
    return await _transition(
        request, session_id, handoff_id, body, idempotency_key, HandoffState.ACCEPTED
    )


@router.post("/{session_id}/handoffs/{handoff_id}/reject")
async def reject_handoff(
    request: Request,
    session_id: str,
    handoff_id: str,
    body: HandoffTransitionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
) -> dict[str, Any]:
    return await _transition(
        request, session_id, handoff_id, body, idempotency_key, HandoffState.REJECTED
    )


@router.post("/{session_id}/handoffs/{handoff_id}/cancel")
async def cancel_handoff(
    request: Request,
    session_id: str,
    handoff_id: str,
    body: HandoffTransitionRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
) -> dict[str, Any]:
    return await _transition(
        request, session_id, handoff_id, body, idempotency_key, HandoffState.CANCELLED
    )


@router.get("/{session_id}/handoffs/{handoff_id}")
async def get_handoff(request: Request, session_id: str, handoff_id: str) -> dict[str, Any]:
    record = await _service(request).get(_tenant_id(request), handoff_id)
    if record is None or record.session_id != session_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Handoff not found")
    return _public(record)


__all__ = ["router"]
