"""D-6: Proactive Voice Alerts — push TTS audio when important events happen.

Listens to Redis pub/sub channels and synthesises alert audio that is pushed to
connected clients via Server-Sent Events. This makes AgentVerse the only product
that proactively speaks to you when something needs attention:

  - mission failed / blocked
  - high-priority approval request
  - budget alert
  - agent error requiring human intervention

Architecture:
  Redis pub/sub channel: "voice:alerts:{tenant_id}"
  Payload: {"type": "alert", "org_id": "...", "event": "...", "message": "..."}

The alert manager runs as a background task started from app lifespan.
Clients receive audio via GET /v1/voice/alerts/stream (SSE).
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import structlog
from opentelemetry import trace

log = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

# ── Alert event types ─────────────────────────────────────────────────────────

ALERT_TEMPLATES: dict[str, str] = {
    "mission_failed": "Alert: Mission {title} has failed and requires your attention.",
    "mission_blocked": "Heads up: Mission {title} is blocked and waiting for input.",
    "approval_urgent": "Urgent: You have a pending approval for {title} that is time-sensitive.",
    "budget_exceeded": "Budget alert: Mission {title} has exceeded its budget limit.",
    "agent_error": "Agent error in {title}. Human intervention may be required.",
    "mission_completed": "Great news: Mission {title} has completed successfully.",
    "goal_failed": "Goal execution failed for {title}. Please review.",
}


def build_alert_text(event_type: str, context: dict[str, Any]) -> str:
    """Render a spoken alert text from event type + context."""
    template = ALERT_TEMPLATES.get(event_type, "Alert: {message}")
    try:
        return template.format(**context)
    except KeyError:
        return context.get("message", f"Alert: {event_type}")


# ── Background alert listener ──────────────────────────────────────────────────


class VoiceAlertManager:
    """Listens to Redis pub/sub and synthesises TTS for proactive alerts.

    D-6: Subscribers connect to GET /v1/voice/alerts/stream (SSE) to receive
    base64-encoded PCM16 audio chunks for their tenant.
    """

    def __init__(self, redis: Any, tts_factory: Any | None = None) -> None:
        self._redis = redis
        self._tts_factory = tts_factory
        self._subscribers: dict[str, list[asyncio.Queue]] = {}  # tenant_id → queues
        self._running = False

    async def start(self) -> None:
        """Start the pub/sub listener loop."""
        self._running = True
        asyncio.create_task(self._listen_loop())
        log.info("voice.alerts.manager_started")

    async def stop(self) -> None:
        self._running = False

    def subscribe(self, tenant_id: str) -> asyncio.Queue:
        """Subscribe to voice alerts for a tenant. Returns an audio chunk queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._subscribers.setdefault(tenant_id, []).append(q)
        return q

    def unsubscribe(self, tenant_id: str, q: asyncio.Queue) -> None:
        if tenant_id in self._subscribers:
            self._subscribers[tenant_id].discard(q)

    async def _listen_loop(self) -> None:
        """Subscribe to Redis pub/sub and process alert events."""
        if self._redis is None:
            log.warning("voice.alerts.no_redis")
            return
        try:
            pubsub = self._redis.pubsub()
            await pubsub.psubscribe("voice:alerts:*")
            log.info("voice.alerts.subscribed pattern=voice:alerts:*")

            async for raw in pubsub.listen():
                if not self._running:
                    break
                if raw["type"] not in ("pmessage", "message"):
                    continue
                await self._handle_alert_message(raw)
        except Exception as exc:
            log.error("voice.alerts.loop_error", exc_info=exc)

    async def _handle_alert_message(self, raw: dict) -> None:
        with tracer.start_as_current_span("voice.alerts.handle") as span:
            try:
                data = json.loads(raw.get("data", "{}"))
                channel = (
                    raw.get("channel", b"").decode()
                    if isinstance(raw.get("channel"), bytes)
                    else raw.get("channel", "")
                )
                tenant_id = channel.split(":")[-1] if channel else ""
                if not tenant_id or tenant_id not in self._subscribers:
                    return

                event_type = data.get("event", "alert")
                context = data.get("context", {})
                context.setdefault("message", data.get("message", "Alert"))
                context.setdefault("title", data.get("title", "mission"))

                span.set_attribute("event_type", event_type)
                span.set_attribute("tenant_id", tenant_id)

                alert_text = build_alert_text(event_type, context)
                log.info("voice.alerts.synthesizing", tenant_id=tenant_id, event=event_type)

                # Synthesise TTS for the alert
                from app.voice.tts_engine import synthesize_streaming

                chunks = []
                async for chunk in synthesize_streaming(alert_text, language="en"):
                    chunks.append(base64.b64encode(chunk).decode())

                # Push to all subscribers for this tenant
                for q in list(self._subscribers.get(tenant_id, [])):
                    try:
                        await q.put(
                            {
                                "event_type": event_type,
                                "text": alert_text,
                                "chunks": chunks,
                            }
                        )
                    except asyncio.QueueFull:
                        log.warning("voice.alerts.queue_full", tenant_id=tenant_id)
            except Exception as exc:
                log.error("voice.alerts.handle_error", exc_info=exc)


async def publish_voice_alert(
    redis: Any,
    tenant_id: str,
    event_type: str,
    context: dict[str, Any],
    *,
    title: str = "",
    message: str = "",
) -> None:
    """Publish a voice alert to the tenant's pub/sub channel.

    Call this from anywhere in the codebase when an alertable event occurs:
        await publish_voice_alert(redis, tenant_id, "mission_failed",
                                  {"title": mission.title})
    """
    if redis is None:
        return
    payload = json.dumps(
        {
            "event": event_type,
            "context": context,
            "title": title or context.get("title", ""),
            "message": message,
        }
    )
    channel = f"voice:alerts:{tenant_id}"
    await redis.publish(channel, payload)
    log.info("voice.alerts.published", tenant_id=tenant_id, event=event_type)
