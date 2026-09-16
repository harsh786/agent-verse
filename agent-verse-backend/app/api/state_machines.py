"""State Machine API — CRUD for state machine definitions + instance transitions."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.triggers.state_machine import (
    StateDefinition,
    StateMachine,
    StateMachineDefinition,
    TransitionDefinition,
)

router = APIRouter(prefix="/state-machines", tags=["state-machines"])

# Module-level in-memory store (replaced by DB-backed version in production)
_sm_registry = StateMachine()


# ── Request models ────────────────────────────────────────────────────────────


class StateModel(BaseModel):
    name: str
    is_initial: bool = False
    is_terminal: bool = False
    description: str = ""


class TransitionModel(BaseModel):
    from_state: str
    to_state: str
    event: str
    condition_cel: str = ""
    description: str = ""


class CreateStateMachineRequest(BaseModel):
    name: str
    states: list[StateModel]
    transitions: list[TransitionModel]


class TransitionRequest(BaseModel):
    event: str
    payload: dict = {}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return ctx


def _get_registry(request: Request) -> StateMachine:
    return getattr(request.app.state, "state_machine_registry", _sm_registry)


# ── Routes ────────────────────────────────────────────────────────────────────


@router.post("", status_code=201)
async def create_state_machine(request: Request, body: CreateStateMachineRequest) -> dict:
    tenant = _require_tenant(request)
    registry = _get_registry(request)
    machine_id = uuid.uuid4().hex

    defn = StateMachineDefinition(
        machine_id=machine_id,
        tenant_id=tenant.tenant_id,
        name=body.name,
        states=[StateDefinition(**s.model_dump()) for s in body.states],
        transitions=[TransitionDefinition(**t.model_dump()) for t in body.transitions],
    )
    await registry.define_async(defn)

    return {
        "machine_id": machine_id,
        "name": body.name,
        "states": [s.model_dump() for s in body.states],
        "transitions": [t.model_dump() for t in body.transitions],
    }


@router.get("")
async def list_state_machines(request: Request) -> list[dict]:
    tenant = _require_tenant(request)
    registry = _get_registry(request)
    return [
        {"machine_id": d.machine_id, "name": d.name, "state_count": len(d.states)}
        for d in await registry.list_definitions_async(tenant.tenant_id)
    ]


@router.get("/{machine_id}")
async def get_state_machine(machine_id: str, request: Request) -> dict:
    tenant = _require_tenant(request)
    registry = _get_registry(request)
    defn = await registry.get_definition_async(machine_id, tenant.tenant_id)
    if defn is None:
        raise HTTPException(status_code=404, detail="State machine not found")
    return {
        "machine_id": defn.machine_id,
        "name": defn.name,
        "states": [
            {"name": s.name, "is_initial": s.is_initial, "is_terminal": s.is_terminal}
            for s in defn.states
        ],
        "transitions": [
            {"from_state": t.from_state, "to_state": t.to_state, "event": t.event}
            for t in defn.transitions
        ],
    }


@router.delete("/{machine_id}", status_code=204)
async def delete_state_machine(machine_id: str, request: Request) -> None:
    tenant = _require_tenant(request)
    registry = _get_registry(request)
    defn = await registry.get_definition_async(machine_id, tenant.tenant_id)
    if defn is None:
        raise HTTPException(status_code=404, detail="State machine not found")
    await registry.delete_definition_async(machine_id, tenant.tenant_id)


@router.post("/{machine_id}/instances", status_code=201)
async def create_instance(machine_id: str, request: Request) -> dict:
    tenant = _require_tenant(request)
    body = await request.json()
    entity_id = body.get("entity_id", uuid.uuid4().hex)
    registry = _get_registry(request)
    try:
        instance = await registry.create_instance_async(machine_id, entity_id, tenant.tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "instance_id": instance.instance_id,
        "entity_id": entity_id,
        "current_state": instance.current_state,
        "status": instance.status,
    }


@router.post("/{machine_id}/instances/{entity_id}/transition")
async def transition_instance(
    machine_id: str, entity_id: str, request: Request, body: TransitionRequest
) -> dict:
    tenant = _require_tenant(request)
    registry = _get_registry(request)
    try:
        result = await registry.transition_async(
            machine_id, entity_id, body.event, tenant.tenant_id, payload=body.payload
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result


@router.get("/{machine_id}/instances/{entity_id}")
async def get_instance(machine_id: str, entity_id: str, request: Request) -> dict:
    tenant = _require_tenant(request)
    registry = _get_registry(request)
    instance = await registry.get_instance_async(entity_id, tenant.tenant_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="Instance not found")
    return {
        "instance_id": instance.instance_id,
        "entity_id": entity_id,
        "current_state": instance.current_state,
        "status": instance.status,
        "history": instance.history[-20:],  # last 20 transitions
        "updated_at": instance.updated_at,
    }
