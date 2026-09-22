"""Tests for app/voice/alerts.py — proactive voice alert manager."""
from __future__ import annotations

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.voice.alerts import (
    ALERT_TEMPLATES,
    VoiceAlertManager,
    build_alert_text,
    publish_voice_alert,
)


# ── build_alert_text ─────────────────────────────────────────────────────────


class TestBuildAlertText:
    def test_known_template_renders_context(self) -> None:
        text = build_alert_text("mission_failed", {"title": "Q4 Launch"})
        assert text == "Alert: Mission Q4 Launch has failed and requires your attention."

    def test_all_known_templates_render(self) -> None:
        for event_type in ALERT_TEMPLATES:
            text = build_alert_text(event_type, {"title": "Foo"})
            assert "Foo" in text or "{" not in text

    def test_unknown_event_type_uses_message_fallback(self) -> None:
        text = build_alert_text("unknown_event", {"message": "Something happened"})
        assert text == "Alert: {message}".format(message="Something happened")

    def test_missing_context_key_falls_back_to_message(self) -> None:
        # template requires {title} but context has no title -> KeyError -> fallback
        text = build_alert_text("mission_failed", {"message": "fallback text"})
        assert text == "fallback text"

    def test_missing_context_key_and_no_message_falls_back_to_generic(self) -> None:
        text = build_alert_text("mission_failed", {})
        assert text == "Alert: mission_failed"


# ── VoiceAlertManager.subscribe/unsubscribe ──────────────────────────────────


class TestSubscribeUnsubscribe:
    def test_subscribe_returns_queue_and_registers(self) -> None:
        mgr = VoiceAlertManager(redis=None)
        q = mgr.subscribe("tenant-1")
        assert isinstance(q, asyncio.Queue)
        assert mgr._subscribers["tenant-1"] == [q]

    def test_subscribe_multiple_same_tenant(self) -> None:
        mgr = VoiceAlertManager(redis=None)
        q1 = mgr.subscribe("tenant-1")
        q2 = mgr.subscribe("tenant-1")
        assert mgr._subscribers["tenant-1"] == [q1, q2]

    def test_unsubscribe_removes_queue(self) -> None:
        mgr = VoiceAlertManager(redis=None)
        q = mgr.subscribe("tenant-1")
        mgr.unsubscribe("tenant-1", q)
        assert "tenant-1" not in mgr._subscribers

    def test_unsubscribe_keeps_other_queues(self) -> None:
        mgr = VoiceAlertManager(redis=None)
        q1 = mgr.subscribe("tenant-1")
        q2 = mgr.subscribe("tenant-1")
        mgr.unsubscribe("tenant-1", q1)
        assert mgr._subscribers["tenant-1"] == [q2]

    def test_unsubscribe_unknown_tenant_is_noop(self) -> None:
        mgr = VoiceAlertManager(redis=None)
        q = asyncio.Queue()
        # should not raise
        mgr.unsubscribe("does-not-exist", q)

    def test_unsubscribe_queue_not_in_list_is_noop(self) -> None:
        mgr = VoiceAlertManager(redis=None)
        mgr.subscribe("tenant-1")
        other_q = asyncio.Queue()
        # should not raise even though other_q was never subscribed
        mgr.unsubscribe("tenant-1", other_q)
        assert "tenant-1" in mgr._subscribers


# ── start/stop ────────────────────────────────────────────────────────────────


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_sets_running_and_schedules_loop(self) -> None:
        mgr = VoiceAlertManager(redis=None)
        with patch("asyncio.create_task") as mock_create_task:
            await mgr.start()
        assert mgr._running is True
        mock_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_clears_running(self) -> None:
        mgr = VoiceAlertManager(redis=None)
        mgr._running = True
        await mgr.stop()
        assert mgr._running is False


# ── _listen_loop ──────────────────────────────────────────────────────────────


class TestListenLoop:
    @pytest.mark.asyncio
    async def test_no_redis_logs_warning_and_returns(self) -> None:
        mgr = VoiceAlertManager(redis=None)
        # should return immediately without error
        await mgr._listen_loop()

    @pytest.mark.asyncio
    async def test_listen_loop_dispatches_messages_and_ignores_subscribe_acks(self) -> None:
        redis = MagicMock()
        pubsub = MagicMock()
        redis.pubsub.return_value = pubsub
        pubsub.psubscribe = AsyncMock()

        messages = [
            {"type": "psubscribe", "data": 1},
            {"type": "pmessage", "data": "{}", "channel": "voice:alerts:t1"},
        ]

        async def fake_listen():
            for m in messages:
                yield m

        pubsub.listen = fake_listen

        mgr = VoiceAlertManager(redis=redis)
        mgr._running = True
        handled = []

        async def fake_handle(raw):
            handled.append(raw)
            mgr._running = False  # stop after first real message

        mgr._handle_alert_message = fake_handle  # type: ignore[method-assign]
        await mgr._listen_loop()
        assert len(handled) == 1
        assert handled[0]["type"] == "pmessage"

    @pytest.mark.asyncio
    async def test_listen_loop_swallows_exceptions(self) -> None:
        redis = MagicMock()
        redis.pubsub.side_effect = RuntimeError("boom")
        mgr = VoiceAlertManager(redis=redis)
        # should not raise
        await mgr._listen_loop()


