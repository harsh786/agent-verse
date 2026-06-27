"""Tests for email-to-goal integration."""
import os
from unittest.mock import patch

import pytest


def test_imap_disabled_by_default():
    from app.integrations.email.imap_listener import _is_enabled

    with patch.dict(os.environ, {"IMAP_ENABLED": "false"}):
        assert _is_enabled() is False


def test_imap_enabled_via_env():
    from app.integrations.email.imap_listener import _is_enabled

    with patch.dict(os.environ, {"IMAP_ENABLED": "true"}):
        assert _is_enabled() is True


@pytest.mark.asyncio
async def test_check_emails_returns_zero_when_disabled():
    from app.integrations.email.imap_listener import check_and_process_emails

    with patch.dict(os.environ, {"IMAP_ENABLED": "false"}):
        count = await check_and_process_emails(None, None)
        assert count == 0


def test_imap_config_from_env():
    from app.integrations.email.imap_listener import _get_config

    with patch.dict(
        os.environ,
        {"IMAP_HOST": "mail.example.com", "IMAP_USER": "agent@x.com"},
    ):
        cfg = _get_config()
        assert cfg["host"] == "mail.example.com"
        assert cfg["user"] == "agent@x.com"
