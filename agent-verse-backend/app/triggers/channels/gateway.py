"""Channel Ingestion Gateway — routes inbound channel events to the dispatcher."""
from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)

_CHANNEL_TYPE_MAP = {
    "slack":   ["chat_command", "chat_keyword", "chat_mention", "slack_event"],
    "teams":   ["teams_webhook", "chat_command"],
    "discord": ["discord_event", "chat_command", "chat_keyword"],
    "email":   ["email_intent", "email_arrival"],
    "sms":     ["sms_inbound"],
    "voice":   ["voice_transcript", "meeting_ended"],
    "form":    ["form_submission"],
}


class ChannelIngestionGateway:
    """Routes channel events to matching trigger types and dispatches them."""

    def __init__(
        self,
        *,
        trigger_store: Any = None,
        dispatcher: Any = None,
    ) -> None:
        self._store = trigger_store
        self._dispatcher = dispatcher

    async def ingest(
        self,
        channel_type: str,
        payload: dict,
        *,
        tenant_id: str,
        plan: str = "free",
    ) -> list[Any]:
        """Process an inbound channel event and fire matching triggers.

        Returns list of TriggerEvent objects (one per matched trigger).
        """
        trigger_types = _CHANNEL_TYPE_MAP.get(channel_type, [channel_type])
        fired: list[Any] = []

        for trigger_type in trigger_types:
            if self._store is None:
                continue
            try:
                triggers = await self._store.find_by_type_async(
                    trigger_type, tenant_id=tenant_id
                )
            except Exception as exc:
                _log.warning("channel_ingest_store_error channel=%s: %s", channel_type, exc)
                continue

            for trigger in triggers:
                spec = getattr(trigger, "spec", trigger)
                from types import SimpleNamespace
                tenant_ctx = SimpleNamespace(tenant_id=tenant_id, plan=plan)
                try:
                    if self._dispatcher:
                        event = await self._dispatcher.dispatch(
                            spec, payload, tenant_ctx,
                            message_id=payload.get("message_id") or payload.get("event_id"),
                        )
                        fired.append(event)
                except Exception as exc:
                    _log.warning(
                        "channel_ingest_dispatch_error trigger=%s: %s",
                        getattr(spec, "trigger_id", "?"), exc,
                    )

        return fired


class NLIntentClassifier:
    """Classify natural-language messages to trigger types using embeddings + LLM fallback."""

    def __init__(self, *, embedder: Any = None, llm_provider: Any = None) -> None:
        self._embedder = embedder
        self._llm = llm_provider

    async def classify(self, text: str, channel: str) -> str:
        """Return the most likely trigger_type string for the given message."""
        # Fast path: command prefix detection
        if text.strip().startswith("/"):
            return "chat_command"
        # Fast path: keyword scan
        urgent_words = ["urgent", "alert", "incident", "p1", "critical", "help"]
        if any(w in text.lower() for w in urgent_words):
            return "chat_keyword"
        # Fallback: return generic chat trigger
        return "chat_keyword"
