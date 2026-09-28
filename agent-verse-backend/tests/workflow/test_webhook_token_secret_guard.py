"""The public dev signing key must never authenticate workflow webhooks in production."""

from __future__ import annotations

import pytest

from app.workflow import webhook_tokens as wt


def test_production_without_secret_refuses_to_mint_and_rejects_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WORKFLOW_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    forged = wt.make_webhook_token("victim-tenant", "wf-1")  # signed with the public dev key
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(wt.WebhookSecretNotConfiguredError):
        wt.make_webhook_token("t", "w")
    assert wt.verify_webhook_token(forged) is None


def test_configured_secret_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("WORKFLOW_WEBHOOK_SECRET", "real-secret")
    token = wt.make_webhook_token("t1", "wf-9")
    assert wt.verify_webhook_token(token) == ("t1", "wf-9")
    monkeypatch.setenv("WORKFLOW_WEBHOOK_SECRET", "rotated")
    assert wt.verify_webhook_token(token) is None
