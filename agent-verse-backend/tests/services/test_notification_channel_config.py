"""QA-5: a Teams notification channel actually delivers.

The UI saved Teams channels as ``{"webhook_url": ...}`` while delivery read
``config["url"]`` for webhook/teams ("teams channel has no URL configured"),
creation accepted any channel type / config without validation, and a Teams
incoming webhook was posted the raw internal message dict instead of a payload
Teams understands.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services import notification_service as ns
from app.services.notification_service import (
    NotificationChannel,
    NotificationService,
    normalize_channel_config,
)


@pytest.fixture
def posted(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def _fake_post(url: str, payload: dict[str, Any]) -> None:
        calls.append((url, payload))

    monkeypatch.setattr(ns, "_post_public", _fake_post)
    return calls


# ── normalize_channel_config ─────────────────────────────────────────────────


@pytest.mark.parametrize("channel_type", ["teams", "webhook"])
def test_webhook_url_alias_is_normalized_to_url(channel_type: str) -> None:
    cfg = normalize_channel_config(channel_type, {"webhook_url": "https://hooks.example.com/x"})
    assert cfg == {"url": "https://hooks.example.com/x"}


def test_url_wins_over_alias_and_other_keys_are_kept() -> None:
    cfg = normalize_channel_config(
        "webhook", {"url": "https://a.example.com", "webhook_url": "https://b.example.com",
                    "method": "POST"}
    )
    assert cfg == {"url": "https://a.example.com", "method": "POST"}


def test_slack_accepts_url_alias() -> None:
    cfg = normalize_channel_config("slack", {"url": "https://hooks.slack.com/services/x"})
    assert cfg == {"webhook_url": "https://hooks.slack.com/services/x"}


def test_channel_type_is_case_insensitive() -> None:
    assert normalize_channel_config(" Teams ", {"url": "https://x.example.com"}) == {
        "url": "https://x.example.com"
    }


def test_unsupported_channel_type_is_refused() -> None:
    with pytest.raises(ValueError, match="channel_type must be one of"):
        normalize_channel_config("pagerduty", {"url": "https://x.example.com"})


@pytest.mark.parametrize("channel_type", ["teams", "webhook", "slack"])
def test_missing_url_is_refused(channel_type: str) -> None:
    with pytest.raises(ValueError, match="requires"):
        normalize_channel_config(channel_type, {})


@pytest.mark.parametrize("bad", ["ftp://x.example.com/hook", "not a url", "https://", "   "])
def test_non_http_url_is_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        normalize_channel_config("teams", {"url": bad})


# ── delivery ─────────────────────────────────────────────────────────────────


async def test_teams_channel_saved_by_old_ui_still_delivers(
    posted: list[tuple[str, dict[str, Any]]],
) -> None:
    """Channels stored before the fix carry ``webhook_url``."""
    svc = NotificationService()
    svc.add_channel(
        NotificationChannel(
            channel_id="c-old", tenant_id="t1", channel_type="teams",
            config={"webhook_url": "https://contoso.webhook.office.com/abc"},
        )
    )

    result = await svc.notify_approval_required(
        request_id="r1", goal_id="g1", action="deploy", risk_level="high", tenant_id="t1"
    )

    assert result["sent"] == 1, result
    assert [u for u, _ in posted] == ["https://contoso.webhook.office.com/abc"]


async def test_teams_payload_is_a_text_message(
    posted: list[tuple[str, dict[str, Any]]],
) -> None:
    svc = NotificationService()
    channel = NotificationChannel(
        channel_id="c1", tenant_id="t1", channel_type="teams",
        config={"url": "https://contoso.webhook.office.com/abc"},
    )

    await svc._send(channel, {"type": "test", "request_id": "x", "text": "Hello Teams"})

    assert posted == [("https://contoso.webhook.office.com/abc", {"text": "Hello Teams"})]


async def test_generic_webhook_still_gets_the_full_message(
    posted: list[tuple[str, dict[str, Any]]],
) -> None:
    svc = NotificationService()
    channel = NotificationChannel(
        channel_id="c1", tenant_id="t1", channel_type="webhook",
        config={"webhook_url": "https://hooks.example.com/abc"},
    )
    message = {"type": "test", "request_id": "x", "text": "Hi"}

    await svc._send(channel, message)

    assert posted == [("https://hooks.example.com/abc", message)]


async def test_delivery_stays_behind_the_ssrf_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    guard = AsyncMock(side_effect=ValueError("blocked private address"))
    monkeypatch.setattr(ns, "assert_public_url_async", guard)
    svc = NotificationService()
    channel = NotificationChannel(
        channel_id="c1", tenant_id="t1", channel_type="teams",
        config={"url": "http://169.254.169.254/latest"},
    )

    with pytest.raises(ValueError, match="blocked"):
        await svc._send(channel, {"text": "x"})
    guard.assert_awaited_once()
