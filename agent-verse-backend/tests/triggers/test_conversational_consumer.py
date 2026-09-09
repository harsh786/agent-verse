"""2.W-1 / Family C: conversational triggers match normalized channel events."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.triggers.consumers.conversational import (
    ConversationalTriggerConsumer,
    conversational_matches,
    event_channel_name,
    normalize_conversational_event,
)


def _spec(**kw: Any) -> Any:
    base = {
        "channel_type": "",
        "channel_id": "",
        "command_pattern": "",
        "keyword_pattern": "",
        "mention_bot_id": "",
        "email_sender_filter": "",
        "email_subject_pattern": "",
        "phone_number_filter": "",
        "form_id": "",
    }
    base.update(kw)
    return SimpleNamespace(**base)


# ── normalization ─────────────────────────────────────────────────────────────


def test_normalize_slack() -> None:
    body = {"team_id": "T1", "event": {"text": "hi <@U42>", "user": "U9", "channel": "C1"}}
    ev = normalize_conversational_event("slack", body)
    assert ev["text"] == "hi <@U42>"
    assert ev["sender"] == "U9"
    assert ev["mentions"] == ["U42"]


def test_normalize_email_and_sms() -> None:
    em = normalize_conversational_event("email", {"from": "a@b.com", "subject": "Hi", "text": "x"})
    assert em["sender"] == "a@b.com" and em["subject"] == "Hi"
    sms = normalize_conversational_event("sms", {"from": "+1555", "body": "STOP"})
    assert sms["phone"] == "+1555" and sms["text"] == "STOP"


# ── matchers ──────────────────────────────────────────────────────────────────


def test_chat_command_prefix() -> None:
    ev = {"channel_type": "slack", "text": "/deploy prod"}
    assert conversational_matches("chat_command", _spec(command_pattern="/deploy"), ev)
    assert not conversational_matches("chat_command", _spec(command_pattern="/rollback"), ev)


def test_chat_keyword_list() -> None:
    ev = {"channel_type": "slack", "text": "the server is on fire"}
    assert conversational_matches("chat_keyword", _spec(keyword_pattern="fire,outage"), ev)
    assert not conversational_matches("chat_keyword", _spec(keyword_pattern="calm,ok"), ev)


def test_chat_mention() -> None:
    ev = {"channel_type": "slack", "text": "hey", "mentions": ["UBOT"]}
    assert conversational_matches("chat_mention", _spec(mention_bot_id="UBOT"), ev)
    assert not conversational_matches("chat_mention", _spec(mention_bot_id="UOTHER"), ev)


def test_email_intent_filters() -> None:
    ev = {"channel_type": "email", "sender": "vip@corp.com", "subject": "URGENT: down"}
    assert conversational_matches(
        "email_intent", _spec(email_sender_filter="@corp.com", email_subject_pattern="URGENT"), ev
    )
    assert not conversational_matches("email_intent", _spec(email_subject_pattern="^FYI"), ev)


def test_sms_and_form_and_voice() -> None:
    assert conversational_matches(
        "sms_inbound",
        _spec(phone_number_filter=r"\+1555"),
        {"channel_type": "sms", "phone": "+1555"},
    )
    assert conversational_matches(
        "form_submission", _spec(form_id="contact"), {"channel_type": "form", "form_id": "contact"}
    )
    assert conversational_matches(
        "voice_transcript", _spec(), {"channel_type": "voice", "text": "hello there"}
    )
    assert not conversational_matches(
        "voice_transcript", _spec(), {"channel_type": "voice", "text": ""}
    )


def test_channel_scoping() -> None:
    ev = {"channel_type": "discord", "text": "/deploy"}
    # A slack-scoped trigger must not match a discord event.
    assert not conversational_matches(
        "chat_command", _spec(channel_type="slack", command_pattern="/deploy"), ev
    )


# ── dispatch flow ─────────────────────────────────────────────────────────────


class _FakePubSub:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._messages = messages

    async def psubscribe(self, pattern: str) -> None:
        self.pattern = pattern

    async def listen(self) -> Any:
        for m in self._messages:
            await asyncio.sleep(0)
            yield m


class _FakeRedis:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._messages = messages

    def pubsub(self) -> _FakePubSub:
        return _FakePubSub(self._messages)


class _FakeStore:
    def __init__(self, by_type: dict[str, list[dict[str, Any]]]) -> None:
        self._t = by_type

    async def find_by_type_async(self, ttype: str, *, tenant_id: str) -> list[dict[str, Any]]:
        return self._t.get(ttype, [])


class _FakeDispatcher:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def dispatch(
        self, spec: Any, payload: Any, tenant_ctx: Any, *, message_id: str = ""
    ) -> None:
        self.calls.append((spec, payload, tenant_ctx))


@pytest.mark.asyncio
async def test_consumer_dispatches_matching_chat_command() -> None:
    event = {"conv": True, "tenant_id": "t1", "channel_type": "slack", "text": "/deploy now"}
    msg = {
        "type": "pmessage",
        "channel": event_channel_name("conversational").encode(),
        "data": json.dumps(event).encode(),
    }
    store = _FakeStore({"chat_command": [{"spec": _spec(command_pattern="/deploy")}]})
    disp = _FakeDispatcher()
    c = ConversationalTriggerConsumer(trigger_store=store, dispatcher=disp, redis=_FakeRedis([msg]))
    await c.start()
    assert len(disp.calls) == 1


@pytest.mark.asyncio
async def test_consumer_ignores_non_matching() -> None:
    event = {"conv": True, "tenant_id": "t1", "channel_type": "slack", "text": "hello"}
    msg = {
        "type": "pmessage",
        "channel": event_channel_name("conversational").encode(),
        "data": json.dumps(event).encode(),
    }
    store = _FakeStore({"chat_command": [{"spec": _spec(command_pattern="/deploy")}]})
    disp = _FakeDispatcher()
    c = ConversationalTriggerConsumer(trigger_store=store, dispatcher=disp, redis=_FakeRedis([msg]))
    await c.start()
    assert disp.calls == []


def test_email_arrival_and_discord_event() -> None:
    assert conversational_matches(
        "email_arrival", _spec(), {"channel_type": "email", "sender": "x@y.com"}
    )
    assert conversational_matches(
        "email_arrival",
        _spec(email_sender_filter="@corp"),
        {"channel_type": "email", "sender": "a@corp"},
    )
    assert not conversational_matches(
        "email_arrival", _spec(), {"channel_type": "slack", "text": "hi"}
    )
    assert conversational_matches(
        "discord_event", _spec(), {"channel_type": "discord", "text": "/x"}
    )
    assert not conversational_matches(
        "discord_event", _spec(), {"channel_type": "slack", "text": "/x"}
    )


def test_meeting_ended_matcher() -> None:
    assert conversational_matches(
        "meeting_ended", _spec(), {"channel_type": "meeting", "meeting_platform": "zoom"}
    )
    assert not conversational_matches(
        "meeting_ended", _spec(), {"channel_type": "slack", "text": "hi"}
    )
