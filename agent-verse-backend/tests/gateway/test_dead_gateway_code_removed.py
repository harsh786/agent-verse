"""Dead, superseded gateway modules stay deleted (a10-F248-01/02).

They had no importer outside their own tests, and live paths replaced them, so
keeping them only invited someone to wire a weaker copy:

* ``EmailChannelAdapter`` (open to any sender when no allowlist was set) — the
  live email path is ``POST /channels/email/inbound`` (shared channel secret,
  verified address mapping) in ``app/api/channels/ingestion.py``.
* ``VoiceWebhookAdapter`` — voice is served by ``VoicePhoneChannelAdapter``
  (Twilio signature) and the channel ingestion voice endpoint.
* ``WebhookDeliverySystem`` (``app.gateway.webhook_delivery``) and
  ``OutboundWebhookService`` (``app.services.webhook_service``) — two in-memory
  outbound-webhook "guaranteed delivery" layers nothing called, with no SSRF
  guard on the target URL. Live outbound callbacks go through their own
  SSRF-guarded, durable senders (workflow callbacks ``deliver_workflow_callback``,
  A2A dispatch).
"""

from __future__ import annotations

import importlib.util

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "app.gateway.channels.email",
        "app.gateway.channels.voice_webhook",
        "app.gateway.webhook_delivery",
        "app.services.webhook_service",
    ],
)
def test_dead_gateway_module_is_gone(module: str) -> None:
    assert importlib.util.find_spec(module) is None
