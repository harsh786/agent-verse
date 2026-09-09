"""Conversational trigger consumer — Family C (2.W-1).

Semantic ruling (user-approved): conversational triggers fire when a channel
ingestion endpoint publishes a *normalized* inbound event to the EVENT bus. The
Slack/Teams/Discord/email/SMS/voice/form endpoints publish a normalized event to
``trigger:event:conversational``; this consumer matches the tenant's Family C
triggers against it:

  * CHAT_COMMAND     — text starts with ``command_pattern``.
  * CHAT_KEYWORD     — text matches ``keyword_pattern`` (comma-list or regex).
  * CHAT_MENTION     — ``mention_bot_id`` is in the event's mentions.
  * EMAIL_INTENT     — sender/subject match the configured regex filters.
  * SMS_INBOUND      — inbound phone matches ``phone_number_filter``.
  * VOICE_TRANSCRIPT — a voice transcript arrived (optional phone filter).
  * FORM_SUBMISSION  — the event's ``form_id`` matches ``form_id``.

``meeting_ended`` has no inbound endpoint, so it is NOT promoted (stays
unsupported). Firing is tenant-scoped and dispatched through the governed
TriggerDispatcher.
"""

from __future__ import annotations

import json
import logging
import re
from types import SimpleNamespace
from typing import Any

from app.triggers.consumers.event import CHANNEL_PREFIX, _decode, event_channel_name

_log = logging.getLogger(__name__)

CONVERSATIONAL_CHANNEL = "conversational"
_CONV_TYPES = (
    "chat_command",
    "chat_keyword",
    "chat_mention",
    "email_intent",
    "email_arrival",
    "sms_inbound",
    "voice_transcript",
    "form_submission",
    "discord_event",
    "meeting_ended",
)
_SLACK_MENTION = re.compile(r"<@([A-Z0-9]+)>")


def normalize_conversational_event(channel_type: str, body: dict[str, Any]) -> dict[str, Any]:
    """Best-effort normalization of a raw channel payload into a common event
    shape: {channel_type, text, sender, mentions, channel_id, subject, phone,
    form_id}."""
    ev: dict[str, Any] = {
        "conv": True,
        "channel_type": channel_type,
        "text": "",
        "sender": "",
        "channel_id": "",
        "mentions": [],
        "subject": "",
        "phone": "",
        "form_id": "",
    }
    if channel_type == "slack":
        s = body.get("event", {}) if isinstance(body.get("event"), dict) else {}
        ev["text"] = str(s.get("text", "") or "")
        ev["sender"] = str(s.get("user", "") or "")
        ev["channel_id"] = str(s.get("channel", "") or body.get("team_id", "") or "")
        ev["mentions"] = _SLACK_MENTION.findall(ev["text"])
    elif channel_type == "teams":
        ev["text"] = str(body.get("text", "") or "")
        frm = body.get("from", {}) if isinstance(body.get("from"), dict) else {}
        ev["sender"] = str(frm.get("id", "") or "")
        ev["channel_id"] = str(body.get("serviceUrl", "") or "")
        ev["mentions"] = [
            str(m.get("mentioned", {}).get("id", ""))
            for m in (body.get("entities", []) or [])
            if isinstance(m, dict) and m.get("type") == "mention"
        ]
    elif channel_type == "discord":
        data = body.get("data", {}) if isinstance(body.get("data"), dict) else {}
        ev["text"] = str(data.get("name", "") or body.get("content", "") or "")
        member = body.get("member", {}) if isinstance(body.get("member"), dict) else {}
        user = member.get("user", {}) if isinstance(member.get("user"), dict) else {}
        ev["sender"] = str(user.get("id", "") or "")
        ev["channel_id"] = str(body.get("guild_id", "") or body.get("channel_id", "") or "")
        ev["mentions"] = [
            str(u.get("id", "")) for u in (body.get("mentions", []) or []) if isinstance(u, dict)
        ]
    elif channel_type == "email":
        ev["sender"] = str(body.get("from", "") or "")
        ev["subject"] = str(body.get("subject", "") or "")
        ev["text"] = str(body.get("text", "") or "")
    elif channel_type == "sms":
        ev["phone"] = str(body.get("from", "") or "")
        ev["text"] = str(body.get("body", "") or "")
    elif channel_type == "voice":
        ev["text"] = str(body.get("transcript", "") or body.get("text", "") or "")
        ev["phone"] = str(body.get("from", "") or "")
    elif channel_type == "form":
        ev["form_id"] = str(body.get("form_id", "") or "")
        ev["text"] = json.dumps({k: v for k, v in body.items() if k != "form_id"})
    elif channel_type == "meeting":
        ev["meeting_platform"] = str(body.get("platform", "") or "")
        ev["text"] = str(body.get("summary", "") or body.get("transcript", "") or "")
    ev["mentions"] = [m for m in ev["mentions"] if m]
    return ev


async def publish_conversational_event(
    redis: Any, *, tenant_id: str, event: dict[str, Any]
) -> None:
    """Publish a normalized conversational event onto the EVENT bus (tenant stamped)."""
    body = {**event, "tenant_id": tenant_id, "conv": True}
    result = redis.publish(event_channel_name(CONVERSATIONAL_CHANNEL), json.dumps(body))
    if hasattr(result, "__await__"):
        await result


