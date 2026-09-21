"""Router-level contract tests for app/gateway/router.py.

These exercise the actual FastAPI endpoints (auth gating, background-task
scheduling, response shape) for every channel webhook, plus the deeper
``_process_command`` paths (untrusted-tenant no-op, trusted-tenant goal
submission + file ingestion + reply) and the unified ``channel_chat`` /
``voice_incoming`` branches that the existing e2e tests don't reach:
unknown channel, bad signature, no-text short-circuit, and the outbound-reply
send path.

Endpoint tests that would otherwise trigger the real background task (goal
submission, LLM calls, etc.) monkeypatch ``gw._process_command`` with an
``AsyncMock`` so we assert routing/auth/scheduling behavior in isolation —
``_process_command`` itself is covered directly further down.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway import router as gw
from app.gateway.channel_registry import ChannelRegistry
from app.gateway.command import CommandFile, OrgCommand


def _bare_app() -> FastAPI:
    app = FastAPI()
    app.include_router(gw.router)
    return app


def _client() -> TestClient:
    return TestClient(_bare_app())


# ── Telegram ──────────────────────────────────────────────────────────────────


class TestTelegramWebhook:
    def test_rejects_invalid_secret_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "correct-secret")
        client = _client()
        r = client.post(
            "/v1/gateway/org1/telegram/webhook",
            json={"message": {"chat": {"id": "1"}, "from": {"id": "2"}, "text": "hi"}},
            headers={"x-telegram-bot-api-secret-token": "wrong"},
        )
        assert r.status_code == 403

    def test_schedules_processing_when_text_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
        mock = AsyncMock()
        monkeypatch.setattr(gw, "_process_command", mock)
        client = _client()
        r = client.post(
            "/v1/gateway/org1/telegram/webhook",
            json={
                "message": {
                    "chat": {"id": "42"},
                    "from": {"id": "7", "first_name": "Bob"},
                    "text": "do the thing",
                }
            },
        )
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
        mock.assert_awaited_once()
        (command,), _ = mock.await_args
        assert command.text == "do the thing"
        assert command.actor_channel == "telegram"

    def test_skips_processing_when_normalize_yields_no_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
        mock = AsyncMock()
        monkeypatch.setattr(gw, "_process_command", mock)
        client = _client()
        # Neither "message" nor "callback_query" → normalize's fallback branch,
        # which yields an empty-text OrgCommand — no background work scheduled.
        r = client.post("/v1/gateway/org1/telegram/webhook", json={})
        assert r.status_code == 200
        mock.assert_not_awaited()


# ── Slack ─────────────────────────────────────────────────────────────────────


class TestSlackEvents:
    def test_verify_challenge_get(self) -> None:
        client = _client()
        r = client.get("/v1/gateway/org1/slack/events", params={"challenge": "abc123"})
        assert r.status_code == 200
        assert r.json() == {"challenge": "abc123"}

    def test_url_verification_short_circuits_before_auth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Even with a signing secret configured (which would otherwise 403 an
        # unsigned request), the url_verification challenge is answered first.
        monkeypatch.setenv("SLACK_SIGNING_SECRET", "shh")
        client = _client()
        r = client.post(
            "/v1/gateway/org1/slack/events",
            json={"type": "url_verification", "challenge": "xyz"},
        )
        assert r.status_code == 200
        assert r.json() == {"challenge": "xyz"}

    def test_rejects_invalid_signature(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The adapter singleton reads its secret once at construction time, so
        # patch the instance attribute directly (env-only would be a no-op here).
        monkeypatch.setattr(gw._slack, "_signing_secret", "shh")
        client = _client()
        r = client.post(
            "/v1/gateway/org1/slack/events",
            json={"command": "/goal", "text": "ship it", "user_id": "u1", "channel_id": "c1"},
            headers={"x-slack-request-timestamp": "1", "x-slack-signature": "v0=bad"},
        )
        assert r.status_code == 403

    def test_schedules_processing_for_slash_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
        mock = AsyncMock()
        monkeypatch.setattr(gw, "_process_command", mock)
        client = _client()
        r = client.post(
            "/v1/gateway/org1/slack/events",
            json={"command": "/goal", "text": "ship it", "user_id": "u1", "channel_id": "c1"},
        )
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
        mock.assert_awaited_once()
        (command,), _ = mock.await_args
        assert command.text == "/goal ship it"

    def test_skips_processing_when_no_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
        mock = AsyncMock()
        monkeypatch.setattr(gw, "_process_command", mock)
        client = _client()
        r = client.post(
            "/v1/gateway/org1/slack/events",
            json={"type": "event_callback", "event": {"type": "reaction_added"}},
        )
        assert r.status_code == 200
        mock.assert_not_awaited()


# ── WhatsApp ──────────────────────────────────────────────────────────────────


class TestWhatsAppWebhook:
    def test_verify_success_echoes_challenge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify-me")
        client = _client()
        r = client.get(
            "/v1/gateway/org1/whatsapp/webhook",
            params={"hub_mode": "subscribe", "hub_verify_token": "verify-me", "hub_challenge": "555"},
        )
        assert r.status_code == 200
        assert r.json() == 555

    def test_verify_failure_wrong_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify-me")
        client = _client()
        r = client.get(
            "/v1/gateway/org1/whatsapp/webhook",
            params={"hub_mode": "subscribe", "hub_verify_token": "wrong", "hub_challenge": "555"},
        )
        assert r.status_code == 403

    def test_rejects_invalid_signature(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gw._whatsapp, "_app_secret", "shh")
        client = _client()
        r = client.post(
            "/v1/gateway/org1/whatsapp/webhook",
            json={"entry": []},
            headers={"x-hub-signature-256": "sha256=bad"},
        )
        assert r.status_code == 403

    def test_schedules_processing_for_valid_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("WHATSAPP_APP_SECRET", raising=False)
        mock = AsyncMock()
        monkeypatch.setattr(gw, "_process_command", mock)
        client = _client()
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "contacts": [{"profile": {"name": "Ada"}}],
                                "messages": [
                                    {"from": "1555", "type": "text", "text": {"body": "hello"}}
                                ],
                            }
                        }
                    ]
                }
            ]
        }
        r = client.post("/v1/gateway/org1/whatsapp/webhook", json=payload)
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}
        mock.assert_awaited_once()


# ── Microsoft Teams ───────────────────────────────────────────────────────────


class TestTeamsMessages:
    def test_rejects_missing_auth_header(self) -> None:
        client = _client()
        r = client.post(
            "/v1/gateway/org1/teams/messages",
            json={"type": "message", "text": "hi", "from": {}, "conversation": {}},
        )
        assert r.status_code == 403

    def test_schedules_processing_for_valid_auth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = AsyncMock()
        monkeypatch.setattr(gw, "_process_command", mock)
        client = _client()
        r = client.post(
            "/v1/gateway/org1/teams/messages",
            json={
                "type": "message",
                "text": "do the thing",
                "from": {"id": "u1", "name": "Ada"},
                "conversation": {"id": "conv1"},
            },
            headers={"authorization": "Bearer a-fairly-long-fake-jwt-token"},
        )
        assert r.status_code == 200
        assert r.json() == {"type": "message", "text": ""}
        mock.assert_awaited_once()

    def test_skips_processing_for_non_message_activity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock = AsyncMock()
        monkeypatch.setattr(gw, "_process_command", mock)
        client = _client()
        r = client.post(
            "/v1/gateway/org1/teams/messages",
            json={"type": "conversationUpdate"},
            headers={"authorization": "Bearer a-fairly-long-fake-jwt-token"},
        )
        assert r.status_code == 200
        mock.assert_not_awaited()


# ── Generic webhook ───────────────────────────────────────────────────────────


class TestGenericWebhook:
    def test_rejects_invalid_signature(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gw._webhook, "_secret", "shh")
        client = _client()
        r = client.post(
            "/v1/gateway/org1/webhook",
            json={"command": "do it"},
            headers={"x-webhook-signature": "sha256=bad"},
        )
        assert r.status_code == 403

    def test_valid_signature_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gw._webhook, "_secret", "shh")
        mock = AsyncMock()
        monkeypatch.setattr(gw, "_process_command", mock)
        client = _client()
        payload = {"command": "do it"}
        body = json.dumps(payload, separators=(",", ":")).encode()
        sig = "sha256=" + hmac.new(b"shh", body, hashlib.sha256).hexdigest()
        r = client.post(
            "/v1/gateway/org1/webhook", json=payload, headers={"x-webhook-signature": sig}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "queued"
        assert body["command_id"]
        mock.assert_awaited_once()

    def test_always_schedules_even_without_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unlike the chat channels, the generic webhook queues unconditionally —
        even a trigger-only payload with no natural text is turned into one via
        ``_infer_command_from_trigger`` and processed."""
        monkeypatch.setattr(gw._webhook, "_secret", "")
        mock = AsyncMock()
        monkeypatch.setattr(gw, "_process_command", mock)
        client = _client()
        r = client.post("/v1/gateway/org1/webhook", json={"trigger": "alert.critical"})
        assert r.status_code == 200
        assert r.json()["status"] == "queued"
        mock.assert_awaited_once()
        (command,), _ = mock.await_args
        assert "critical" in command.text.lower()


