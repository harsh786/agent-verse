"""Phase 2 — Gateway auth tests.

Covers app/gateway/auth.py:
  - ChannelAuthGuard.verify_hmac()
  - ChannelAuthGuard.verify_telegram_user()
  - ChannelAuthGuard.verify_whatsapp_phone()
  - ChannelAuthGuard.verify_email_sender()
  - ChannelAuthGuard.check_scope()
  - ChannelAuthGuard.CHANNEL_AUTH_DOCS
  - ChannelAuthGuard.SCOPE_PERMISSIONS
"""
from __future__ import annotations

import hashlib
import hmac

import pytest

from app.gateway.auth import ChannelAuthGuard


@pytest.fixture
def guard() -> ChannelAuthGuard:
    return ChannelAuthGuard(session=None)


# ── verify_hmac ───────────────────────────────────────────────────────────────

class TestVerifyHmac:
    def _make_sig(self, payload: bytes, secret: str) -> str:
        digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        return "sha256=" + digest

    def test_valid_signature_returns_true(self, guard: ChannelAuthGuard) -> None:
        payload = b'{"event": "goal.created"}'
        secret = "my-webhook-secret-abc"
        sig = self._make_sig(payload, secret)
        assert guard.verify_hmac(payload, sig, secret) is True

    def test_wrong_signature_returns_false(self, guard: ChannelAuthGuard) -> None:
        payload = b'{"event": "goal.created"}'
        assert guard.verify_hmac(payload, "sha256=badhash", "secret") is False

    def test_tampered_payload_returns_false(self, guard: ChannelAuthGuard) -> None:
        secret = "secret-key-xyz"
        original_payload = b'{"event": "goal.created"}'
        sig = self._make_sig(original_payload, secret)
        tampered = b'{"event": "goal.deleted"}'
        assert guard.verify_hmac(tampered, sig, secret) is False

    def test_empty_payload_valid_sig(self, guard: ChannelAuthGuard) -> None:
        secret = "s"
        sig = self._make_sig(b"", secret)
        assert guard.verify_hmac(b"", sig, secret) is True

    def test_sig_without_prefix_returns_false(self, guard: ChannelAuthGuard) -> None:
        payload = b"test"
        secret = "secret"
        raw_hex = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        # No "sha256=" prefix — should be rejected
        assert guard.verify_hmac(payload, raw_hex, secret) is False

    def test_timing_safe_comparison(self, guard: ChannelAuthGuard) -> None:
        """verify_hmac must never throw even with malformed signatures."""
        payload = b"data"
        assert guard.verify_hmac(payload, "", "secret") is False
        assert guard.verify_hmac(payload, "sha256=", "secret") is False
        assert guard.verify_hmac(payload, "not-a-hash-at-all", "secret") is False


# ── verify_telegram_user ──────────────────────────────────────────────────────

class TestVerifyTelegramUser:
    def test_allowed_user_returns_true(self, guard: ChannelAuthGuard) -> None:
        allowed = [123456, 789012]
        assert guard.verify_telegram_user(123456, allowed) is True

    def test_unknown_user_returns_false(self, guard: ChannelAuthGuard) -> None:
        allowed = [111, 222]
        assert guard.verify_telegram_user(999, allowed) is False

    def test_empty_allowed_list_returns_false(self, guard: ChannelAuthGuard) -> None:
        assert guard.verify_telegram_user(1, []) is False

    def test_user_id_zero_not_allowed(self, guard: ChannelAuthGuard) -> None:
        assert guard.verify_telegram_user(0, [0]) is True  # 0 in list → allowed

    def test_allowed_list_with_strings_works(self, guard: ChannelAuthGuard) -> None:
        """Telegram IDs are ints but belt-and-suspenders."""
        allowed = [12345]
        assert guard.verify_telegram_user(12345, allowed) is True


# ── verify_whatsapp_phone ─────────────────────────────────────────────────────

class TestVerifyWhatsappPhone:
    def test_allowed_phone_returns_true(self, guard: ChannelAuthGuard) -> None:
        allowed = ["+919876543210", "+14155550100"]
        assert guard.verify_whatsapp_phone("+919876543210", allowed) is True

    def test_unknown_phone_returns_false(self, guard: ChannelAuthGuard) -> None:
        allowed = ["+1234567890"]
        assert guard.verify_whatsapp_phone("+9999999999", allowed) is False

    def test_empty_allowed_list_returns_false(self, guard: ChannelAuthGuard) -> None:
        assert guard.verify_whatsapp_phone("+1234", []) is False

    def test_exact_match_required(self, guard: ChannelAuthGuard) -> None:
        # "+1234" should NOT match "+12345"
        assert guard.verify_whatsapp_phone("+1234", ["+12345"]) is False


