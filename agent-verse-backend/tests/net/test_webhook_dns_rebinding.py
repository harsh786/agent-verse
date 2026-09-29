"""Tenant-supplied webhook senders must connect to the address they validated.

Notification webhooks, the A2A completion callback and workflow run callbacks
validated the URL and then posted with a plain ``httpx.AsyncClient``, which
resolves the name AGAIN to connect — a DNS answer flipping from a public IP
(checked) to 127.0.0.1 (connected) reached internal services. AlertRouter
webhooks had no SSRF check at all. All now use the IP-pinned
``public_async_client``.

The resolver below answers public on the first lookup (the up-front check) and
loopback afterwards (the rebinding). A plain client would dial the hostname
via httpcore's AnyIOBackend; the pinned client re-checks at connect and refuses.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock

import httpcore
import pytest

import app.net.ssrf_guard as g

URL = "https://rebind.test/hook"


@pytest.fixture
def rebinding(monkeypatch: pytest.MonkeyPatch) -> Iterator[AsyncMock]:
    calls = {"n": 0}

    def _resolve(host: str) -> list[str]:
        calls["n"] += 1
        return ["93.184.215.14"] if calls["n"] == 1 else ["127.0.0.1"]

    monkeypatch.setattr(g, "_resolve_host", _resolve)
    dial = AsyncMock(side_effect=OSError("tests never dial out"))
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", dial)
    yield dial


async def test_notification_webhook_is_ip_pinned(rebinding: AsyncMock) -> None:
    from app.services.notification_service import _post_public

    with pytest.raises(g.SSRFError):
        await _post_public(URL, {"text": "hi"})
    rebinding.assert_not_called()


async def test_a2a_callback_is_ip_pinned(rebinding: AsyncMock) -> None:
    from app.api.a2a import _send_callback

    await _send_callback(URL, "task-1", "complete", "done")  # swallows errors
    rebinding.assert_not_called()


async def test_workflow_callback_is_ip_pinned(rebinding: AsyncMock) -> None:
    from app.workflow import callbacks

    with pytest.raises((callbacks.CallbackPermanentError, callbacks.CallbackTransientError)):
        await callbacks.deliver_callback(
            URL, {"run_id": "r", "status": "completed"}, tenant_id="t", workflow_id="w"
        )
    rebinding.assert_not_called()


async def test_alert_router_webhook_is_ip_pinned(rebinding: AsyncMock) -> None:
    from app.observability.alert_router import AlertRouter, FiredAlert

    alert = FiredAlert(
        rule_name="r", metric="m", value=2.0, threshold=1.0, severity="warning", tenant_id="t"
    )
    with pytest.raises(g.SSRFError):
        await AlertRouter().send_alert(alert, URL)
    rebinding.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:6379/",
        "http://10.0.0.5/hook",
        "file:///etc/passwd",
    ],
)
async def test_alert_router_refuses_internal_webhook(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.observability.alert_router import AlertRouter, FiredAlert

    dial = AsyncMock(side_effect=OSError("tests never dial out"))
    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", dial)
    alert = FiredAlert(
        rule_name="r", metric="m", value=2.0, threshold=1.0, severity="warning", tenant_id="t"
    )
    with pytest.raises(g.SSRFError):
        await AlertRouter().send_alert(alert, url)
    dial.assert_not_called()


async def test_alert_router_register_rejects_internal_webhook() -> None:
    from app.observability.alert_router import AlertRouter, AlertRule

    with pytest.raises(g.SSRFError):
        AlertRouter().register_rule(
            AlertRule(
                metric="m",
                threshold=1,
                window_seconds=60,
                severity="critical",
                webhook_url="http://169.254.169.254/",
                name="bad",
            )
        )
