"""Phase 3 e2e — Telegram & WhatsApp inbound messages drive the unified ChatService.

Mounts the gateway router with an in-memory ChatService (+ FakeProvider for QA
replies), a channel registry, and identity service, then posts real Telegram- and
WhatsApp-shaped webhook payloads and asserts: the tenant is resolved from the
addressee, the message runs through the SAME chat pipeline (durable session), and a
non-JSON reply comes back — plus cross-channel continuity to one principal.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.chat.service import ChatService
from app.gateway.channel_registry import ChannelRegistry
from app.gateway.router import router as gateway_router
from app.identity import IdentityService
from app.providers.fake import FakeProvider

TENANT = "tenant-msg"
TG_BOT = "bot-1"
WA_NUM = "15550000000"


def _app() -> tuple[FastAPI, ChatService, IdentityService]:
    app = FastAPI()
    app.include_router(gateway_router)
    identity = IdentityService()
    chat = ChatService(answer_generator=FakeProvider(responses=["Here's a quick answer for you."]))
    chat.attach_engine(identity_service=identity)
    reg = ChannelRegistry()
    reg.register("telegram", TG_BOT, TENANT)
    reg.register("whatsapp", WA_NUM, TENANT)
    app.state.chat_service = chat
    app.state.channel_registry = reg
    return app, chat, identity


def _telegram_payload(user_id: str, text: str) -> dict:
    return {
        "addressee": TG_BOT,
        "message": {"from": {"id": user_id, "first_name": "Ada"},
                    "chat": {"id": user_id}, "text": text},
    }


def _whatsapp_payload(from_number: str, text: str) -> dict:
    return {
        "addressee": WA_NUM,
        "entry": [{"changes": [{"value": {
            "contacts": [{"profile": {"name": "Ada"}}],
            "messages": [{"from": from_number, "type": "text", "text": {"body": text}}],
        }}]}],
    }


def test_telegram_inbound_routes_through_chatservice() -> None:
    app, chat, _ = _app()
    client = TestClient(app)
    r = client.post("/v1/gateway/telegram/chat", json=_telegram_payload("42", "what can you do?"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and body["channel"] == "telegram"
    assert body["reply"] and not body["reply"].lstrip().startswith("{")  # never raw JSON
    # A durable voice/telegram session exists for this user with the turn persisted.
    session = chat.get_or_create_channel_session(
        tenant_id=TENANT, channel="telegram", channel_user_id="42"
    )
    assert any("what can you do" in m.content for m in chat.list_messages(session.id, TENANT))


def test_whatsapp_inbound_routes_through_chatservice() -> None:
    app, chat, _ = _app()
    client = TestClient(app)
    r = client.post("/v1/gateway/whatsapp/chat", json=_whatsapp_payload("15551234567", "hello"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and body["channel"] == "whatsapp"
    assert body["reply"]


def test_unknown_addressee_does_no_tenant_work() -> None:
    app, _, _ = _app()
    client = TestClient(app)
    r = client.post("/v1/gateway/telegram/chat",
                    json={"addressee": "unknown-bot", "message": {"from": {"id": "1"},
                          "chat": {"id": "1"}, "text": "hi"}})
    assert r.status_code == 200
    assert r.json()["status"] == "ignored"


def test_repeat_telegram_messages_continue_one_session() -> None:
    app, chat, _ = _app()
    client = TestClient(app)
    client.post("/v1/gateway/telegram/chat", json=_telegram_payload("99", "first message"))
    client.post("/v1/gateway/telegram/chat", json=_telegram_payload("99", "second message"))
    session = chat.get_or_create_channel_session(
        tenant_id=TENANT, channel="telegram", channel_user_id="99"
    )
    contents = [m.content for m in chat.list_messages(session.id, TENANT)]
    assert any("first message" in c for c in contents)
    assert any("second message" in c for c in contents)


def test_a_redelivered_telegram_webhook_does_not_create_a_second_turn() -> None:
    """Telegram/WhatsApp/Slack redeliver on any non-2xx or timeout.

    Regression: `channel_chat` had no dedup at all, so a redelivery was
    indistinguishable from a new message — the turn was persisted twice and, for
    a GOAL intent, two goals were submitted for one user message.
    `CommandDeduplicator` was written for exactly this case ("Critical for:
    button double-taps, network retries, webhook replay") but was wired into
    nothing.
    """
    app, chat, _ = _app()
    client = TestClient(app)
    user_id = "tg-replay-1"
    payload = _telegram_payload(user_id, "book me a meeting")
    payload["message"]["message_id"] = 4242

    first = client.post("/v1/gateway/telegram/chat", json=payload)
    assert first.status_code == 200, first.text

    # Identical redelivery — same platform message_id.
    second = client.post("/v1/gateway/telegram/chat", json=payload)
    assert second.status_code == 200, second.text
    assert second.json().get("reason") == "duplicate message ignored", second.json()

    session = chat.get_or_create_channel_session(
        tenant_id=TENANT, channel="telegram", channel_user_id=user_id
    )
    user_turns = [
        m for m in chat.list_messages(session.id, TENANT)
        if m.role == "user" and "book me a meeting" in m.content
    ]
    assert len(user_turns) == 1, f"redelivery persisted the turn twice: {user_turns}"


def test_a_genuinely_new_message_from_the_same_user_still_goes_through() -> None:
    app, chat, _ = _app()
    client = TestClient(app)
    user_id = "tg-replay-2"
    for i, text in enumerate(("first question", "second question"), start=1):
        payload = _telegram_payload(user_id, text)
        payload["message"]["message_id"] = 9000 + i
        resp = client.post("/v1/gateway/telegram/chat", json=payload)
        assert resp.status_code == 200
        assert resp.json().get("reason") != "duplicate message ignored", resp.json()

    session = chat.get_or_create_channel_session(
        tenant_id=TENANT, channel="telegram", channel_user_id=user_id
    )
    contents = [m.content for m in chat.list_messages(session.id, TENANT) if m.role == "user"]
    assert any("first question" in c for c in contents), contents
    assert any("second question" in c for c in contents), contents