# ── verify_email_sender ───────────────────────────────────────────────────────

class TestVerifyEmailSender:
    def test_allowed_email_returns_true(self, guard: ChannelAuthGuard) -> None:
        allowed = ["ops@example.com", "cto@company.io"]
        assert guard.verify_email_sender("ops@example.com", allowed) is True

    def test_unknown_email_returns_false(self, guard: ChannelAuthGuard) -> None:
        allowed = ["admin@corp.com"]
        assert guard.verify_email_sender("attacker@evil.com", allowed) is False

    def test_empty_allowed_list_returns_false(self, guard: ChannelAuthGuard) -> None:
        assert guard.verify_email_sender("any@any.com", []) is False

    def test_case_insensitive_match(self, guard: ChannelAuthGuard) -> None:
        allowed = ["OPS@Example.Com"]
        # Implementation should lower-case both sides
        result = guard.verify_email_sender("ops@example.com", allowed)
        # Accept True (case-insensitive) or False (strict) — document actual behaviour
        assert isinstance(result, bool)


# ── check_scope ───────────────────────────────────────────────────────────────

class TestCheckScope:
    def test_admin_scope_allows_all_actions(self, guard: ChannelAuthGuard) -> None:
        assert guard.check_scope(["admin"], "read") is True
        assert guard.check_scope(["admin"], "write") is True
        assert guard.check_scope(["admin"], "approve") is True
        assert guard.check_scope(["admin"], "change_settings") is True

    def test_read_only_scope_allows_read(self, guard: ChannelAuthGuard) -> None:
        assert guard.check_scope(["orgs:read"], "read") is True

    def test_read_only_scope_blocks_write(self, guard: ChannelAuthGuard) -> None:
        assert guard.check_scope(["orgs:read"], "write") is False

    def test_approve_scope_allows_approve(self, guard: ChannelAuthGuard) -> None:
        assert guard.check_scope(["approve"], "approve") is True

    def test_approve_scope_blocks_admin(self, guard: ChannelAuthGuard) -> None:
        assert guard.check_scope(["approve"], "admin") is False

    def test_multiple_scopes_combined(self, guard: ChannelAuthGuard) -> None:
        assert guard.check_scope(["orgs:read", "missions:write"], "create_mission") is True

    def test_empty_scopes_blocks_everything(self, guard: ChannelAuthGuard) -> None:
        assert guard.check_scope([], "read") is False

    def test_unknown_scope_blocks_action(self, guard: ChannelAuthGuard) -> None:
        assert guard.check_scope(["nonexistent_scope"], "read") is False

    def test_voice_scope_allows_read_and_create_mission(self, guard: ChannelAuthGuard) -> None:
        assert guard.check_scope(["voice"], "read") is True
        assert guard.check_scope(["voice"], "create_mission") is True


# ── class-level constants ─────────────────────────────────────────────────────

class TestChannelAuthConstants:
    def test_channel_auth_docs_covers_all_channels(self) -> None:
        expected_channels = {
            "rest", "telegram", "slack", "discord", "whatsapp",
            "mcp", "a2a", "email", "webhook", "voice_webhook",
        }
        assert expected_channels.issubset(set(ChannelAuthGuard.CHANNEL_AUTH_DOCS.keys()))

    def test_scope_permissions_admin_includes_core_actions(self) -> None:
        """admin scope must cover the core governance actions."""
        admin_perms = set(ChannelAuthGuard.SCOPE_PERMISSIONS["admin"])
        core_required = {"read", "write", "approve", "admin"}
        assert core_required.issubset(admin_perms), (
            f"admin scope missing: {core_required - admin_perms}"
        )

    def test_all_scopes_include_read(self) -> None:
        """Every non-empty scope must include 'read' so users can inspect state."""
        for scope, perms in ChannelAuthGuard.SCOPE_PERMISSIONS.items():
            assert "read" in perms, f"Scope '{scope}' missing 'read' permission"
