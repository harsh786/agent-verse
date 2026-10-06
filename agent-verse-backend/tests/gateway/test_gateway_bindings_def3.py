"""DEF-3: self-service gateway bindings are verified by the platform and sealed
per tenant.

* WhatsApp: Meta's subscription handshake (``GET`` with ``hub.mode`` /
  ``hub.verify_token`` / ``hub.challenge``) must echo the binding's own
  server-generated verify token — another binding's token, a wrong token or an
  unknown binding is 403.
* Telegram: ``setWebhook`` registers the binding URL with its ``secret_token``.
* Binding secrets are sealed with the tenant's envelope key and re-sealed by
  ``tenant-key-compact``.
(The DB/RLS/multi-replica flow runs on real Postgres + Redis in
test_gateway_bindings_integration.py.)
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway import router as gw
from app.gateway.binding_verification import (
    BindingOwnershipError,
    public_base_url,
    register_telegram_webhook,
)
from app.gateway.channel_registry import ChannelRegistry

_VT_A = "-".join(("verify", "token", "tenant", "a"))
_VT_B = "-".join(("verify", "token", "tenant", "b"))
_TG_TOKEN = "123456789:" + "B" * 35


def _client() -> TestClient:
    reg = ChannelRegistry()
    reg.register("whatsapp", "1098765432", "tenant-a", secret="s-a", verify_token=_VT_A)
    reg.register("whatsapp", "2098765432", "tenant-b", secret="s-b", verify_token=_VT_B)
    reg.register("whatsapp", "3098765432", "tenant-c", secret="s-c")  # no verify token
    app = FastAPI()
    app.include_router(gw.router)
    app.state.chat_service = object()
    app.state.channel_registry = reg
    return TestClient(app)


def _verify(client: TestClient, binding: str, token: str, *, mode: str = "subscribe") -> Any:
    return client.get(
        f"/v1/gateway/whatsapp/chat/{binding}",
        params={"hub.mode": mode, "hub.verify_token": token, "hub.challenge": "1158201444"},
    )


def test_whatsapp_handshake_echoes_the_challenge_for_the_bindings_own_token() -> None:
    resp = _verify(_client(), "1098765432", _VT_A)
    assert resp.status_code == 200
    assert resp.text == "1158201444"


@pytest.mark.parametrize(
    ("binding", "token", "mode"),
    [
        ("1098765432", _VT_B, "subscribe"),  # another tenant's verify token
        ("1098765432", "guess", "subscribe"),
        ("1098765432", _VT_A, "unsubscribe"),
        ("3098765432", "", "subscribe"),  # binding without a verify token
        ("9999999999", _VT_A, "subscribe"),  # unknown binding
    ],
)
def test_whatsapp_handshake_refusals(binding: str, token: str, mode: str) -> None:
    assert _verify(_client(), binding, token, mode=mode).status_code == 403


def test_get_verification_is_whatsapp_only() -> None:
    resp = _client().get("/v1/gateway/telegram/chat/123", params={"hub.mode": "subscribe"})
    assert resp.status_code == 405


class _Resp:
    def __init__(self, status: int, data: dict[str, Any]) -> None:
        self.status_code = status
        self._data = data

    def json(self) -> dict[str, Any]:
        return self._data


class _Http:
    def __init__(self, status: int, data: dict[str, Any]) -> None:
        self.resp = _Resp(status, data)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def post(self, url: str, **kw: Any) -> _Resp:
        self.calls.append((url, kw.get("json") or {}))
        return self.resp


@pytest.mark.asyncio
async def test_telegram_set_webhook_registers_url_and_secret_token() -> None:
    http = _Http(200, {"ok": True, "result": True, "description": "Webhook was set"})
    url = "https://agents.example.com/v1/gateway/telegram/chat/123456789"
    await register_telegram_webhook(_TG_TOKEN, url, "tg_secret-1", http=http)
    assert http.calls == [
        (
            f"https://api.telegram.org/bot{_TG_TOKEN}/setWebhook",
            {
                "url": url,
                "secret_token": "tg_secret-1",
                "allowed_updates": ["message", "edited_message"],
            },
        )
    ]


@pytest.mark.asyncio
async def test_telegram_set_webhook_refusal_raises() -> None:
    http = _Http(400, {"ok": False, "description": "Bad Request: bad webhook: HTTPS url must be provided"})
    with pytest.raises(BindingOwnershipError, match="HTTPS url"):
        await register_telegram_webhook(_TG_TOKEN, "https://x/y", "tg", http=http)


@pytest.mark.asyncio
async def test_telegram_secret_token_outside_the_alphabet_is_refused_before_a_call() -> None:
    http = _Http(200, {"ok": True, "result": True})
    with pytest.raises(BindingOwnershipError):
        await register_telegram_webhook(_TG_TOKEN, "https://x/y", "has space", http=http)
    assert http.calls == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://agents.example.com/", "https://agents.example.com"),
        ("https://agents.example.com/api", "https://agents.example.com/api"),
        ("http://agents.example.com", ""),
        ("https://agents.example.com/?x=1", ""),
        ("", ""),
    ],
)
def test_public_base_url_is_https_only(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
) -> None:
    monkeypatch.setenv("GATEWAY_PUBLIC_BASE_URL", raw)
    assert public_base_url() == expected


# ── per-tenant envelope sealing ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_binding_secrets_are_sealed_and_opened_with_the_tenant_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.gateway import binding_store
    from app.providers import tenant_vault
    from app.providers.tenant_vault import TENANT_CIPHER_PREFIX
    from app.providers.vault import CredentialVault

    tenant_key = CredentialVault.from_byok(bytes(range(32)))

    async def _ensure(db: Any, tenant_id: str, *, refresh: bool = False) -> Any:
        return tenant_key if tenant_id == "tenant-a" else None

    monkeypatch.setattr(tenant_vault, "ensure_tenant_vault", _ensure)
    sealed = await binding_store.seal_binding_secret(object(), "tenant-a", "hmac-key")
    assert sealed.startswith(TENANT_CIPHER_PREFIX)
    platform = await binding_store.seal_binding_secret(object(), "tenant-b", "hmac-key")
    assert not platform.startswith(TENANT_CIPHER_PREFIX)
    assert await binding_store.seal_binding_secret(object(), "tenant-a", "") == ""

    opened = await binding_store._open_sealed(
        object(), "tenant-a", {"secret_enc": sealed, "verify_token_enc": "", "x": 1}
    )
    assert opened == {"secret_enc": "hmac-key", "outbound_token_enc": "", "verify_token_enc": ""}

    # Another tenant (no key / another key) cannot open tenant A's sealed value.
    with pytest.raises(tenant_vault.TenantVaultError):
        await binding_store._open_sealed(object(), "tenant-b", {"secret_enc": sealed})


def test_tenant_key_compaction_reseals_binding_secrets() -> None:
    from app.providers import tenant_key_compaction as tkc
    from app.providers.tenant_vault import TENANT_CIPHER_PREFIX
    from app.providers.vault import CredentialVault

    new, old = bytes(range(32)), bytes(range(40, 72))
    assert any(store[1] == "channel_tenant_mappings" for store in tkc._PG_STORES)
    sealer = tkc._Sealer([new, old], tkc.TenantCompaction("t"))
    cfg = {
        "gateway": True,
        "app_id": "",
        "secret_enc": TENANT_CIPHER_PREFIX + CredentialVault.from_byok(old).encrypt("s"),
        "outbound_token_enc": "",
    }
    out = sealer.reseal_source(json.loads(json.dumps(cfg)))
    assert out is not None
    body = out["secret_enc"][len(TENANT_CIPHER_PREFIX) :]
    assert CredentialVault.from_byok(new).decrypt(body) == "s"
    assert sealer.reseal_source(out) is None


def test_channel_tenant_map_env_is_deprecated_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("CHANNEL_TENANT_MAP", "telegram:123:tenant-x::s")
    with caplog.at_level("WARNING"):
        reg = ChannelRegistry.from_env()
    assert reg.resolve("telegram", "123") is not None  # still honoured as a fallback
    assert any("CHANNEL_TENANT_MAP is deprecated" in r.getMessage() for r in caplog.records)
