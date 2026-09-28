"""Regression: /v1/gateway/{channel}/chat must not let the body pick the tenant.

The addressee (bot id / number) is read from the unauthenticated body. It used to
select the tenant after the request was verified against ONE platform-wide
channel secret — so anyone holding that secret (every tenant that set up a bot)
could address any other tenant's bot and act as that tenant. Now the addressee
only selects a binding, and the request must be signed with THAT binding's
per-tenant secret.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway import router as gw
from app.gateway.channel_registry import ChannelRegistry
from tests.gateway.conftest import TG_SECRET, WEBHOOK_SECRET


class _Chat:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def achannel_turn(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        return {"session_id": "s1", "reply": "ok", "actions": []}


@pytest.fixture
def setup(signed_channels: None) -> tuple[TestClient, _Chat]:
    reg = ChannelRegistry()
    reg.register("telegram", "bot-a", "tenant-a", secret="secret-a")
    reg.register("telegram", "bot-b", "tenant-b", secret="secret-b")
    reg.register("telegram", "bot-nosecret", "tenant-c")
    reg.register("webhook", "hook-b", "tenant-b", secret="hook-secret-b")
    chat = _Chat()
    app = FastAPI()
    app.include_router(gw.router)
    app.state.chat_service = chat
    app.state.channel_registry = reg
    return TestClient(app), chat


def _tg(addressee: str) -> dict[str, Any]:
    return {
        "addressee": addressee,
        "message": {"from": {"id": "7"}, "chat": {"id": "7"}, "text": "delete everything"},
    }


def test_platform_secret_cannot_address_another_tenants_bot(
    setup: tuple[TestClient, _Chat],
) -> None:
    client, chat = setup
    r = client.post(
        "/v1/gateway/telegram/chat",
        json=_tg("bot-b"),
        headers={"x-telegram-bot-api-secret-token": TG_SECRET},
    )
    assert r.status_code == 401
    assert chat.calls == []


def test_other_tenants_binding_secret_is_rejected(setup: tuple[TestClient, _Chat]) -> None:
    client, chat = setup
    r = client.post(
        "/v1/gateway/telegram/chat",
        json=_tg("bot-b"),
        headers={"x-telegram-bot-api-secret-token": "secret-a"},
    )
    assert r.status_code == 401
    assert chat.calls == []


def test_binding_secret_authenticates_its_own_tenant(setup: tuple[TestClient, _Chat]) -> None:
    client, chat = setup
    r = client.post(
        "/v1/gateway/telegram/chat",
        json=_tg("bot-b"),
        headers={"x-telegram-bot-api-secret-token": "secret-b"},
    )
    assert r.status_code == 200, r.text
    assert [c["tenant_id"] for c in chat.calls] == ["tenant-b"]


def test_binding_without_secret_fails_closed(setup: tuple[TestClient, _Chat]) -> None:
    client, chat = setup
    r = client.post(
        "/v1/gateway/telegram/chat",
        json=_tg("bot-nosecret"),
        headers={"x-telegram-bot-api-secret-token": TG_SECRET},
    )
    assert r.status_code == 503
    assert chat.calls == []


def test_webhook_binding_is_hmac_signed_with_binding_secret(
    setup: tuple[TestClient, _Chat],
) -> None:
    client, chat = setup
    body = json.dumps({"addressee": "hook-b", "text": "hi"}).encode()

    def _sig(key: str) -> str:
        return "sha256=" + hmac.new(key.encode(), body, hashlib.sha256).hexdigest()

    platform = client.post(
        "/v1/gateway/webhook/chat",
        content=body,
        headers={"content-type": "application/json", "x-webhook-signature": _sig(WEBHOOK_SECRET)},
    )
    assert platform.status_code == 401
    own = client.post(
        "/v1/gateway/webhook/chat",
        content=body,
        headers={"content-type": "application/json", "x-webhook-signature": _sig("hook-secret-b")},
    )
    assert own.status_code == 200, own.text
    assert [c["tenant_id"] for c in chat.calls] == ["tenant-b"]


def test_from_env_reads_binding_secret() -> None:
    reg = ChannelRegistry.from_env("telegram:bot-1:t1:org1:s:e:c, whatsapp:155:t2")
    b1 = reg.resolve("telegram", "bot-1")
    b2 = reg.resolve("whatsapp", "155")
    assert b1 is not None and b1.secret == "s:e:c" and b1.org_id == "org1"
    assert b2 is not None and b2.secret == ""
