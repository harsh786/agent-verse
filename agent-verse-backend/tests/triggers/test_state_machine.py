"""Tests for StateMachine model and API."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI
from types import SimpleNamespace

from app.triggers.state_machine import (
    StateMachine,
    StateMachineDefinition,
    StateDefinition,
    TransitionDefinition,
)
from app.api.state_machines import router


# ── StateMachine unit tests ───────────────────────────────────────────────────

def make_simple_sm() -> tuple[StateMachine, StateMachineDefinition]:
    sm = StateMachine()
    defn = StateMachineDefinition(
        machine_id="m1",
        tenant_id="t1",
        name="Order Flow",
        states=[
            StateDefinition(name="pending", is_initial=True),
            StateDefinition(name="processing"),
            StateDefinition(name="completed", is_terminal=True),
            StateDefinition(name="cancelled", is_terminal=True),
        ],
        transitions=[
            TransitionDefinition(from_state="pending", to_state="processing", event="start"),
            TransitionDefinition(from_state="processing", to_state="completed", event="complete"),
            TransitionDefinition(from_state="processing", to_state="cancelled", event="cancel"),
            TransitionDefinition(from_state="pending", to_state="cancelled", event="cancel"),
        ],
    )
    sm.define(defn)
    return sm, defn


def test_sm_create_instance():
    sm, _ = make_simple_sm()
    instance = sm.create_instance("m1", "order-001", "t1")
    assert instance.current_state == "pending"
    assert instance.status == "running"


def test_sm_transition_success():
    sm, _ = make_simple_sm()
    sm.create_instance("m1", "order-001", "t1")
    result = sm.transition("m1", "order-001", "start", "t1")
    assert result["transitioned"] is True
    assert result["from_state"] == "pending"
    assert result["to_state"] == "processing"


def test_sm_transition_chain():
    sm, _ = make_simple_sm()
    sm.create_instance("m1", "order-002", "t1")
    sm.transition("m1", "order-002", "start", "t1")
    result = sm.transition("m1", "order-002", "complete", "t1")
    assert result["to_state"] == "completed"
    instance = sm.get_instance("order-002", "t1")
    assert instance.status == "completed"


def test_sm_no_matching_transition():
    sm, _ = make_simple_sm()
    sm.create_instance("m1", "order-003", "t1")
    result = sm.transition("m1", "order-003", "nonexistent_event", "t1")
    assert result["transitioned"] is False
    assert result["reason"] == "no_matching_transition"


def test_sm_history_recorded():
    sm, _ = make_simple_sm()
    sm.create_instance("m1", "order-004", "t1")
    sm.transition("m1", "order-004", "start", "t1")
    sm.transition("m1", "order-004", "cancel", "t1")
    instance = sm.get_instance("order-004", "t1")
    assert len(instance.history) == 2
    assert instance.history[0]["event"] == "start"
    assert instance.history[1]["event"] == "cancel"


def test_sm_tenant_isolation():
    sm, _ = make_simple_sm()
    sm.create_instance("m1", "order-005", "t1")
    # Tenant 2 cannot access tenant 1's instance
    instance = sm.get_instance("order-005", "t2")
    assert instance is None


def test_sm_list_definitions():
    sm, _ = make_simple_sm()
    defs = sm.list_definitions("t1")
    assert len(defs) == 1
    assert defs[0].name == "Order Flow"


def test_sm_unknown_machine_raises():
    sm = StateMachine()
    with pytest.raises(ValueError, match="Unknown state machine"):
        sm.create_instance("nonexistent", "e1", "t1")


# ── State Machine API tests ───────────────────────────────────────────────────

@pytest.fixture
def app():
    application = FastAPI()
    application.include_router(router)

    @application.middleware("http")
    async def inject_tenant(req, call_next):
        req.state.tenant = SimpleNamespace(tenant_id="t1", plan="free")
        return await call_next(req)

    return application


@pytest.fixture
def client(app):
    return TestClient(app)


def test_api_create_state_machine(client):
    resp = client.post("/state-machines", json={
        "name": "Order Flow",
        "states": [
            {"name": "pending", "is_initial": True},
            {"name": "completed", "is_terminal": True},
        ],
        "transitions": [
            {"from_state": "pending", "to_state": "completed", "event": "complete"},
        ],
    })
    assert resp.status_code == 201
    data = resp.json()
    assert "machine_id" in data
    assert data["name"] == "Order Flow"


def test_api_list_state_machines(client):
    client.post("/state-machines", json={
        "name": "Test SM",
        "states": [{"name": "start", "is_initial": True}],
        "transitions": [],
    })
    resp = client.get("/state-machines")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_api_create_and_transition(client):
    cr = client.post("/state-machines", json={
        "name": "Simple",
        "states": [
            {"name": "open", "is_initial": True},
            {"name": "closed", "is_terminal": True},
        ],
        "transitions": [
            {"from_state": "open", "to_state": "closed", "event": "close"},
        ],
    })
    machine_id = cr.json()["machine_id"]

    # Create instance
    ir = client.post(f"/state-machines/{machine_id}/instances", json={"entity_id": "e1"})
    assert ir.status_code == 201
    assert ir.json()["current_state"] == "open"

    # Transition
    tr = client.post(f"/state-machines/{machine_id}/instances/e1/transition", json={"event": "close"})
    assert tr.status_code == 200
    assert tr.json()["transitioned"] is True
    assert tr.json()["to_state"] == "closed"


def test_api_get_instance(client):
    cr = client.post("/state-machines", json={
        "name": "Track",
        "states": [{"name": "init", "is_initial": True}],
        "transitions": [],
    })
    machine_id = cr.json()["machine_id"]
    client.post(f"/state-machines/{machine_id}/instances", json={"entity_id": "e99"})
    resp = client.get(f"/state-machines/{machine_id}/instances/e99")
    assert resp.status_code == 200
    assert resp.json()["current_state"] == "init"


def test_api_delete_state_machine(client):
    cr = client.post("/state-machines", json={
        "name": "Deletable",
        "states": [{"name": "s", "is_initial": True}],
        "transitions": [],
    })
    machine_id = cr.json()["machine_id"]
    assert client.delete(f"/state-machines/{machine_id}").status_code == 204
    assert client.get(f"/state-machines/{machine_id}").status_code == 404
