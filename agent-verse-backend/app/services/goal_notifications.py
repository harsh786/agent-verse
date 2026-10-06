"""Opt-in goal outcome notifications (a08-F196-05).

Reads the goal lifecycle stream (``goal.completed`` / ``goal.failed``, XADDed by
GoalService for in-process goals and by the ``run_goal`` worker) through its own
consumer group, so every replica's consumer shares the work and each event is
delivered to one of them. For a tenant that opted in (``goalComplete`` /
``goalFailed`` in its notification preferences, off by default) it sends the
outcome to the tenant's notification channels (Slack / Teams / webhook).

Exactly-once per goal outcome across replicas and redeliveries: a ``SET NX``
claim on ``goal_notified:{tenant}:{goal}:{outcome}`` is taken before sending; a
redelivered or duplicate event loses the claim and sends nothing. (A crash
between the claim and the send loses that one notification — at most once, by
design: a duplicate alert is worse than none for an opt-in convenience.)

Content: goal id, status and a short summary — the goal text on success, the
stored failure reason on failure — both redacted (secrets + PII) and truncated.
Never tool output. Nothing here can affect the goal: this runs after the goal
ended, in a separate consumer; a channel error is logged and counted.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.services.notification_prefs import GOAL_OUTCOME_PREF, load_prefs

_log = logging.getLogger(__name__)

GROUP = "notifications:goal-outcome"
CHANNEL_OUTCOME: dict[str, str] = {"goal.completed": "complete", "goal.failed": "failed"}
_CLAIM_TTL_S = 7 * 86400
_SUMMARY_MAX = 280


def claim_key(tenant_id: str, goal_id: str, outcome: str) -> str:
    return f"goal_notified:{tenant_id}:{goal_id}:{outcome}"


def _metric(outcome: str, result: str) -> None:
    from app.observability.metrics import record_goal_notification

    record_goal_notification(outcome, result)


def sanitize_summary(text: Any) -> str:
    """Redacted (secrets + PII), single-line, truncated summary text."""
    from app.guardrails_v2.output_screening import redact_baseline

    flat = " ".join(str(text or "").split())
    if not flat:
        return ""
    cleaned = redact_baseline(flat)
    return cleaned if len(cleaned) <= _SUMMARY_MAX else cleaned[: _SUMMARY_MAX - 1] + "…"


class GoalNotificationConsumer:
    """Stream consumer that turns goal outcomes into opt-in notifications."""

    GROUP = GROUP
    CHANNELS = ("goal.completed", "goal.failed")

    def __init__(
        self,
        *,
        redis: Any,
        notification_service: Any,
        db_session_factory: Any = None,
    ) -> None:
        self._redis = redis
        self._notifications = notification_service
        self._db = db_session_factory
        self._running = False

    async def start(self) -> None:
        if self._redis is None:
            _log.warning("goal_notification_consumer_no_redis")
            return
        from app.triggers.bus import run_stream_consumer

        self._running = True
        await run_stream_consumer(
            self, label="goal_notification_consumer", channel=self.CHANNELS[0], group=GROUP
        )

    async def stop(self) -> None:
        self._running = False

    async def _handle(self, message: dict[str, Any]) -> None:
        raw_channel = message.get("channel", "")
        channel = raw_channel.decode() if isinstance(raw_channel, bytes) else str(raw_channel)
        raw = message.get("data", b"")
        try:
            data = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        except Exception:
            return
        if isinstance(data, dict):
            await self.handle_event(channel, data)

    async def handle_event(self, channel: str, data: dict[str, Any]) -> str:
        """Process one lifecycle event; returns what happened (for tests / logs).

        Raises only when the preferences or the claim cannot be read from Redis,
        so the stream entry stays pending and is retried (the claim is not taken
        yet). Everything after the claim is logged and counted, never raised.
        """
        outcome = CHANNEL_OUTCOME.get(channel)
        tenant_id = str(data.get("tenant_id") or "")
        goal_id = str(data.get("goal_id") or "")
        if outcome is None or not tenant_id or not goal_id:
            return "ignored"
        prefs = await load_prefs(self._redis, tenant_id)
        if not prefs.get(GOAL_OUTCOME_PREF[outcome], False):
            return "not_opted_in"
        won = await self._redis.set(
            claim_key(tenant_id, goal_id, outcome), "1", nx=True, ex=_CLAIM_TTL_S
        )
        if not won:
            _metric(outcome, "duplicate")
            return "duplicate"
        try:
            summary = await self._summary(tenant_id, goal_id, outcome)
            result = await self._notifications.notify_goal_outcome(
                goal_id=goal_id, status=outcome, tenant_id=tenant_id, summary=summary
            )
        except Exception as exc:
            _log.warning(
                "goal_notification_error tenant=%s goal=%s: %s",
                tenant_id,
                goal_id,
                type(exc).__name__,
            )
            _metric(outcome, "error")
            return "error"
        failed = [c for c in result.get("channels", []) if c.get("status") != "sent"]
        for _ in failed:
            _metric(outcome, "channel_failed")
        if failed:
            _log.warning(
                "goal_notification_channels_failed tenant=%s goal=%s failed=%d",
                tenant_id,
                goal_id,
                len(failed),
            )
        sent = int(result.get("sent", 0) or 0)
        if sent:
            _metric(outcome, "sent")
            return "sent"
        return "channel_failed" if failed else "no_channels"

    async def _summary(self, tenant_id: str, goal_id: str, outcome: str) -> str:
        """Sanitized goal text (success) or failure reason (failure); '' if unknown."""
        if self._db is None:
            return ""
        try:
            from sqlalchemy import text

            from app.db.rls import sqlalchemy_rls_context

            async with (
                self._db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                row = (
                    await session.execute(
                        text(
                            "SELECT goal_text, error_message FROM goals "
                            "WHERE id = :gid AND tenant_id = :tid"
                        ),
                        {"gid": goal_id, "tid": tenant_id},
                    )
                ).first()
        except Exception as exc:
            # The notification still goes out, with id + status only.
            _log.warning("goal_notification_summary_unavailable: %s", type(exc).__name__)
            return ""
        if row is None:
            return ""
        return sanitize_summary(row[0] if outcome == "complete" else (row[1] or ""))
