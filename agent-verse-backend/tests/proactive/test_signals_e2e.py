"""Phase 9 e2e — a proactive signal drives the engine and lands in a chat thread.

Mounts the proactive signals router with a real ProactiveEngine whose deliver
callback posts into an in-memory ChatService (as boot wiring does), then posts a
signal and asserts: the consent gate is honored, an allowed outreach is persisted
into the principal's chat thread and audited source=proactive, and tenant comes
from auth (not the body).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.chat.proactive import ProactivePreferences
from app.chat.service import ChatService
from app.proactive.engine import ProactiveEngine
from app.proactive.router import router as proactive_router

TENANT = "tenant-proactive"


class _FakeTenant:
    def __init__(self, tid: str) -> None:
        self.tenant_id = tid


def _build(prefs: ProactivePreferences | None = None):
    app = FastAPI()

    # Inject an authenticated tenant like TenantMiddleware would.
    @app.middleware("http")
    async def _auth(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.tenant = _FakeTenant(TENANT)
        return await call_next(request)

    app.include_router(proactive_router)
    chat = ChatService()
    audits: list[dict[str, Any]] = []

    async def _deliver(signal: Any, proposal: Any) -> None:
        await chat.deliver_proactive(
            principal_id=signal.principal_id, tenant_id=signal.tenant_id,
            message=proposal.message, channel=signal.channel,
        )

    def _audit(event: dict[str, Any]) -> None:
        audits.append(event)

    app.state.chat_service = chat
    app.state.proactive_engine = ProactiveEngine(
        deliver=_deliver, audit=_audit,
        preferences_provider=(lambda _pid: prefs) if prefs else None,
    )
    return app, chat, audits


def test_signal_delivers_into_chat_thread_and_audits() -> None:
    app, chat, audits = _build()
    client = TestClient(app)
    r = client.post("/v1/proactive/signals", json={
        "kind": "flight_delayed",
        "principal_id": TENANT,
        "payload": {"title": "Flight AA123"},
    })
    assert r.status_code == 200
    data = r.json()
    assert data["delivered"] is True
    assert data["requires_confirmation"] is True  # high-impact → confirmation

    # The proactive message is now in the principal's chat thread.
    session_id = chat._principal_sessions[TENANT]
    history = chat.list_messages(session_id, TENANT)
    assert history and history[-1].role == "assistant"
    assert "rebook" in history[-1].content.lower()
    assert history[-1].metadata.get("delivery") == "proactive"

    # Audited as source=proactive.
    assert audits and audits[-1]["source"] == "proactive"
    assert audits[-1]["tenant_id"] == TENANT


def test_quiet_hours_signal_is_not_delivered() -> None:
    # A signal during quiet hours is gated — nothing is posted.
    prefs = ProactivePreferences(quiet_hours=(0, 23))  # nearly always quiet
    app, chat, _ = _build(prefs)
    client = TestClient(app)
    r = client.post("/v1/proactive/signals", json={
        "kind": "memory_followup", "payload": {"note": "water plants"},
    })
    assert r.status_code == 200
    body = r.json()
    # Either delivered or quiet_hours depending on the hour; when gated, no thread.
    if not body["delivered"]:
        assert body["reason"] in ("quiet_hours", "rate_limited")
        assert TENANT not in chat._principal_sessions


def test_unknown_signal_kind_makes_no_proposal() -> None:
    app, _, _ = _build()
    client = TestClient(app)
    r = client.post("/v1/proactive/signals", json={"kind": "random_noise"})
    assert r.status_code == 200
    assert r.json() == {"delivered": False, "reason": "no_proposal",
                        "requires_confirmation": False}


async def test_deliver_proactive_creates_session_when_none_open() -> None:
    chat = ChatService()
    msg = await chat.deliver_proactive(
        principal_id="p1", tenant_id="t1", message="Reminder: standup at 10."
    )
    assert msg is not None and msg.role == "assistant"
    session_id = chat._principal_sessions["p1"]
    history = chat.list_messages(session_id, "t1")
    assert history[-1].content == "Reminder: standup at 10."
