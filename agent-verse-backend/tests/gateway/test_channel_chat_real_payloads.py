"""TRG-41: real Telegram / WhatsApp deliveries resolve their binding and get a reply.

``/{channel}/chat`` picked the binding from a top-level ``addressee`` / ``bot_id``
/ ``to`` field that real Telegram updates and WhatsApp Cloud payloads do not
carry (the old e2e tests injected a synthetic ``addressee``), ``from_env`` never
parsed an outbound token, and the reply call did not match the adapters'
signatures — so real deliveries never resolved and no reply was ever sent.

Now the binding comes from the URL (``/{channel}/chat/{binding_id}``, e.g. the
Telegram bot id in the webhook URL) or the platform's own field (WhatsApp
``metadata.phone_number_id``), and the reply goes out with the binding's
outbound token.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from app.chat.service import ChatService
from app.gateway.channel_registry import ChannelRegistry
from app.gateway.router import router as gateway_router
from app.identity import IdentityService
from app.providers.fake import FakeProvider
from tests.gateway.conftest import TG_SECRET, WA_SECRET, SignedClient

pytestmark = pytest.mark.usefixtures("signed_channels")

TENANT = "tenant-real"
TG_BOT = "7001234567"
TG_TOKEN = "7001234567:AAH-real-bot-token"
WA_PHONE_ID = "106540352242922"
WA_TOKEN = "EAAG-wa-token"


def _app(monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, AsyncMock, AsyncMock]:
    from app.gateway import router as gw

    app = FastAPI()
    app.include_router(gateway_router)
    identity = IdentityService()
    chat = ChatService(answer_generator=FakeProvider(responses=["On it."]))
    chat.attach_engine(identity_service=identity)
    reg = ChannelRegistry.from_env(
        f"telegram:{TG_BOT}:{TENANT}::{TG_SECRET};outbound_token={TG_TOKEN},"
        f"whatsapp:{WA_PHONE_ID}:{TENANT}::{WA_SECRET};outbound_token={WA_TOKEN}"
    )
    app.state.chat_service = chat
    app.state.channel_registry = reg
    tg_send = AsyncMock(return_value={"ok": True})
    wa_send = AsyncMock(return_value={"messages": [{"id": "wamid.1"}]})
    monkeypatch.setattr(gw._telegram, "send_text", tg_send)
    monkeypatch.setattr(gw._whatsapp, "send_text", wa_send)
    return app, tg_send, wa_send


def _telegram_update(chat_id: int, text: str) -> dict[str, Any]:
    """A real Bot API Update — note: no bot id / addressee anywhere in it."""
    return {
        "update_id": 881234567,
        "message": {
            "message_id": 42,
            "from": {"id": chat_id, "is_bot": False, "first_name": "Ada", "language_code": "en"},
            "chat": {"id": chat_id, "first_name": "Ada", "type": "private"},
            "date": 1727690000,
            "text": text,
        },
    }


def _whatsapp_cloud(from_number: str, text: str) -> dict[str, Any]:
    """A real WhatsApp Cloud API webhook — the addressee is metadata.phone_number_id."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "102290129340398",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {
                        "display_phone_number": "15550783881",
                        "phone_number_id": WA_PHONE_ID,
                    },
                    "contacts": [{"profile": {"name": "Ada"}, "wa_id": from_number}],
                    "messages": [{
                        "from": from_number, "id": "wamid.HBgLMTY1MDM4Nzk0MzkVAgASGBQzQTRBNjU",
                        "timestamp": "1727690000", "type": "text", "text": {"body": text},
                    }],
                },
            }],
        }],
    }


def test_from_env_parses_outbound_token_and_keeps_colon_secrets() -> None:
    reg = ChannelRegistry.from_env(
        f"telegram:{TG_BOT}:t1:org1:s:e:c;outbound_token={TG_TOKEN}, whatsapp:155:t2"
    )
    b = reg.resolve("telegram", TG_BOT)
    assert b is not None
    assert (b.secret, b.outbound_token, b.org_id) == ("s:e:c", TG_TOKEN, "org1")
    assert reg.resolve("whatsapp", "155").outbound_token == ""  # type: ignore[union-attr]


def test_real_telegram_update_resolves_binding_from_url_and_replies(monkeypatch) -> None:
    app, tg_send, _ = _app(monkeypatch)
    r = SignedClient(app).post(
        f"/v1/gateway/telegram/chat/{TG_BOT}", json=_telegram_update(5550001, "what can you do?")
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and body["reply_sent"] is True
    tg_send.assert_awaited_once()
    kwargs = tg_send.await_args.kwargs
    assert kwargs["token"] == TG_TOKEN
    assert str(kwargs["chat_id"]) == "5550001"
    assert kwargs["text"] == body["reply"]


def test_real_telegram_update_without_binding_id_is_not_routed(monkeypatch) -> None:
    app, tg_send, _ = _app(monkeypatch)
    r = SignedClient(app).post("/v1/gateway/telegram/chat", json=_telegram_update(1, "hi"))
    assert r.status_code in (200, 401, 403, 503)
    assert r.json().get("status") != "ok"
    tg_send.assert_not_awaited()


def test_real_whatsapp_payload_resolves_binding_from_phone_number_id(monkeypatch) -> None:
    app, _, wa_send = _app(monkeypatch)
    r = SignedClient(app).post(
        "/v1/gateway/whatsapp/chat", json=_whatsapp_cloud("16505551234", "hello")
    )
    assert r.status_code == 200, r.text
    assert r.json()["reply_sent"] is True
    wa_send.assert_awaited_once()
    kwargs = wa_send.await_args.kwargs
    assert kwargs["token"] == WA_TOKEN
    assert kwargs["phone_number_id"] == WA_PHONE_ID
    assert kwargs["to"] == "16505551234"
