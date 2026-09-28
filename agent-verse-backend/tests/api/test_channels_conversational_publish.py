"""Security: conversational events publish only for a verified webhook request
(fail-closed), so a spoofed webhook cannot fire a victim tenant's triggers.

The per-event ``_channel_verified`` gate was replaced by request-level auth:
every inbound channel endpoint now rejects an unauthenticated request (503/401)
before any tenant work, via ``_require_channel_secret`` / signature checks."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from fastapi import HTTPException

from app.api.channels.ingestion import _emit_chat_event, _require_channel_secret


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


def test_unconfigured_channel_is_rejected_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHANNEL_WEBHOOK_SECRET_TEAMS", raising=False)
    with pytest.raises(HTTPException) as exc:
        _require_channel_secret(_request(), "teams")
    assert exc.value.status_code == 503


def test_wrong_secret_is_rejected_401() -> None:
    req = _request(secrets={"teams": "s3cret"}, headers={"X-Webhook-Secret": "wrong"})
    with pytest.raises(HTTPException) as exc:
        _require_channel_secret(req, "teams")
    assert exc.value.status_code == 401


def test_matching_secret_is_accepted() -> None:
    req = _request(secrets={"teams": "s3cret"}, headers={"X-Webhook-Secret": "s3cret"})
    _require_channel_secret(req, "teams")  # no raise


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
