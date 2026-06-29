"""Comprehensive coverage for app/integrations/email/ modules.

Covers:
  - imap_listener: _is_enabled, _get_config, _decode_header_value,
    check_and_process_emails (disabled, no-host, aioimaplib missing, happy path, error)
  - approval_sender: _sign, _verify, send_approval_email (success, import error, SMTP error)
"""
from __future__ import annotations

import email as email_lib
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── imap_listener: configuration helpers ────────────────────────────────────

class TestImapListenerIsEnabled:
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            import importlib
            import app.integrations.email.imap_listener as mod
            importlib.reload(mod)
            assert mod._is_enabled() is False

    def test_enabled_via_true(self):
        with patch.dict(os.environ, {"IMAP_ENABLED": "true"}):
            import importlib
            import app.integrations.email.imap_listener as mod
            importlib.reload(mod)
            assert mod._is_enabled() is True

    def test_enabled_via_1(self):
        with patch.dict(os.environ, {"IMAP_ENABLED": "1"}):
            import importlib
            import app.integrations.email.imap_listener as mod
            importlib.reload(mod)
            assert mod._is_enabled() is True

    def test_enabled_via_yes(self):
        with patch.dict(os.environ, {"IMAP_ENABLED": "yes"}):
            import importlib
            import app.integrations.email.imap_listener as mod
            importlib.reload(mod)
            assert mod._is_enabled() is True

    def test_disabled_via_false(self):
        with patch.dict(os.environ, {"IMAP_ENABLED": "false"}):
            import importlib
            import app.integrations.email.imap_listener as mod
            importlib.reload(mod)
            assert mod._is_enabled() is False


class TestImapListenerGetConfig:
    def test_defaults(self):
        env = {k: v for k, v in os.environ.items()
               if k not in {"IMAP_HOST", "IMAP_PORT", "IMAP_USER",
                             "IMAP_PASSWORD", "IMAP_SSL", "IMAP_MAILBOX"}}
        with patch.dict(os.environ, env, clear=True):
            import importlib
            import app.integrations.email.imap_listener as mod
            importlib.reload(mod)
            cfg = mod._get_config()
        assert cfg["port"] == 993
        assert cfg["ssl"] is True
        assert cfg["mailbox"] == "INBOX"
        assert cfg["host"] == ""

    def test_custom_values(self):
        env = {
            "IMAP_HOST": "imap.corp.com",
            "IMAP_PORT": "143",
            "IMAP_USER": "agent@corp.com",
            "IMAP_PASSWORD": "s3cr3t",
            "IMAP_SSL": "false",
            "IMAP_MAILBOX": "TASKS",
        }
        with patch.dict(os.environ, env):
            import importlib
            import app.integrations.email.imap_listener as mod
            importlib.reload(mod)
            cfg = mod._get_config()
        assert cfg["host"] == "imap.corp.com"
        assert cfg["port"] == 143
        assert cfg["ssl"] is False
        assert cfg["mailbox"] == "TASKS"
        assert cfg["user"] == "agent@corp.com"

    def test_ssl_false_via_zero(self):
        with patch.dict(os.environ, {"IMAP_SSL": "0"}):
            import importlib
            import app.integrations.email.imap_listener as mod
            importlib.reload(mod)
            cfg = mod._get_config()
        assert cfg["ssl"] is False


class TestDecodeHeaderValue:
    def test_plain_ascii(self):
        from app.integrations.email.imap_listener import _decode_header_value
        assert _decode_header_value("Hello World") == "Hello World"

    def test_empty_string(self):
        from app.integrations.email.imap_listener import _decode_header_value
        assert _decode_header_value("") == ""

    def test_multiple_words(self):
        from app.integrations.email.imap_listener import _decode_header_value
        result = _decode_header_value("Re: Action Required")
        assert "Action Required" in result


