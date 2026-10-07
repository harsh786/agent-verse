"""a10-F254-02: payload-level contract for the uninventoried frontend folders.

The obsidian, notifications, simulation and status pages (feature folders with
no inventory entry) were only checked for route existence; their e2e specs
mocked or ignored the backend payload. This pins, against the real
``create_app()`` (in-memory phase — no live services), every response field
those pages read, as typed in ``agent-verse-frontend/src`` (``StatusPage.tsx``,
``SimulationPage.tsx`` / ``simulationApi``, ``notificationsApi``,
``ObsidianPage.tsx``). A backend change that drops or renames one fails here.
(Onboarding only calls settings / connectors / agents / goals, which are
inventoried features with their own contracts. ObsidianPage reads
``GET /v1/org`` -> ``data[].id/name``, which needs Postgres: pinned in
``tests/e2e_full/test_org_db_e2e.py``.)
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(scope="module")
def headers(client: TestClient) -> dict[str, str]:
    r = client.post("/tenants/signup", json={"name": "Contract", "email": "contract@example.com"})
    assert r.status_code in (200, 201), r.text
    return {"X-API-Key": r.json()["api_key"]}


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ── status (StatusPage.tsx: StatusData) ──────────────────────────────────────


def test_public_status_payload(client: TestClient) -> None:
    r = client.get("/status")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in {"operational", "degraded", "unknown"}
    assert isinstance(body["components"], dict) and body["components"]
    for comp in body["components"].values():
        assert comp["status"] in {"operational", "degraded", "unknown"}
        assert set(comp) <= {"status", "latency_ms"}  # never error text
    assert _num(body["timestamp"])


# ── simulation (SimulationPage.tsx + simulationApi) ──────────────────────────


def test_simulation_run_payload(client: TestClient, headers: dict[str, str]) -> None:
    r = client.post(
        "/enterprise/simulation",
        json={"goal": "Fetch GitHub issues and report", "mock_tools": {"github:list_issues": "[]"}},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert isinstance(body["run_id"], str) and body["run_id"]
    assert isinstance(body["status"], str)
    assert isinstance(body["steps"], list) and body["steps"]
    for step in body["steps"]:
        assert "output" in step and "tool" in step
    assert _num(body["cost_usd"])
    assert isinstance(body["iterations"], int)
    assert isinstance(body["used_real_llm"], bool)
    assert isinstance(body.get("message", ""), str)

    got = client.get(f"/enterprise/simulation/{body['run_id']}", headers=headers)
    assert got.status_code == 200, got.text
    run = got.json()
    assert run["run_id"] == body["run_id"]
    assert isinstance(run["status"], str)
    assert isinstance(run["steps"], list)
    assert _num(run["cost_usd"])


def test_simulation_available_tools_payload(client: TestClient, headers: dict[str, str]) -> None:
    r = client.get("/enterprise/simulation/available-tools", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["tools"], list)
    assert isinstance(body["total"], int)
    for tool in body["tools"]:
        assert {"name", "description", "server_id"} <= set(tool)


def test_simulation_stream_events(client: TestClient, headers: dict[str, str]) -> None:
    r = client.post(
        "/enterprise/simulation/stream",
        json={"goal": "Send a Slack message", "mock_tools": {"slack:send_message": "ok"}},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    events = [
        json.loads(line[len("data: "):]) for line in r.text.splitlines() if line.startswith("data: ")
    ]
    types = [e["type"] for e in events]
    assert "simulation_started" in types
    assert types[-1] in {"simulation_complete", "simulation_error"}
    for e in events:
        if e["type"] == "step_completed":
            assert isinstance(e["step_number"], int)
            assert "output" in e and "tool_called" in e and "mock_hit" in e
            assert _num(e["cost_increment"])
        if e["type"] == "simulation_complete":
            assert isinstance(e["total_steps"], int)
            assert _num(e["total_cost"])
            assert isinstance(e["used_real_llm"], bool)
            assert isinstance(e["final_status"], str)
        if e["type"] == "simulation_error":
            assert isinstance(e["message"], str)


def test_governance_simulate_payload(client: TestClient, headers: dict[str, str]) -> None:
    r = client.post("/governance/simulate", json={"goal": "deploy to prod"}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    summary = body["summary"]
    for key in ("allowed_tools", "denied_tools", "requires_approval"):
        assert isinstance(summary[key], list)
    assert isinstance(summary["would_block_execution"], bool)
    assert isinstance(summary["hitl_approvals_needed"], int)
    assert isinstance(body["policy_checks"], list)


# ── notifications (notificationsApi) ─────────────────────────────────────────


def test_notification_channels_payload(client: TestClient, headers: dict[str, str]) -> None:
    created = client.post(
        "/governance/notifications",
        json={"channel_type": "webhook", "config": {"url": "https://example.com/hook"}},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    c = created.json()
    assert {"channel_id", "type", "status"} <= set(c)
    listed = client.get("/governance/notifications", headers=headers)
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert isinstance(rows, list) and rows
    for row in rows:
        assert isinstance(row["channel_id"], str)
        assert isinstance(row["type"], str)
        assert isinstance(row["enabled"], bool)
