"""Dead, superseded gateway modules stay deleted (a10-F248-01/02).

They had no importer outside their own tests, and live paths replaced them, so
keeping them only invited someone to wire a weaker copy:

* ``EmailChannelAdapter`` (open to any sender when no allowlist was set) — the
  live email path is ``POST /channels/email/inbound`` (shared channel secret,
  verified address mapping) in ``app/api/channels/ingestion.py``.
* ``VoiceWebhookAdapter`` — voice is served by ``VoicePhoneChannelAdapter``
  (Twilio signature) and the channel ingestion voice endpoint.
"""

from __future__ import annotations

import importlib.util

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "app.gateway.channels.email",
        "app.gateway.channels.voice_webhook",
    ],
)
def test_dead_gateway_module_is_gone(module: str) -> None:
    assert importlib.util.find_spec(module) is None
