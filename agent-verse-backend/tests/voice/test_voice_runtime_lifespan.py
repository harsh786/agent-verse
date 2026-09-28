"""Regression: the voice runtime was started AFTER the lifespan's ``yield``.

The warmup + ``VoiceAlertManager`` block sat after the ``if manage_pools: ...
else: ...`` whose branches each ``yield`` — so it only ran at shutdown, and the
alert manager never existed while the app served traffic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def test_voice_alert_manager_runs_while_serving_and_not_after() -> None:
    from app.main import create_app

    app = create_app()
    with patch("app.voice.providers.warmup_providers", AsyncMock()) as warm:
        with TestClient(app):
            mgr = getattr(app.state, "voice_alert_manager", None)
            assert mgr is not None, "alert manager must be started before serving"
            warm.assert_called()