class TestCheckAndProcessEmails:
    @pytest.mark.asyncio
    async def test_returns_zero_when_disabled(self):
        from app.integrations.email.imap_listener import check_and_process_emails
        with patch.dict(os.environ, {"IMAP_ENABLED": "false"}):
            result = await check_and_process_emails(MagicMock(), MagicMock())
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_host(self):
        env = {"IMAP_ENABLED": "true", "IMAP_HOST": "", "IMAP_USER": "u@x.com"}
        with patch.dict(os.environ, env):
            import importlib
            import app.integrations.email.imap_listener as mod
            importlib.reload(mod)
            result = await mod.check_and_process_emails(MagicMock(), MagicMock())
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_user(self):
        env = {"IMAP_ENABLED": "true", "IMAP_HOST": "imap.x.com", "IMAP_USER": ""}
        with patch.dict(os.environ, env):
            import importlib
            import app.integrations.email.imap_listener as mod
            importlib.reload(mod)
            result = await mod.check_and_process_emails(MagicMock(), MagicMock())
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_aioimaplib_not_installed(self):
        env = {"IMAP_ENABLED": "true", "IMAP_HOST": "imap.x.com", "IMAP_USER": "u@x.com"}
        with patch.dict(os.environ, env):
            saved = sys.modules.get("aioimaplib")
            sys.modules["aioimaplib"] = None  # type: ignore
            try:
                import importlib
                import app.integrations.email.imap_listener as mod
                importlib.reload(mod)
                result = await mod.check_and_process_emails(MagicMock(), MagicMock())
            finally:
                if saved is not None:
                    sys.modules["aioimaplib"] = saved
                else:
                    sys.modules.pop("aioimaplib", None)
        assert result == 0

    @pytest.mark.asyncio
    async def test_handles_imap_connection_exception(self):
        """IMAP connection failure returns 0 gracefully."""
        mock_imap_module = MagicMock()
        mock_imap_module.IMAP4_SSL = MagicMock(side_effect=Exception("Connection refused"))

        env = {
            "IMAP_ENABLED": "true",
            "IMAP_HOST": "imap.example.com",
            "IMAP_USER": "u@example.com",
            "IMAP_SSL": "true",
        }
        with patch.dict(os.environ, env):
            saved = sys.modules.get("aioimaplib")
            sys.modules["aioimaplib"] = mock_imap_module  # type: ignore
            try:
                import importlib
                import app.integrations.email.imap_listener as mod
                importlib.reload(mod)
                result = await mod.check_and_process_emails(MagicMock(), MagicMock())
            finally:
                if saved is not None:
                    sys.modules["aioimaplib"] = saved
                else:
                    sys.modules.pop("aioimaplib", None)
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_unseen_messages(self):
        """search returns empty → 0 processed."""
        mock_imap = AsyncMock()
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock()
        mock_imap.select = AsyncMock()
        mock_imap.search = AsyncMock(return_value=("OK", [b""]))
        mock_imap.logout = AsyncMock()

        mock_imap_module = MagicMock()
        mock_imap_module.IMAP4_SSL = MagicMock(return_value=mock_imap)

        env = {
            "IMAP_ENABLED": "true",
            "IMAP_HOST": "imap.example.com",
            "IMAP_USER": "u@example.com",
            "IMAP_SSL": "true",
        }
        with patch.dict(os.environ, env):
            saved = sys.modules.get("aioimaplib")
            sys.modules["aioimaplib"] = mock_imap_module  # type: ignore
            try:
                import importlib
                import app.integrations.email.imap_listener as mod
                importlib.reload(mod)
                result = await mod.check_and_process_emails(MagicMock(), MagicMock())
            finally:
                if saved is not None:
                    sys.modules["aioimaplib"] = saved
                else:
                    sys.modules.pop("aioimaplib", None)
        assert result == 0

    @pytest.mark.asyncio
    async def test_search_status_not_ok_returns_zero(self):
        """search returns non-OK status → 0 processed."""
        mock_imap = AsyncMock()
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock()
        mock_imap.select = AsyncMock()
        mock_imap.search = AsyncMock(return_value=("NO", [b""]))
        mock_imap.logout = AsyncMock()

        mock_imap_module = MagicMock()
        mock_imap_module.IMAP4_SSL = MagicMock(return_value=mock_imap)

        env = {
            "IMAP_ENABLED": "true",
            "IMAP_HOST": "imap.example.com",
            "IMAP_USER": "u@example.com",
            "IMAP_SSL": "true",
        }
        with patch.dict(os.environ, env):
            saved = sys.modules.get("aioimaplib")
            sys.modules["aioimaplib"] = mock_imap_module  # type: ignore
            try:
                import importlib
                import app.integrations.email.imap_listener as mod
                importlib.reload(mod)
                result = await mod.check_and_process_emails(MagicMock(), MagicMock())
            finally:
                if saved is not None:
                    sys.modules["aioimaplib"] = saved
                else:
                    sys.modules.pop("aioimaplib", None)
        assert result == 0

    @pytest.mark.asyncio
    async def test_processes_plaintext_email(self):
        """Processes a simple plaintext email and submits it as a goal."""
        # Build a real RFC822 message
        msg = email_lib.message.Message()
        msg["Subject"] = "Deploy the new feature"
        msg["From"] = "boss@company.com"
        msg.set_payload("Please deploy feature X to production tonight.")

        mock_imap = AsyncMock()
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock()
        mock_imap.select = AsyncMock()
        mock_imap.search = AsyncMock(return_value=("OK", [b"1"]))
        mock_imap.fetch = AsyncMock(return_value=("OK", [None, msg.as_bytes()]))
        mock_imap.store = AsyncMock()
        mock_imap.logout = AsyncMock()

        mock_goal_service = AsyncMock()
        mock_goal_service.submit_goal = AsyncMock(return_value={"goal_id": "g-001"})

        mock_imap_module = MagicMock()
        mock_imap_module.IMAP4_SSL = MagicMock(return_value=mock_imap)

        env = {
            "IMAP_ENABLED": "true",
            "IMAP_HOST": "imap.example.com",
            "IMAP_USER": "u@example.com",
            "IMAP_SSL": "true",
        }
        with patch.dict(os.environ, env):
            saved = sys.modules.get("aioimaplib")
            sys.modules["aioimaplib"] = mock_imap_module  # type: ignore
            try:
                import importlib
                import app.integrations.email.imap_listener as mod
                importlib.reload(mod)
                result = await mod.check_and_process_emails(
                    mock_goal_service, MagicMock()
                )
            finally:
                if saved is not None:
                    sys.modules["aioimaplib"] = saved
                else:
                    sys.modules.pop("aioimaplib", None)

        assert result == 1
        mock_goal_service.submit_goal.assert_awaited_once()
        call_kwargs = mock_goal_service.submit_goal.call_args
        assert "Deploy the new feature" in call_kwargs.kwargs.get("goal", "")

    @pytest.mark.asyncio
    async def test_processes_multipart_email(self):
        """Extracts text/plain body from multipart email."""
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Run the ETL pipeline"
        msg["From"] = "data@company.com"
        msg.attach(MIMEText("Run the ETL job for today's data", "plain"))
        msg.attach(MIMEText("<p>Run the ETL job</p>", "html"))

        mock_imap = AsyncMock()
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock()
        mock_imap.select = AsyncMock()
        mock_imap.search = AsyncMock(return_value=("OK", [b"1"]))
        mock_imap.fetch = AsyncMock(return_value=("OK", [None, msg.as_bytes()]))
        mock_imap.store = AsyncMock()
        mock_imap.logout = AsyncMock()

        mock_goal_service = AsyncMock()
        mock_goal_service.submit_goal = AsyncMock(return_value={"goal_id": "g-002"})

        mock_imap_module = MagicMock()
        mock_imap_module.IMAP4_SSL = MagicMock(return_value=mock_imap)

        env = {
            "IMAP_ENABLED": "true",
            "IMAP_HOST": "imap.example.com",
            "IMAP_USER": "u@example.com",
            "IMAP_SSL": "true",
        }
        with patch.dict(os.environ, env):
            saved = sys.modules.get("aioimaplib")
            sys.modules["aioimaplib"] = mock_imap_module  # type: ignore
            try:
                import importlib
                import app.integrations.email.imap_listener as mod
                importlib.reload(mod)
                result = await mod.check_and_process_emails(
                    mock_goal_service, MagicMock()
                )
            finally:
                if saved is not None:
                    sys.modules["aioimaplib"] = saved
                else:
                    sys.modules.pop("aioimaplib", None)

        assert result == 1

    @pytest.mark.asyncio
    async def test_fetch_failure_skips_email(self):
        """fetch returning non-OK status skips that email."""
        mock_imap = AsyncMock()
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock()
        mock_imap.select = AsyncMock()
        mock_imap.search = AsyncMock(return_value=("OK", [b"1 2"]))
        mock_imap.fetch = AsyncMock(return_value=("NO", []))
        mock_imap.logout = AsyncMock()

        mock_goal_service = AsyncMock()
        mock_imap_module = MagicMock()
        mock_imap_module.IMAP4_SSL = MagicMock(return_value=mock_imap)

        env = {
            "IMAP_ENABLED": "true",
            "IMAP_HOST": "imap.example.com",
            "IMAP_USER": "u@example.com",
            "IMAP_SSL": "true",
        }
        with patch.dict(os.environ, env):
            saved = sys.modules.get("aioimaplib")
            sys.modules["aioimaplib"] = mock_imap_module  # type: ignore
            try:
                import importlib
                import app.integrations.email.imap_listener as mod
                importlib.reload(mod)
                result = await mod.check_and_process_emails(
                    mock_goal_service, MagicMock()
                )
            finally:
                if saved is not None:
                    sys.modules["aioimaplib"] = saved
                else:
                    sys.modules.pop("aioimaplib", None)

        assert result == 0

    @pytest.mark.asyncio
    async def test_goal_submission_failure_continues(self):
        """Goal submission exception is caught; processing continues."""
        msg = email_lib.message.Message()
        msg["Subject"] = "Test Subject"
        msg["From"] = "test@x.com"
        msg.set_payload("Some body text")

        mock_imap = AsyncMock()
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock()
        mock_imap.select = AsyncMock()
        mock_imap.search = AsyncMock(return_value=("OK", [b"1"]))
        mock_imap.fetch = AsyncMock(return_value=("OK", [None, msg.as_bytes()]))
        mock_imap.store = AsyncMock()
        mock_imap.logout = AsyncMock()

        mock_goal_service = AsyncMock()
        mock_goal_service.submit_goal = AsyncMock(side_effect=Exception("DB unavailable"))

        mock_imap_module = MagicMock()
        mock_imap_module.IMAP4_SSL = MagicMock(return_value=mock_imap)

        env = {
            "IMAP_ENABLED": "true",
            "IMAP_HOST": "imap.example.com",
            "IMAP_USER": "u@example.com",
            "IMAP_SSL": "true",
        }
        with patch.dict(os.environ, env):
            saved = sys.modules.get("aioimaplib")
            sys.modules["aioimaplib"] = mock_imap_module  # type: ignore
            try:
                import importlib
                import app.integrations.email.imap_listener as mod
                importlib.reload(mod)
                result = await mod.check_and_process_emails(
                    mock_goal_service, MagicMock()
                )
            finally:
                if saved is not None:
                    sys.modules["aioimaplib"] = saved
                else:
                    sys.modules.pop("aioimaplib", None)

        # Error was caught; 0 processed but no exception raised
        assert result == 0

    @pytest.mark.asyncio
    async def test_uses_starttls_when_ssl_false(self):
        """Non-SSL config → IMAP4 (STARTTLS) class is used."""
        mock_imap = AsyncMock()
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock()
        mock_imap.select = AsyncMock()
        mock_imap.search = AsyncMock(return_value=("OK", [b""]))
        mock_imap.logout = AsyncMock()

        mock_imap_module = MagicMock()
        mock_imap_module.IMAP4 = MagicMock(return_value=mock_imap)
        mock_imap_module.IMAP4_SSL = MagicMock()

        env = {
            "IMAP_ENABLED": "true",
            "IMAP_HOST": "imap.example.com",
            "IMAP_USER": "u@example.com",
            "IMAP_SSL": "false",
        }
        with patch.dict(os.environ, env):
            saved = sys.modules.get("aioimaplib")
            sys.modules["aioimaplib"] = mock_imap_module  # type: ignore
            try:
                import importlib
                import app.integrations.email.imap_listener as mod
                importlib.reload(mod)
                await mod.check_and_process_emails(MagicMock(), MagicMock())
            finally:
                if saved is not None:
                    sys.modules["aioimaplib"] = saved
                else:
                    sys.modules.pop("aioimaplib", None)

        mock_imap_module.IMAP4.assert_called_once()
        mock_imap_module.IMAP4_SSL.assert_not_called()


