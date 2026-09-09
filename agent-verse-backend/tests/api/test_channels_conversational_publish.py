"""Security: conversational events publish only for a verified webhook request
(fail-closed), so a spoofed webhook cannot fire a victim tenant's triggers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.api.channels.ingestion import _channel_verified, _emit_chat_event


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list[Any] = []

    def publish(self, channel: str, data: str) -> None:
        self.published.append((channel, data))


def _request(*, secrets: dict | None = None, headers: dict | None = None, redis: Any = None) -> Any:
    state = SimpleNamespace(
        channel_webhook_secrets=secrets or {},
        trigger_event_redis=redis,
    )
    return SimpleNamespace(app=SimpleNamespace(state=state), headers=headers or {})


def test_unconfigured_channel_is_not_verified() -> None:
    assert _channel_verified(_request(), "teams") is False


def test_wrong_secret_is_not_verified() -> None:
    req = _request(secrets={"teams": "s3cret"}, headers={"X-Webhook-Secret": "wrong"})
    assert _channel_verified(req, "teams") is False


def test_matching_secret_is_verified() -> None:
    req = _request(secrets={"teams": "s3cret"}, headers={"X-Webhook-Secret": "s3cret"})
    assert _channel_verified(req, "teams") is True


def test_slack_uses_signature_result() -> None:
    assert _channel_verified(_request(), "slack", slack_ok=True) is True
    assert _channel_verified(_request(), "slack", slack_ok=False) is False


@pytest.mark.asyncio
async def test_emit_does_not_publish_when_unverified() -> None:
    redis = _FakeRedis()
    req = _request(redis=redis)
    await _emit_chat_event(req, "teams", {"text": "hi"}, "t1", verified=False)
    assert redis.published == []  # fail-closed: spoofed/unverified → no publish


@pytest.mark.asyncio
async def test_emit_publishes_when_verified() -> None:
    redis = _FakeRedis()
    req = _request(redis=redis)
    await _emit_chat_event(req, "slack", {"event": {"text": "/deploy"}}, "t1", verified=True)
    assert len(redis.published) == 1
    channel, _data = redis.published[0]
    assert channel == "trigger:event:conversational"