# ── Config & admin endpoints ──────────────────────────────────────────────────


class TestGatewayConfigAndStatus:
    def test_get_config_returns_defaults(self) -> None:
        client = _client()
        r = client.get("/v1/gateway/org1/config")
        assert r.status_code == 200
        body = r.json()
        assert body["telegram_enabled"] is False
        assert body["webhook_enabled"] is True
        assert body["max_commands_per_hour"] == 100

    def test_update_config_echoes_payload(self) -> None:
        client = _client()
        payload = {
            "telegram_enabled": True,
            "slack_enabled": False,
            "whatsapp_enabled": False,
            "teams_enabled": False,
            "discord_enabled": False,
            "email_enabled": False,
            "mcp_enabled": True,
            "webhook_enabled": True,
            "max_commands_per_hour": 250,
        }
        r = client.put("/v1/gateway/org1/config", json=payload)
        assert r.status_code == 200
        assert r.json() == payload

    def test_channel_status_lists_all_channels(self) -> None:
        client = _client()
        r = client.get("/v1/gateway/acme/channels/status")
        assert r.status_code == 200
        body = r.json()
        assert body["org_id"] == "acme"
        names = {c["name"] for c in body["channels"]}
        assert {"rest", "telegram", "slack", "teams", "discord", "whatsapp", "email",
                "mcp", "webhook"}.issubset(names)
        rest = next(c for c in body["channels"] if c["name"] == "rest")
        assert rest["enabled"] is True
        assert rest["endpoint"] == "/v1/org/acme/command"