# ── approval_sender: signing/verification ────────────────────────────────────

class TestApprovalSenderSigning:
    def test_sign_produces_32_char_hex(self):
        from app.integrations.email.approval_sender import _sign
        sig = _sign("req-123", "approve")
        assert len(sig) == 32
        assert all(c in "0123456789abcdef" for c in sig)

    def test_verify_correct_signature(self):
        from app.integrations.email.approval_sender import _sign, _verify
        sig = _sign("req-abc", "reject")
        assert _verify("req-abc", "reject", sig) is True

    def test_verify_wrong_signature(self):
        from app.integrations.email.approval_sender import _verify
        assert _verify("req-abc", "approve", "aaaa" * 8) is False

    def test_verify_tampered_action(self):
        from app.integrations.email.approval_sender import _sign, _verify
        sig = _sign("req-abc", "approve")
        assert _verify("req-abc", "reject", sig) is False

    def test_verify_tampered_request_id(self):
        from app.integrations.email.approval_sender import _sign, _verify
        sig = _sign("req-original", "approve")
        assert _verify("req-tampered", "approve", sig) is False

    def test_sign_uses_env_secret(self):
        from app.integrations.email.approval_sender import _sign
        with patch.dict(os.environ, {"HITL_EMAIL_SECRET": "secret-A"}):
            sig_a = _sign("req-1", "approve")
        with patch.dict(os.environ, {"HITL_EMAIL_SECRET": "secret-B"}):
            sig_b = _sign("req-1", "approve")
        assert sig_a != sig_b

    def test_sign_default_secret_when_env_not_set(self):
        from app.integrations.email.approval_sender import _sign
        env = {k: v for k, v in os.environ.items() if k != "HITL_EMAIL_SECRET"}
        with patch.dict(os.environ, env, clear=True):
            sig = _sign("req-1", "approve")
        assert len(sig) == 32