def _regex_ok(pattern: str, value: str) -> bool:
    """True if pattern is empty (no filter) or matches value; bad regex → False."""
    if not pattern:
        return True
    try:
        return re.search(pattern, value) is not None
    except re.error:
        return False


def conversational_matches(ttype: str, spec: Any, event: dict[str, Any]) -> bool:
    """Pure matcher: does a normalized event satisfy a Family C trigger's spec?"""
    ct = getattr(spec, "channel_type", "") or ""
    if ct and ct != event.get("channel_type", ""):
        return False
    cid = getattr(spec, "channel_id", "") or ""
    if cid and cid != event.get("channel_id", ""):
        return False

    text = str(event.get("text", "") or "")
    if ttype == "chat_command":
        pattern = (getattr(spec, "command_pattern", "") or "").strip()
        return bool(pattern) and text.strip().startswith(pattern)
    if ttype == "chat_keyword":
        raw = (getattr(spec, "keyword_pattern", "") or "").strip()
        if not raw:
            return False
        if "," in raw or " " in raw or raw.isalnum():
            keywords = [k.strip().lower() for k in raw.split(",") if k.strip()]
            low = text.lower()
            return any(k in low for k in keywords)
        return _regex_ok(raw, text)
    if ttype == "chat_mention":
        bot_id = getattr(spec, "mention_bot_id", "") or ""
        return bool(bot_id) and bot_id in (event.get("mentions", []) or [])
    if ttype == "email_intent":
        return _regex_ok(
            getattr(spec, "email_sender_filter", "") or "", str(event.get("sender", ""))
        ) and _regex_ok(
            getattr(spec, "email_subject_pattern", "") or "", str(event.get("subject", ""))
        )
    if ttype == "sms_inbound":
        return _regex_ok(
            getattr(spec, "phone_number_filter", "") or "", str(event.get("phone", ""))
        )
    if ttype == "voice_transcript":
        # Fire on any transcript for the tenant, optionally phone-filtered.
        return bool(text) and _regex_ok(
            getattr(spec, "phone_number_filter", "") or "", str(event.get("phone", ""))
        )
    if ttype == "form_submission":
        want = getattr(spec, "form_id", "") or ""
        return (not want) or want == event.get("form_id", "")
    if ttype == "email_arrival":
        # Fire on any inbound email for the tenant (optional sender filter).
        return event.get("channel_type", "") == "email" and _regex_ok(
            getattr(spec, "email_sender_filter", "") or "", str(event.get("sender", ""))
        )
    if ttype == "discord_event":
        # Fire on any Discord event for the tenant (channel scoping applied above).
        return event.get("channel_type", "") == "discord"
    if ttype == "meeting_ended":
        if event.get("channel_type", "") != "meeting":
            return False
        want = getattr(spec, "meeting_platform", "") or ""
        return (not want) or want == event.get("meeting_platform", "")
    return False


class ConversationalTriggerConsumer:
    """Fires Family C triggers on normalized conversational events."""

    def __init__(
        self,
        *,
        trigger_store: object | None = None,
        dispatcher: object | None = None,
        redis: object | None = None,
    ) -> None:
        self._store = trigger_store
        self._dispatcher = dispatcher
        self._redis = redis
        self._running = False

    async def start(self) -> None:
        if self._redis is None:
            _log.warning("conversational_consumer_no_redis — chat triggers disabled")
            return
        self._running = True
        try:
            pubsub = self._redis.pubsub()  # type: ignore[attr-defined]
            await pubsub.psubscribe(f"{CHANNEL_PREFIX}*")
            _log.info("conversational_consumer_started")
            async for message in pubsub.listen():
                if not self._running:
                    break
                if message.get("type") != "pmessage":
                    continue
                await self._handle(message)
        except Exception as exc:  # pragma: no cover - defensive
            _log.error("conversational_consumer_error: %s", exc)

    async def stop(self) -> None:
        self._running = False

    async def _handle(self, message: dict) -> None:
        channel = _decode(message.get("channel"))
        if channel != event_channel_name(CONVERSATIONAL_CHANNEL):
            return
        try:
            event = json.loads(_decode(message.get("data")))
        except Exception:
            return
        if isinstance(event, dict):
            await self._dispatch_matching(event)

    async def _dispatch_matching(self, event: dict) -> None:
        if self._store is None or self._dispatcher is None:
            return
        tenant_id = event.get("tenant_id", "")
        if not tenant_id:
            return
        for ttype in _CONV_TYPES:
            try:
                triggers = await self._store.find_by_type_async(  # type: ignore[attr-defined]
                    ttype, tenant_id=tenant_id
                )
            except Exception as exc:
                _log.warning("conversational_store_error type=%s: %s", ttype, exc)
                continue
            for trig in triggers:
                spec = trig.get("spec") if isinstance(trig, dict) else getattr(trig, "spec", None)
                if spec is None or not conversational_matches(ttype, spec, event):
                    continue
                tenant_ctx = SimpleNamespace(
                    tenant_id=tenant_id, plan=event.get("tenant_plan", "free")
                )
                try:
                    await self._dispatcher.dispatch(  # type: ignore[attr-defined]
                        spec, event, tenant_ctx, message_id=str(event.get("event_id", "") or "")
                    )
                except Exception as exc:
                    _log.warning("conversational_dispatch_error: %s", exc)
