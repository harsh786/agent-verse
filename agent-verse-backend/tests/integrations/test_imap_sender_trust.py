"""TRG-37: only authenticated, allow-listed senders can start goals by email.

With IMAP_ENABLED every unseen email from ANY sender became an autonomous goal,
with no sender allowlist and no SPF/DKIM check. Now a message must come from an
address/domain on IMAP_SENDER_ALLOWLIST and carry a receiving-MTA
Authentication-Results header with an aligned dkim=pass / spf=pass / dmarc=pass;
anything else is dropped (marked read, logged) without creating a goal.
"""

from __future__ import annotations

import email as email_lib
import os
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations.email.imap_listener import sender_trust_problem

PASS_DKIM = "mx.example.net; dkim=pass header.d=company.com; spf=fail smtp.mailfrom=x.org"


def _msg(sender: str, auth: str | None = PASS_DKIM, extra_auth: str | None = None) -> Any:
    msg = email_lib.message.Message()
    msg["Subject"] = "Deploy the new feature"
    msg["From"] = sender
    if auth is not None:
        msg["Authentication-Results"] = auth
    if extra_auth is not None:
        msg["Authentication-Results"] = extra_auth
    msg.set_payload("Please deploy feature X.")
    return msg


@pytest.fixture(autouse=True)
def _allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAP_SENDER_ALLOWLIST", "@company.com, ops@partner.io")
    monkeypatch.delenv("IMAP_AUTHSERV_ID", raising=False)


def test_allowlisted_and_dkim_aligned_sender_is_trusted() -> None:
    assert sender_trust_problem(_msg("Boss <boss@company.com>")) is None
    assert sender_trust_problem(_msg("ops@partner.io", auth="mx; spf=pass smtp.mailfrom=partner.io")) is None
    assert sender_trust_problem(_msg("a@company.com", auth="mx; dmarc=pass header.from=company.com")) is None


@pytest.mark.parametrize(
    ("sender", "auth", "reason"),
    [
        ("attacker@evil.com", PASS_DKIM, "not on IMAP_SENDER_ALLOWLIST"),
        ("boss@company.com", None, "no Authentication-Results"),
        ("boss@company.com", "mx; dkim=fail header.d=company.com; spf=softfail", "not authenticated"),
        # dkim passes, but for a different domain than the From address (spoofed From).
        ("boss@company.com", "mx; dkim=pass header.d=evil.com", "not authenticated"),
        ("other@partner.io", "mx; spf=pass smtp.mailfrom=partner.io", "not on IMAP_SENDER_ALLOWLIST"),
    ],
)
def test_untrusted_senders_are_rejected(sender: str, auth: str | None, reason: str) -> None:
    problem = sender_trust_problem(_msg(sender, auth))
    assert problem is not None and reason in problem


def test_only_the_topmost_authentication_results_counts() -> None:
    # A sender can add their own "dkim=pass" header lower down; the receiving MTA
    # prepends its verdict on top.
    msg = email_lib.message.Message()
    msg["From"] = "boss@company.com"
    msg["Authentication-Results"] = "mx.example.net; dkim=fail header.d=company.com"
    msg["Authentication-Results"] = "forged; dkim=pass header.d=company.com"
    assert sender_trust_problem(msg) is not None


def test_authserv_id_is_pinned_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAP_AUTHSERV_ID", "mx.example.net")
    assert sender_trust_problem(_msg("boss@company.com")) is None
    forged = _msg("boss@company.com", auth="attacker.host; dkim=pass header.d=company.com")
    assert "authserv-id" in (sender_trust_problem(forged) or "")


def test_empty_allowlist_trusts_nobody(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMAP_SENDER_ALLOWLIST", "")
    assert "IMAP_SENDER_ALLOWLIST" in (sender_trust_problem(_msg("boss@company.com")) or "")


# ── end to end through the poller ────────────────────────────────────────────


async def _poll(msg: Any) -> tuple[int, AsyncMock, AsyncMock]:
    mock_imap = AsyncMock()
    mock_imap.search = AsyncMock(return_value=("OK", [b"1"]))
    mock_imap.fetch = AsyncMock(return_value=("OK", [None, msg.as_bytes()]))
    goal_service = AsyncMock()
    goal_service._redis = None
    goal_service.submit_goal = AsyncMock(return_value={"goal_id": "g-1"})
    module = MagicMock()
    module.IMAP4_SSL = MagicMock(return_value=mock_imap)
    env = {"IMAP_ENABLED": "true", "IMAP_HOST": "imap.example.com", "IMAP_USER": "u@x.com"}
    with patch.dict(os.environ, env), patch.dict(sys.modules, {"aioimaplib": module}):
        from app.integrations.email import imap_listener

        processed = await imap_listener.check_and_process_emails(goal_service, MagicMock())
    return processed, goal_service.submit_goal, mock_imap.store


async def test_email_from_non_allowlisted_sender_creates_no_goal() -> None:
    processed, submit, store = await _poll(_msg("attacker@evil.com"))
    assert processed == 0
    submit.assert_not_awaited()
    store.assert_awaited()  # marked read so it is not re-evaluated every poll


async def test_email_failing_dkim_creates_no_goal() -> None:
    processed, submit, _ = await _poll(_msg("boss@company.com", auth="mx; dkim=fail; spf=fail"))
    assert processed == 0
    submit.assert_not_awaited()


async def test_trusted_email_still_becomes_a_goal() -> None:
    processed, submit, _ = await _poll(_msg("boss@company.com"))
    assert processed == 1
    submit.assert_awaited_once()