# ── _handle_alert_message ─────────────────────────────────────────────────────


class TestHandleAlertMessage:
    @pytest.mark.asyncio
    async def test_ignores_unknown_tenant(self) -> None:
        mgr = VoiceAlertManager(redis=None)
        raw = {"data": json.dumps({"event": "mission_failed"}), "channel": "voice:alerts:unknown"}
        # no subscribers registered -> should just return without error
        await mgr._handle_alert_message(raw)

    @pytest.mark.asyncio
    async def test_ignores_when_no_channel(self) -> None:
        mgr = VoiceAlertManager(redis=None)
        raw = {"data": json.dumps({"event": "mission_failed"}), "channel": ""}
        await mgr._handle_alert_message(raw)

    @pytest.mark.asyncio
    async def test_pushes_synthesized_audio_to_subscriber_queue(self) -> None:
        mgr = VoiceAlertManager(redis=None)
        q = mgr.subscribe("t1")
        raw = {
            "data": json.dumps(
                {"event": "mission_failed", "context": {"title": "Launch"}}
            ),
            "channel": "voice:alerts:t1",
        }

        async def fake_stream(text, language="en"):
            yield b"chunk1"
            yield b"chunk2"

        with patch("app.voice.tts_engine.synthesize_streaming", fake_stream):
            await mgr._handle_alert_message(raw)

        assert not q.empty()
        item = q.get_nowait()
        assert item["event_type"] == "mission_failed"
        assert item["text"] == "Alert: Mission Launch has failed and requires your attention."
        assert item["chunks"] == [
            base64.b64encode(b"chunk1").decode(),
            base64.b64encode(b"chunk2").decode(),
        ]

    @pytest.mark.asyncio
    async def test_channel_as_bytes_is_decoded(self) -> None:
        mgr = VoiceAlertManager(redis=None)
        q = mgr.subscribe("t2")
        raw = {
            "data": json.dumps({"event": "goal_failed", "context": {"title": "X"}}),
            "channel": b"voice:alerts:t2",
        }

        async def fake_stream(text, language="en"):
            return
            yield  # pragma: no cover

        with patch("app.voice.tts_engine.synthesize_streaming", fake_stream):
            await mgr._handle_alert_message(raw)
        assert not q.empty()

    @pytest.mark.asyncio
    async def test_queue_full_is_logged_not_raised(self) -> None:
        mgr = VoiceAlertManager(redis=None)
        q = mgr.subscribe("t3")
        # fill the queue to capacity (maxsize=50)
        for _ in range(50):
            q.put_nowait({"filler": True})

        raw = {
            "data": json.dumps({"event": "budget_exceeded", "context": {"title": "Y"}}),
            "channel": "voice:alerts:t3",
        }

        async def fake_stream(text, language="en"):
            yield b"x"

        with patch("app.voice.tts_engine.synthesize_streaming", fake_stream):
            # should not raise despite the full queue
            await mgr._handle_alert_message(raw)

    @pytest.mark.asyncio
    async def test_malformed_json_is_caught(self) -> None:
        mgr = VoiceAlertManager(redis=None)
        mgr.subscribe("t4")
        raw = {"data": "not json", "channel": "voice:alerts:t4"}
        # should not raise
        await mgr._handle_alert_message(raw)


# ── publish_voice_alert ───────────────────────────────────────────────────────


class TestPublishVoiceAlert:
    @pytest.mark.asyncio
    async def test_no_redis_is_noop(self) -> None:
        # should not raise
        await publish_voice_alert(None, "t1", "mission_failed", {"title": "X"})

    @pytest.mark.asyncio
    async def test_publishes_expected_payload(self) -> None:
        redis = MagicMock()
        redis.publish = AsyncMock()
        await publish_voice_alert(
            redis, "t1", "mission_failed", {"title": "X"}, title="X", message="msg"
        )
        redis.publish.assert_awaited_once()
        channel, payload = redis.publish.call_args.args
        assert channel == "voice:alerts:t1"
        data = json.loads(payload)
        assert data["event"] == "mission_failed"
        assert data["title"] == "X"
        assert data["message"] == "msg"

    @pytest.mark.asyncio
    async def test_title_defaults_to_context_title(self) -> None:
        redis = MagicMock()
        redis.publish = AsyncMock()
        await publish_voice_alert(redis, "t1", "goal_failed", {"title": "From Context"})
        _, payload = redis.publish.call_args.args
        data = json.loads(payload)
        assert data["title"] == "From Context"