class TestSendApprovalEmail:
    @pytest.mark.asyncio
    async def test_send_success(self):
        """Happy path: aiosmtplib.send is awaited, returns True."""
        mock_aiosmtplib = MagicMock()
        mock_aiosmtplib.send = AsyncMock()

        saved = sys.modules.get("aiosmtplib")
        sys.modules["aiosmtplib"] = mock_aiosmtplib  # type: ignore
        try:
            import importlib
            import app.integrations.email.approval_sender as mod
            importlib.reload(mod)
            result = await mod.send_approval_email(
                to_email="approver@example.com",
                goal_description="Deploy to production",
                step_description="Run DB migration",
                request_id="req-success",
                frontend_url="http://app.test",
                smtp_host="localhost",
                smtp_port=1025,
            )
        finally:
            if saved is not None:
                sys.modules["aiosmtplib"] = saved
            else:
                sys.modules.pop("aiosmtplib", None)

        assert result is True
        mock_aiosmtplib.send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_import_error_returns_false(self):
        """Returns False when aiosmtplib is not installed."""
        saved = sys.modules.get("aiosmtplib")
        sys.modules["aiosmtplib"] = None  # type: ignore
        try:
            import importlib
            import app.integrations.email.approval_sender as mod
            importlib.reload(mod)
            result = await mod.send_approval_email(
                to_email="a@b.com",
                goal_description="Do task",
                step_description="Delete everything",
                request_id="req-no-lib",
                frontend_url="http://fe.test",
            )
        finally:
            if saved is not None:
                sys.modules["aiosmtplib"] = saved
            else:
                sys.modules.pop("aiosmtplib", None)

        assert result is False

    @pytest.mark.asyncio
    async def test_send_smtp_error_returns_false(self):
        """Returns False on SMTP error."""
        mock_aiosmtplib = MagicMock()
        mock_aiosmtplib.send = AsyncMock(side_effect=Exception("Connection refused"))

        saved = sys.modules.get("aiosmtplib")
        sys.modules["aiosmtplib"] = mock_aiosmtplib  # type: ignore
        try:
            import importlib
            import app.integrations.email.approval_sender as mod
            importlib.reload(mod)
            result = await mod.send_approval_email(
                to_email="a@b.com",
                goal_description="Do task",
                step_description="Delete prod DB",
                request_id="req-smtp-err",
                frontend_url="http://fe.test",
            )
        finally:
            if saved is not None:
                sys.modules["aiosmtplib"] = saved
            else:
                sys.modules.pop("aiosmtplib", None)

        assert result is False

    @pytest.mark.asyncio
    async def test_email_contains_approve_reject_urls(self):
        """HTML email body contains approve/reject links with request_id."""
        captured_messages = []

        async def capture(msg, **kwargs):
            captured_messages.append(msg)

        mock_aiosmtplib = MagicMock()
        mock_aiosmtplib.send = capture

        saved = sys.modules.get("aiosmtplib")
        sys.modules["aiosmtplib"] = mock_aiosmtplib  # type: ignore
        try:
            import importlib
            import app.integrations.email.approval_sender as mod
            importlib.reload(mod)
            await mod.send_approval_email(
                to_email="approver@test.com",
                goal_description="Scale up cluster",
                step_description="Terminate 50% of VMs",
                request_id="req-html-check",
                frontend_url="http://myapp.local",
            )
        finally:
            if saved is not None:
                sys.modules["aiosmtplib"] = saved
            else:
                sys.modules.pop("aiosmtplib", None)

        assert len(captured_messages) == 1
        msg = captured_messages[0]
        # Get the HTML part
        html_body = ""
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                html_body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                break

        if not html_body:
            # Try direct payload
            html_body = str(msg.get_payload())

        assert "req-html-check" in html_body
        assert "approve" in html_body.lower()
        assert "reject" in html_body.lower()

    @pytest.mark.asyncio
    async def test_email_subject_contains_step_description(self):
        """Email subject truncates and includes step description."""
        captured_messages = []

        async def capture(msg, **kwargs):
            captured_messages.append(msg)

        mock_aiosmtplib = MagicMock()
        mock_aiosmtplib.send = capture

        saved = sys.modules.get("aiosmtplib")
        sys.modules["aiosmtplib"] = mock_aiosmtplib  # type: ignore
        try:
            import importlib
            import app.integrations.email.approval_sender as mod
            importlib.reload(mod)
            await mod.send_approval_email(
                to_email="mgr@test.com",
                goal_description="Goal",
                step_description="Deploy hotfix to prod",
                request_id="req-subj",
                frontend_url="http://app.test",
            )
        finally:
            if saved is not None:
                sys.modules["aiosmtplib"] = saved
            else:
                sys.modules.pop("aiosmtplib", None)

        assert len(captured_messages) == 1
        subject = captured_messages[0]["Subject"]
        assert "Deploy hotfix to prod" in subject
        assert "AgentVerse" in subject