# ── channel_chat: branches the e2e happy-path tests don't reach ──────────────


class TestChannelChatBranches:
    def _app_with_state(self, **state: object) -> FastAPI:
        app = FastAPI()
        app.include_router(gw.router)
        for k, v in state.items():
            setattr(app.state, k, v)
        return app

    def test_unknown_channel_returns_404(self) -> None:
        app = self._app_with_state(chat_service=object())
        client = TestClient(app)
        r = client.post("/v1/gateway/discord/chat", json={})
        assert r.status_code == 404

    def test_missing_chat_service_returns_404(self) -> None:
        app = self._app_with_state()
        client = TestClient(app)
        r = client.post("/v1/gateway/telegram/chat", json={})
        assert r.status_code == 404

    async def test_invalid_signature_returns_403(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gw._webhook, "_secret", "shh")
        app = self._app_with_state(chat_service=object())
        client = TestClient(app)
        r = client.post(
            "/v1/gateway/webhook/chat",
            json={"text": "hi"},
            headers={"x-webhook-signature": "sha256=bad"},
        )
        assert r.status_code == 403

    def test_no_tenant_mapping_is_ignored(self) -> None:
        app = self._app_with_state(chat_service=object(), channel_registry=ChannelRegistry())
        client = TestClient(app)
        r = client.post(
            "/v1/gateway/telegram/chat",
            json={"addressee": "unmapped-bot", "message": {"from": {"id": "1"}, "chat": {"id": "1"}}},
        )
        assert r.status_code == 200
        assert r.json() == {"status": "ignored", "reason": "no tenant mapping for addressee"}

    def test_no_text_in_message_is_acknowledged(self) -> None:
        reg = ChannelRegistry()
        reg.register("telegram", "bot-1", "tenant-a")
        app = self._app_with_state(chat_service=object(), channel_registry=reg)
        client = TestClient(app)
        # Message present but with neither "text" nor "caption" → normalize gives
        # a non-empty "(attachment)" text... use callback_query-free/message-free
        # payload to get a genuinely empty OrgCommand.text.
        r = client.post(
            "/v1/gateway/telegram/chat", json={"addressee": "bot-1", "unrelated": True}
        )
        assert r.status_code == 200
        assert r.json() == {"status": "ok", "reason": "no text in message"}

    async def test_outbound_reply_is_sent_when_token_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.chat.service import ChatService
        from app.identity import IdentityService
        from app.providers.fake import FakeProvider

        reg = ChannelRegistry()
        reg.register("telegram", "bot-1", "tenant-out", outbound_token="tok-123")
        chat = ChatService(answer_generator=FakeProvider(responses=["hi there"]))
        chat.attach_engine(identity_service=IdentityService())
        app = self._app_with_state(chat_service=chat, channel_registry=reg)

        sent_mock = AsyncMock(return_value=None)
        monkeypatch.setattr(gw._telegram, "send_message", sent_mock)

        client = TestClient(app)
        r = client.post(
            "/v1/gateway/telegram/chat",
            json={
                "addressee": "bot-1",
                "message": {"from": {"id": "9"}, "chat": {"id": "9"}, "text": "hello there"},
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["reply_sent"] is True
        sent_mock.assert_awaited_once()
        assert sent_mock.await_args.kwargs["token"] == "tok-123"

    def test_outbound_reply_not_sent_without_token(self) -> None:
        from app.chat.service import ChatService
        from app.identity import IdentityService
        from app.providers.fake import FakeProvider

        reg = ChannelRegistry()
        reg.register("telegram", "bot-1", "tenant-out")  # no outbound_token
        chat = ChatService(answer_generator=FakeProvider(responses=["hi there"]))
        chat.attach_engine(identity_service=IdentityService())
        app = self._app_with_state(chat_service=chat, channel_registry=reg)
        client = TestClient(app)
        r = client.post(
            "/v1/gateway/telegram/chat",
            json={
                "addressee": "bot-1",
                "message": {"from": {"id": "9"}, "chat": {"id": "9"}, "text": "hello there"},
            },
        )
        assert r.status_code == 200
        assert r.json()["reply_sent"] is False


# ── voice_incoming: exception fallback branch ─────────────────────────────────


class TestVoiceIncomingExceptionFallback:
    def _app(self) -> FastAPI:
        from app.chat.service import ChatService
        from app.gateway.channels.voice_phone import VoicePhoneChannelAdapter
        from app.gateway.voice_registry import VoicePhoneRegistry
        from app.identity import IdentityService

        app = FastAPI()
        app.include_router(gw.router)
        chat = ChatService()
        chat.attach_engine(identity_service=IdentityService())
        reg = VoicePhoneRegistry()
        reg.register("+15550001111", "tenant-voice")
        app.state.chat_service = chat
        app.state.voice_phone_registry = reg
        app.state.voice_phone_adapter = VoicePhoneChannelAdapter()
        return app

    def test_handler_exception_speaks_safe_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _boom(**kwargs: object) -> None:
            raise RuntimeError("downstream exploded")

        monkeypatch.setattr("app.voice.chat_bridge.handle_voice_turn", _boom)
        client = TestClient(self._app())
        r = client.post(
            "/v1/gateway/voice/incoming",
            data={"From": "+15559998888", "To": "+15550001111", "CallSid": "CAx",
                  "SpeechResult": "trigger the failure"},
        )
        assert r.status_code == 200
        assert "Sorry, I hit a problem" in r.text
        assert "<Gather" in r.text


# ── _process_command: untrusted vs trusted tenant, files + goal + reply ──────


TID = "tenant-proc"


def _cmd(**kw: object) -> OrgCommand:
    base: dict[str, object] = dict(
        command_id="cmd-1",
        tenant_id="",
        org_id="org1",
        text="do the thing",
        actor_channel="telegram",
        conversation_id="chat1",
    )
    base.update(kw)
    return OrgCommand(**base)  # type: ignore[arg-type]


class TestProcessCommandEndToEnd:
    async def test_untrusted_tenant_acknowledges_without_side_effects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        goal_service = SimpleNamespace(submit_goal=AsyncMock())
        from app.main import app as fastapi_app

        monkeypatch.setattr(fastapi_app.state, "goal_service", goal_service, raising=False)
        reply_mock = AsyncMock()
        monkeypatch.setattr(gw, "_reply_to_channel", reply_mock)

        resp = await gw._process_command(_cmd(tenant_id=""))

        assert resp.status == "accepted"
        assert resp.requires_action is False
        assert "No tenant binding" in resp.text
        goal_service.submit_goal.assert_not_awaited()
        reply_mock.assert_awaited_once()

    async def test_trusted_tenant_ingests_files_and_submits_goal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pipeline = SimpleNamespace(
            ingest=AsyncMock(return_value=SimpleNamespace(status="indexed"))
        )
        ks = SimpleNamespace(
            list_collections_async=AsyncMock(return_value=[]),
            create_collection_async=AsyncMock(return_value="coll-1"),
        )
        goal_service = SimpleNamespace(submit_goal=AsyncMock(return_value={"goal_id": "g-999"}))
        from app.main import app as fastapi_app

        monkeypatch.setattr(fastapi_app.state, "goal_service", goal_service, raising=False)
        monkeypatch.setattr(fastapi_app.state, "ingestion_pipeline", pipeline, raising=False)
        monkeypatch.setattr(fastapi_app.state, "knowledge_store", ks, raising=False)
        reply_mock = AsyncMock()
        monkeypatch.setattr(gw, "_reply_to_channel", reply_mock)

        cmd = _cmd(
            tenant_id=TID,
            text="summarize the quarter",
            files=[CommandFile(filename="a.txt", content_type="text/plain", data=b"hello")],
        )
        resp = await gw._process_command(cmd)

        assert resp.mission_id == "g-999"
        assert "Ingested 1 document" in resp.text
        assert "Started goal `g-999`" in resp.text
        goal_service.submit_goal.assert_awaited_once()
        assert goal_service.submit_goal.await_args.kwargs["goal"] == "summarize the quarter"
        reply_mock.assert_awaited_once()

    async def test_urgent_command_maps_to_high_priority(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        goal_service = SimpleNamespace(submit_goal=AsyncMock(return_value={"goal_id": "g-1"}))
        from app.main import app as fastapi_app

        monkeypatch.setattr(fastapi_app.state, "goal_service", goal_service, raising=False)
        monkeypatch.setattr(fastapi_app.state, "ingestion_pipeline", None, raising=False)
        monkeypatch.setattr(fastapi_app.state, "knowledge_store", None, raising=False)
        monkeypatch.setattr(gw, "_reply_to_channel", AsyncMock())

        cmd = _cmd(tenant_id=TID, text="respond now", urgency="urgent")
        await gw._process_command(cmd)

        assert goal_service.submit_goal.await_args.kwargs["priority"] == "high"

    async def test_callback_text_is_not_submitted_as_a_goal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``/callback ...`` text (a button press echoed back by the adapter)
        must never be forwarded to goal submission as if it were a user request."""
        goal_service = SimpleNamespace(submit_goal=AsyncMock())
        from app.main import app as fastapi_app

        monkeypatch.setattr(fastapi_app.state, "goal_service", goal_service, raising=False)
        monkeypatch.setattr(fastapi_app.state, "ingestion_pipeline", None, raising=False)
        monkeypatch.setattr(gw, "_reply_to_channel", AsyncMock())

        cmd = _cmd(tenant_id=TID, text="/callback approve:123")
        resp = await gw._process_command(cmd)

        goal_service.submit_goal.assert_not_awaited()
        assert resp.mission_id is None


class TestReplyToChannel:
    async def test_reply_failure_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _boom(*a: object, **kw: object) -> None:
            raise RuntimeError("network down")

        monkeypatch.setattr(gw._telegram, "send_message", _boom)
        cmd = _cmd(actor_channel="telegram", conversation_id="chat1")
        # Must not raise even though send_message blows up.
        await gw._reply_to_channel(cmd, "hi")

    async def test_unsupported_channel_is_a_no_op(self) -> None:
        cmd = _cmd(actor_channel="webhook", conversation_id="chat1")
        await gw._reply_to_channel(cmd, "hi")  # no sender for webhook — just returns

    async def test_whatsapp_channel_uses_whatsapp_sender(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock = AsyncMock(return_value=None)
        monkeypatch.setattr(gw._whatsapp, "send_message", mock)
        cmd = _cmd(actor_channel="whatsapp", conversation_id="+15551234567")
        await gw._reply_to_channel(cmd, "hi there")
        mock.assert_awaited_once()
        assert mock.await_args.args[0] == "+15551234567"


# ── Helper functions: file download / inbox collection / goal-submit errors ──


class TestDownloadCommandFile:
    async def test_returns_inline_data_directly(self) -> None:
        cf = CommandFile(filename="a.txt", content_type="text/plain", data=b"payload")
        assert await gw._download_command_file(cf) == b"payload"

    async def test_no_data_and_no_url_returns_none(self) -> None:
        cf = CommandFile(filename="a.txt", content_type="text/plain")
        assert await gw._download_command_file(cf) is None

    async def test_ssrf_blocked_url_returns_none(self) -> None:
        # Loopback/link-local URLs must be rejected by the SSRF guard before any
        # network call is attempted.
        cf = CommandFile(
            filename="a.txt", content_type="text/plain", url="http://169.254.169.254/secret"
        )
        assert await gw._download_command_file(cf) is None

    async def test_download_failure_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.net.ssrf_guard.assert_public_url", lambda *a, **kw: None
        )

        class _BoomClient:
            async def __aenter__(self) -> _BoomClient:
                return self

            async def __aexit__(self, *a: object) -> None:
                return None

            async def get(self, url: str) -> None:
                raise RuntimeError("connection refused")

        import httpx as _httpx

        monkeypatch.setattr(_httpx, "AsyncClient", lambda *a, **kw: _BoomClient())
        cf = CommandFile(
            filename="a.txt", content_type="text/plain", url="https://example.com/f.txt"
        )
        assert await gw._download_command_file(cf) is None

    async def test_download_success_returns_content(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.net.ssrf_guard.assert_public_url", lambda *a, **kw: None
        )

        class _FakeResponse:
            content = b"file-bytes"

            def raise_for_status(self) -> None:
                return None

        class _FakeClient:
            async def __aenter__(self) -> _FakeClient:
                return self

            async def __aexit__(self, *a: object) -> None:
                return None

            async def get(self, url: str) -> _FakeResponse:
                return _FakeResponse()

        import httpx as _httpx

        monkeypatch.setattr(_httpx, "AsyncClient", lambda *a, **kw: _FakeClient())
        cf = CommandFile(
            filename="a.txt", content_type="text/plain", url="https://example.com/f.txt"
        )
        assert await gw._download_command_file(cf) == b"file-bytes"


class TestEnsureInboxCollection:
    async def test_no_knowledge_store_returns_none(self) -> None:
        ctx, _ = gw._tenant_ctx_for(_cmd(tenant_id=TID))
        assert await gw._ensure_inbox_collection(SimpleNamespace(knowledge_store=None), ctx, "telegram") is None

    async def test_returns_existing_collection_without_creating(self) -> None:
        existing = SimpleNamespace(name="telegram-inbox", collection_id="coll-existing")
        ks = SimpleNamespace(
            list_collections_async=AsyncMock(return_value=[existing]),
            create_collection_async=AsyncMock(),
        )
        ctx, _ = gw._tenant_ctx_for(_cmd(tenant_id=TID))
        coll_id = await gw._ensure_inbox_collection(SimpleNamespace(knowledge_store=ks), ctx, "telegram")
        assert coll_id == "coll-existing"
        ks.create_collection_async.assert_not_awaited()

    async def test_list_collections_failure_returns_none(self) -> None:
        ks = SimpleNamespace(
            list_collections_async=AsyncMock(side_effect=RuntimeError("db down")),
        )
        ctx, _ = gw._tenant_ctx_for(_cmd(tenant_id=TID))
        assert (
            await gw._ensure_inbox_collection(SimpleNamespace(knowledge_store=ks), ctx, "telegram")
            is None
        )

    async def test_creates_collection_when_none_exists(self) -> None:
        ks = SimpleNamespace(
            list_collections_async=AsyncMock(return_value=[]),
            create_collection_async=AsyncMock(return_value="coll-new"),
        )
        ctx, _ = gw._tenant_ctx_for(_cmd(tenant_id=TID))
        coll_id = await gw._ensure_inbox_collection(SimpleNamespace(knowledge_store=ks), ctx, "slack")
        assert coll_id == "coll-new"
        ks.create_collection_async.assert_awaited_once()
        created_arg = ks.create_collection_async.await_args.args[0]
        assert created_arg.name == "slack-inbox"


class TestSubmitGoalFromCommandFailure:
    async def test_no_goal_service_returns_none(self) -> None:
        state = SimpleNamespace(goal_service=None)
        ctx, _ = gw._tenant_ctx_for(_cmd(tenant_id=TID))
        result = await gw._submit_goal_from_command(_cmd(tenant_id=TID), state, ctx, "do it")
        assert result is None

    async def test_goal_service_exception_returns_none(self) -> None:
        gs = SimpleNamespace(submit_goal=AsyncMock(side_effect=RuntimeError("planner down")))
        state = SimpleNamespace(goal_service=gs)
        ctx, _ = gw._tenant_ctx_for(_cmd(tenant_id=TID))
        result = await gw._submit_goal_from_command(_cmd(tenant_id=TID), state, ctx, "do it")
        assert result is None


class TestIngestCommandFilesEdgeCases:
    async def test_skips_file_with_no_downloadable_data(self) -> None:
        pipeline = SimpleNamespace(ingest=AsyncMock())
        ks = SimpleNamespace(
            list_collections_async=AsyncMock(return_value=[]),
            create_collection_async=AsyncMock(return_value="coll-1"),
        )
        state = SimpleNamespace(ingestion_pipeline=pipeline, knowledge_store=ks)
        cmd = _cmd(
            tenant_id=TID,
            files=[CommandFile(filename="empty.txt", content_type="text/plain")],  # no data/url
        )
        ctx, tid = gw._tenant_ctx_for(cmd)
        n = await gw._ingest_command_files(cmd, state, ctx, tid)
        assert n == 0
        pipeline.ingest.assert_not_awaited()

    async def test_pipeline_exception_is_swallowed_and_not_counted(self) -> None:
        pipeline = SimpleNamespace(ingest=AsyncMock(side_effect=RuntimeError("embed failed")))
        ks = SimpleNamespace(
            list_collections_async=AsyncMock(return_value=[]),
            create_collection_async=AsyncMock(return_value="coll-1"),
        )
        state = SimpleNamespace(ingestion_pipeline=pipeline, knowledge_store=ks)
        cmd = _cmd(
            tenant_id=TID,
            files=[CommandFile(filename="a.txt", content_type="text/plain", data=b"hi")],
        )
        ctx, tid = gw._tenant_ctx_for(cmd)
        n = await gw._ingest_command_files(cmd, state, ctx, tid)
        assert n == 0
        pipeline.ingest.assert_awaited_once()
