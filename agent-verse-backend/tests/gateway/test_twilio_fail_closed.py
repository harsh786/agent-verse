"""Regression: Twilio webhooks fail CLOSED and outbound SMS/voice is consent-gated.

Old bugs:
* ``VoicePhoneChannelAdapter.verify_auth`` returned True when no auth token was
  configured, so ``POST /v1/gateway/voice/incoming`` accepted forged calls into
  any tenant whose number is registered.
* ``POST /channels/sms/inbound`` never checked ``X-Twilio-Signature`` at all, so
  any API-key holder could inject an "SMS" into whichever tenant owns the ``To``
  number.
* No STOP / START handling existed, and outbound SMS / calls were sent to any
  number with no consent check.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.channels.ingestion import router as ingestion_router
from app.chat.service import ChatService
from app.gateway.channels.voice_phone import VoicePhoneChannelAdapter
from app.gateway.router import router as gateway_router
from app.gateway.telephony_consent import (
    TelephonyConsentError,
    TelephonyConsentLedger,
)
from app.gateway.voice_registry import VoicePhoneRegistry
from app.identity import IdentityService

TOKEN = "twilio-test-token"  # test fixture, not a real credential
LINE = "+15550001111"
CALLER = "+15559998888"


def _sign(token: str, url: str, params: dict[str, str]) -> str:
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    return base64.b64encode(
        hmac.new(token.encode(), payload.encode(), hashlib.sha1).digest()
    ).decode()


# ── Voice webhook ─────────────────────────────────────────────────────────────


def _voice_app(adapter: VoicePhoneChannelAdapter) -> FastAPI:
    app = FastAPI()
    app.include_router(gateway_router)
    chat = ChatService()
    chat.attach_engine(identity_service=IdentityService())
    reg = VoicePhoneRegistry()
    reg.register(LINE, "tenant-voice")
    app.state.chat_service = chat
    app.state.voice_phone_registry = reg
    app.state.voice_phone_adapter = adapter
    return app


_VOICE_FORM = {"From": CALLER, "To": LINE, "CallSid": "CA1", "SpeechResult": "hello"}


def test_voice_webhook_unconfigured_token_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOICE_PHONE_AUTH_TOKEN", raising=False)
    client = TestClient(_voice_app(VoicePhoneChannelAdapter(auth_token="")))
    r = client.post("/v1/gateway/voice/incoming", data=_VOICE_FORM)
    assert r.status_code == 503


def test_voice_webhook_invalid_signature_is_403() -> None:
    client = TestClient(_voice_app(VoicePhoneChannelAdapter(auth_token=TOKEN)))
    r = client.post(
        "/v1/gateway/voice/incoming",
        data=_VOICE_FORM,
        headers={"X-Twilio-Signature": "forged"},
    )
    assert r.status_code == 403


def test_voice_webhook_verification_error_is_403() -> None:
    adapter = VoicePhoneChannelAdapter(auth_token=TOKEN)
    adapter.verify_auth = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    client = TestClient(_voice_app(adapter))
    r = client.post("/v1/gateway/voice/incoming", data=_VOICE_FORM)
    assert r.status_code == 403


def test_voice_webhook_valid_signature_is_accepted() -> None:
    client = TestClient(_voice_app(VoicePhoneChannelAdapter(auth_token=TOKEN)))
    url = "http://testserver/v1/gateway/voice/incoming"
    r = client.post(
        "/v1/gateway/voice/incoming",
        data=_VOICE_FORM,
        headers={"X-Twilio-Signature": _sign(TOKEN, url, _VOICE_FORM)},
    )
    assert r.status_code == 200
    assert "<Say>" in r.text


async def test_adapter_verify_auth_without_token_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VOICE_PHONE_AUTH_TOKEN", raising=False)
    adapter = VoicePhoneChannelAdapter(auth_token="")
    assert adapter.is_configured is False
    assert await adapter.verify_auth({}, {"From": CALLER}) is False


# ── SMS webhook ───────────────────────────────────────────────────────────────


def _sms_app(ledger: TelephonyConsentLedger) -> tuple[FastAPI, AsyncMock]:
    app = FastAPI()
    app.include_router(ingestion_router)
    gateway = AsyncMock()
    app.state.channel_gateway = gateway
    app.state.trigger_event_redis = None
    app.state.telephony_consent_ledger = ledger

    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = False
    result = MagicMock()
    result.fetchone.return_value = ("tenant-sms",)
    session.execute.return_value = result
    app.state.db = MagicMock(return_value=session)

    @app.middleware("http")
    async def inject_tenant(req: Any, call_next: Any) -> Any:
        req.state.tenant = SimpleNamespace(tenant_id="some-other-tenant", plan="free")
        return await call_next(req)

    return app, gateway


_SMS_URL = "http://testserver/channels/sms/inbound"


def _sms_form(body: str) -> dict[str, str]:
    return {"To": LINE, "From": CALLER, "Body": body, "MessageSid": "SM1"}


def test_sms_webhook_unconfigured_token_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    app, gateway = _sms_app(TelephonyConsentLedger())
    r = TestClient(app).post("/channels/sms/inbound", data=_sms_form("hello"))
    assert r.status_code == 503
    gateway.ingest.assert_not_awaited()


def test_sms_webhook_missing_or_forged_signature_is_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    app, gateway = _sms_app(TelephonyConsentLedger())
    client = TestClient(app)
    assert client.post("/channels/sms/inbound", data=_sms_form("hi")).status_code == 403
    r = client.post(
        "/channels/sms/inbound",
        data=_sms_form("hi"),
        headers={"X-Twilio-Signature": "forged"},
    )
    assert r.status_code == 403
    gateway.ingest.assert_not_awaited()


def test_sms_webhook_valid_signature_ingests_and_returns_xml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    app, gateway = _sms_app(TelephonyConsentLedger())
    form = _sms_form("hello")
    r = TestClient(app).post(
        "/channels/sms/inbound",
        data=form,
        headers={"X-Twilio-Signature": _sign(TOKEN, _SMS_URL, form)},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert r.text.startswith("<?xml")
    gateway.ingest.assert_awaited_once()
    assert gateway.ingest.call_args.kwargs["tenant_id"] == "tenant-sms"


def test_sms_stop_records_opt_out_and_start_opts_back_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    ledger = TelephonyConsentLedger()
    ledger.record("tenant-sms", CALLER, granted=True, source="test")
    app, gateway = _sms_app(ledger)
    client = TestClient(app)

    stop = _sms_form("  Stop ")
    r = client.post(
        "/channels/sms/inbound",
        data=stop,
        headers={"X-Twilio-Signature": _sign(TOKEN, _SMS_URL, stop)},
    )
    assert r.status_code == 200
    assert ledger.allows_outbound("tenant-sms", CALLER) is False
    # An opt-out keyword is a compliance signal, not a message for the agent.
    gateway.ingest.assert_not_awaited()

    start = _sms_form("START")
    client.post(
        "/channels/sms/inbound",
        data=start,
        headers={"X-Twilio-Signature": _sign(TOKEN, _SMS_URL, start)},
    )
    assert ledger.allows_outbound("tenant-sms", CALLER) is True


# ── Outbound consent (default deny) ───────────────────────────────────────────


def test_ledger_default_denies_unknown_number() -> None:
    ledger = TelephonyConsentLedger()
    assert ledger.allows_outbound("t1", CALLER) is False
    ledger.record("t1", CALLER, granted=True, source="web_form")
    assert ledger.allows_outbound("t1", CALLER) is True
    # Consent is per tenant — another tenant's opt-in does not carry over.
    assert ledger.allows_outbound("t2", CALLER) is False
    # Formatting / whatsapp: prefix do not create a distinct identity.
    assert ledger.allows_outbound("t1", "whatsapp:+1 (555) 999-8888") is True
    ledger.record("t1", CALLER, granted=False, source="sms_keyword")
    assert ledger.allows_outbound("t1", CALLER) is False


class _Calls:
    def __init__(self) -> None:
        self.created: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> dict[str, Any]:
        self.created = kwargs
        return {"sid": "CA-out", "status": "queued"}


async def test_place_call_refuses_number_without_consent() -> None:
    ledger = TelephonyConsentLedger()
    client = SimpleNamespace(calls=_Calls())
    adapter = VoicePhoneChannelAdapter(default_from="+15550000000")
    with pytest.raises(TelephonyConsentError):
        await adapter.place_call(
            to=CALLER, client=client, twiml="<Response/>", tenant_id="t1",
            consent_ledger=ledger,
        )
    assert client.calls.created is None

    ledger.record("t1", CALLER, granted=True, source="test")
    result = await adapter.place_call(
        to=CALLER, client=client, twiml="<Response/>", tenant_id="t1", consent_ledger=ledger
    )
    assert result is not None and result["sid"] == "CA-out"


@pytest.mark.parametrize("tool", ["twilio_send_sms", "twilio_send_whatsapp", "twilio_make_call"])
async def test_twilio_mcp_outbound_requires_consent(
    tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.gateway import telephony_consent
    from app.mcp.servers import twilio_server

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    ledger = TelephonyConsentLedger()
    monkeypatch.setattr(telephony_consent, "_DEFAULT_LEDGER", ledger)

    args = {"to": CALLER, "body": "hi", "twiml": "<Response/>", "from_number": "+15550000000"}
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
        result = await twilio_server.call_tool(tool, args)
    assert "error" in result and "consent" in result["error"].lower()
    post.assert_not_awaited()
