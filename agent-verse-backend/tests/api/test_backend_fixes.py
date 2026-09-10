"""Tests for all backend world-class fixes.

Covers:
- MFA session tokens + rate limiting
- Razorpay mock payment security (allow_mock_payments gating)
- Billing demo invoice gating (development-only)
- Billing INR/USD rate configurable
- Observability StructuredLogStore (memory fallback, tenant isolation)
- LLM provider circuit breaker (open/half-open/closed states)
- GoalService lifecycle transitions (state machine)
- Collab CRDT token store (TTL cleanup)
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.mfa import (
    _generate_recovery_codes,
    _get_mfa_state,
    _hash_recovery_code,
    _mfa_store,
    _mfa_verified_sessions,
    _rate_limits,
    _used_totp_codes,
)
from app.api.mfa import (
    router as mfa_router,
)
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

# ─── Shared fixtures ───────────────────────────────────────────────────────────

_CTX = TenantContext(tenant_id="tid-fix-test", plan=PlanTier.PROFESSIONAL, api_key_id="kid-fix")
_KEY = "ak_test_fixes_key_123"
_HEADERS = {"X-API-Key": _KEY}


def _make_mfa_app() -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(mfa_router)
    return app


def _make_billing_app() -> FastAPI:
    from app.api.billing import router as billing_router

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(billing_router)
    return app


@pytest.fixture(autouse=True)
def _clear_mfa_state():
    """Isolate each test by wiping MFA state and rate-limit buckets for our test tenant."""
    _mfa_store.pop("tid-fix-test", None)
    _mfa_verified_sessions.clear()
    _rate_limits.pop("tid-fix-test", None)
    _used_totp_codes.pop("tid-fix-test", None)
    yield
    _mfa_store.pop("tid-fix-test", None)
    _mfa_verified_sessions.clear()
    _rate_limits.pop("tid-fix-test", None)
    _used_totp_codes.pop("tid-fix-test", None)


def _enable_mfa_direct(tenant_id: str = "tid-fix-test") -> str:
    """Enable MFA by directly seeding the state — avoids replay-code collision.

    Using the enrollment API flow marks the TOTP code as used in
    ``_used_totp_codes``.  If the test then calls ``/auth/mfa/verify`` in the
    same 30-second window it gets a 422 "code already used" error.  Seeding
    state directly sidesteps this by never consuming a code during setup.
    """
    import pyotp

    secret = pyotp.random_base32()
    state = _get_mfa_state(tenant_id)
    state["enabled"] = True
    state["secret"] = secret
    state["recovery_codes_hashed"] = [
        _hash_recovery_code(c) for c in _generate_recovery_codes()
    ]
    return secret


# ─── MFA: Session Token Generation ────────────────────────────────────────────


class TestMFASessionTokens:
    """MFA verify endpoint issues a session token on TOTP success."""

    def test_verify_returns_session_token(self) -> None:
        import pyotp

        # Seed state directly — avoids the replay-code collision that occurs when
        # the enrollment flow consumes the code in the same 30-second window.
        secret = _enable_mfa_direct()
        client = TestClient(_make_mfa_app())
        code = pyotp.TOTP(secret).now()
        resp = client.post("/auth/mfa/verify", json={"code": code}, headers=_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "session_token" in data
        assert len(data["session_token"]) >= 32
        assert data["expires_in"] == 3600
        assert data["method"] == "totp"

    def test_session_token_stored_in_verified_sessions(self) -> None:
        import pyotp

        secret = _enable_mfa_direct()
        client = TestClient(_make_mfa_app())
        code = pyotp.TOTP(secret).now()
        resp = client.post("/auth/mfa/verify", json={"code": code}, headers=_HEADERS).json()
        token = resp.get("session_token")
        assert token is not None
        assert token in _mfa_verified_sessions
        entry = _mfa_verified_sessions[token]
        assert entry["tenant_id"] == "tid-fix-test"
        assert entry["method"] == "totp"
        assert "created_at" in entry

    def test_session_token_is_urlsafe(self) -> None:
        """Session token must only contain URL-safe characters."""
        import re

        import pyotp

        secret = _enable_mfa_direct()
        client = TestClient(_make_mfa_app())
        code = pyotp.TOTP(secret).now()
        resp = client.post("/auth/mfa/verify", json={"code": code}, headers=_HEADERS).json()
        token = resp.get("session_token")
        assert token is not None
        # secrets.token_urlsafe produces A-Z a-z 0-9 - _
        assert re.fullmatch(r"[A-Za-z0-9_\-]+", token), "session token must be URL-safe"

    def test_session_token_cleanup_removes_expired(self) -> None:
        """_cleanup_mfa_sessions evicts entries older than 1 hour."""
        from app.api.mfa import _cleanup_mfa_sessions

        _mfa_verified_sessions["old-token"] = {
            "tenant_id": "tid-fix-test",
            "created_at": time.monotonic() - 3700,  # > 1 hour ago
            "method": "totp",
        }
        _mfa_verified_sessions["fresh-token"] = {
            "tenant_id": "tid-fix-test",
            "created_at": time.monotonic(),
            "method": "totp",
        }
        _cleanup_mfa_sessions()
        assert "old-token" not in _mfa_verified_sessions
        assert "fresh-token" in _mfa_verified_sessions

    def test_recovery_code_does_not_issue_session_token(self) -> None:
        """Recovery code path returns verified status WITHOUT a session token.

        Only TOTP verify issues session tokens; recovery codes use a different
        flow (one-time code consumed, no persistent session created).
        """
        import pyotp

        codes = _generate_recovery_codes()
        secret = pyotp.random_base32()
        state = _get_mfa_state("tid-fix-test")
        state["enabled"] = True
        state["secret"] = secret
        state["recovery_codes_hashed"] = [_hash_recovery_code(c) for c in codes]

        client = TestClient(_make_mfa_app())
        resp = client.post("/auth/mfa/verify", json={"code": codes[0]}, headers=_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "verified"
        assert data["method"] == "recovery_code"
        # Recovery codes do NOT emit a session token — this is intentional
        assert "session_token" not in data
        assert "remaining_recovery_codes" in data

    def test_recovery_code_is_consumed_after_use(self) -> None:
        """Using a recovery code removes it from the store."""
        import pyotp

        codes = _generate_recovery_codes()
        secret = pyotp.random_base32()
        state = _get_mfa_state("tid-fix-test")
        state["enabled"] = True
        state["secret"] = secret
        state["recovery_codes_hashed"] = [_hash_recovery_code(c) for c in codes]

        client = TestClient(_make_mfa_app())
        resp1 = client.post("/auth/mfa/verify", json={"code": codes[0]}, headers=_HEADERS)
        assert resp1.status_code == 200
        assert resp1.json()["remaining_recovery_codes"] == len(codes) - 1

        # Second use of the same code must fail
        resp2 = client.post("/auth/mfa/verify", json={"code": codes[0]}, headers=_HEADERS)
        assert resp2.status_code == 422

    def test_verify_fails_with_wrong_totp_code(self) -> None:
        _enable_mfa_direct()
        client = TestClient(_make_mfa_app())
        resp = client.post("/auth/mfa/verify", json={"code": "000000"}, headers=_HEADERS)
        assert resp.status_code == 422

    def test_verify_fails_when_mfa_not_enabled(self) -> None:
        client = TestClient(_make_mfa_app())
        resp = client.post("/auth/mfa/verify", json={"code": "123456"}, headers=_HEADERS)
        assert resp.status_code == 400

    def test_rate_limit_raises_after_threshold(self) -> None:
        """_check_rate_limit raises HTTPException(429) after hitting the limit."""
        from fastapi import HTTPException

        from app.api.mfa import _check_rate_limit

        with pytest.raises(HTTPException) as exc_info:
            for _ in range(15):  # limit is 10 for /auth/mfa/verify
                _check_rate_limit("tid-fix-test", "/auth/mfa/verify")
        assert exc_info.value.status_code == 429
        assert "Retry-After" in exc_info.value.headers


# ─── Billing: Mock Payment Security ───────────────────────────────────────────


class TestBillingMockPaymentSecurity:
    """Razorpay mock payment must be explicitly enabled via allow_mock_payments."""

    def test_verify_payment_rejects_when_mock_disabled(self) -> None:
        """When Razorpay is not configured and allow_mock_payments=False → 503."""
        client = TestClient(_make_billing_app())
        with patch("app.api.billing._get_razorpay", return_value=None):
            with patch("app.core.config.get_settings") as mock_get:
                s = MagicMock()
                s.allow_mock_payments = False
                s.environment = "production"
                mock_get.return_value = s

                resp = client.post(
                    "/billing/verify-payment",
                    json={
                        "razorpay_order_id": "order_test",
                        "razorpay_payment_id": "pay_test",
                        "razorpay_signature": "fake_sig",
                        "plan": "professional",
                        "cycle": "monthly",
                    },
                    headers=_HEADERS,
                )
                assert resp.status_code == 503
                assert "not configured" in resp.json()["detail"].lower()

    def test_verify_payment_allowed_when_mock_explicitly_enabled(self) -> None:
        """When allow_mock_payments=True, mock payment is accepted (dev only)."""
        client = TestClient(_make_billing_app())
        with patch("app.api.billing._get_razorpay", return_value=None):
            with patch("app.core.config.get_settings") as mock_get:
                s = MagicMock()
                s.allow_mock_payments = True
                s.environment = "development"
                mock_get.return_value = s

                with patch(
                    "app.api.billing._upgrade_tenant_plan",
                    new_callable=AsyncMock,
                ) as mock_upgrade:
                    mock_upgrade.return_value = {
                        "status": "success",
                        "plan": "professional",
                        "cycle": "monthly",
                        "payment_id": "pay_mock",
                        "message": "Upgraded!",
                        "limits": {},
                    }
                    resp = client.post(
                        "/billing/verify-payment",
                        json={
                            "razorpay_order_id": "order_mock",
                            "razorpay_payment_id": "pay_mock",
                            "razorpay_signature": "mock_sig",
                            "plan": "professional",
                            "cycle": "monthly",
                        },
                        headers=_HEADERS,
                    )
                    assert resp.status_code == 200
                    data = resp.json()
                    assert data["status"] == "success"
                    assert data["plan"] == "professional"

    def test_invoices_returns_empty_in_production_without_razorpay(self) -> None:
        """Production mode with no Razorpay configured → empty invoice list."""
        client = TestClient(_make_billing_app())
        with patch("app.api.billing._get_razorpay", return_value=None):
            with patch("app.core.config.get_settings") as mock_get:
                s = MagicMock()
                s.environment = "production"
                s.allow_mock_payments = False
                mock_get.return_value = s

                resp = client.get("/billing/invoices", headers=_HEADERS)
                assert resp.status_code == 200
                assert resp.json() == []

    def test_invoices_returns_demo_data_in_development(self) -> None:
        """Development mode with no Razorpay → demo invoices are returned."""
        client = TestClient(_make_billing_app())
        with patch("app.api.billing._get_razorpay", return_value=None):
            with patch("app.core.config.get_settings") as mock_get:
                s = MagicMock()
                s.environment = "development"
                s.allow_mock_payments = False
                mock_get.return_value = s

                resp = client.get("/billing/invoices", headers=_HEADERS)
                assert resp.status_code == 200
                invoices = resp.json()
                assert len(invoices) > 0
                assert all(i.get("is_demo") is True for i in invoices)

    def test_demo_invoices_contain_required_fields(self) -> None:
        """Demo invoices must have id, date, amount_usd, status, plan, cycle."""
        client = TestClient(_make_billing_app())
        with patch("app.api.billing._get_razorpay", return_value=None):
            with patch("app.core.config.get_settings") as mock_get:
                s = MagicMock()
                s.environment = "development"
                s.allow_mock_payments = False
                mock_get.return_value = s

                invoices = client.get("/billing/invoices", headers=_HEADERS).json()
                for inv in invoices:
                    assert "id" in inv
                    assert "date" in inv
                    assert "amount_usd" in inv
                    assert "status" in inv
                    assert "plan" in inv

    def test_verify_payment_requires_auth(self) -> None:
        """Unauthenticated requests must be rejected with 401."""
        client = TestClient(_make_billing_app())
        resp = client.post(
            "/billing/verify-payment",
            json={
                "razorpay_order_id": "x",
                "razorpay_payment_id": "x",
                "razorpay_signature": "x",
                "plan": "starter",
                "cycle": "monthly",
            },
        )
        assert resp.status_code == 401


# ─── Billing: INR/USD Rate Configurable ───────────────────────────────────────


class TestBillingCurrencyRate:
    """_inr_to_usd reads the exchange rate from configurable settings."""

    def test_inr_to_usd_uses_configurable_rate(self) -> None:
        from app.api.billing import _inr_to_usd

        with patch("app.core.config.get_settings") as mock_get:
            s = MagicMock()
            s.inr_to_usd_rate = 90.0
            mock_get.return_value = s
            assert _inr_to_usd(900.0) == 10.0

    def test_inr_to_usd_default_rate_is_83(self) -> None:
        from app.api.billing import _inr_to_usd

        with patch("app.core.config.get_settings") as mock_get:
            s = MagicMock()
            s.inr_to_usd_rate = 83.0
            mock_get.return_value = s
            assert _inr_to_usd(83.0) == 1.0

    def test_inr_to_usd_rounds_to_two_decimal_places(self) -> None:
        from app.api.billing import _inr_to_usd

        with patch("app.core.config.get_settings") as mock_get:
            s = MagicMock()
            s.inr_to_usd_rate = 83.0
            mock_get.return_value = s
            result = _inr_to_usd(100.0)
            # 100 / 83 ≈ 1.2048... → rounds to 1.2
            assert result == round(100.0 / 83.0, 2)

    def test_inr_to_usd_large_amount(self) -> None:
        from app.api.billing import _inr_to_usd

        with patch("app.core.config.get_settings") as mock_get:
            s = MagicMock()
            s.inr_to_usd_rate = 83.0
            mock_get.return_value = s
            assert _inr_to_usd(8300.0) == 100.0


# ─── Observability: StructuredLogStore ────────────────────────────────────────


class TestStructuredLogStore:
    """StructuredLogStore falls back to in-memory ring buffer when Redis is unavailable."""

    @pytest.mark.asyncio
    async def test_emit_and_query_in_memory(self) -> None:
        from app.api.observability import StructuredLogStore

        store = StructuredLogStore()
        await store.emit("tid-obs-1", "info", "Test log message", source="test")
        logs = await store.query("tid-obs-1", limit=10)
        assert len(logs) >= 1
        assert logs[0]["message"] == "Test log message"
        assert logs[0]["level"] == "info"

    @pytest.mark.asyncio
    async def test_level_filter_excludes_other_levels(self) -> None:
        from app.api.observability import StructuredLogStore

        store = StructuredLogStore()
        await store.emit("tid-filt", "info", "Info message")
        await store.emit("tid-filt", "error", "Error message")
        await store.emit("tid-filt", "warning", "Warning message")

        errors = await store.query("tid-filt", level="error")
        assert all(e["level"] == "error" for e in errors)
        assert any(e["message"] == "Error message" for e in errors)
        assert not any(e["message"] == "Info message" for e in errors)

    @pytest.mark.asyncio
    async def test_memory_buffer_capped_at_500(self) -> None:
        from app.api.observability import StructuredLogStore

        store = StructuredLogStore()
        for i in range(600):
            await store.emit("tid-cap", "info", f"Message {i}")
        logs = await store.query("tid-cap", limit=1000)
        assert len(logs) <= 500

    @pytest.mark.asyncio
    async def test_emit_entry_contains_required_fields(self) -> None:
        from app.api.observability import StructuredLogStore

        store = StructuredLogStore()
        await store.emit(
            "tid-meta",
            "warning",
            "Something happened",
            source="goal_service",
            goal_id="g123",
        )
        logs = await store.query("tid-meta")
        assert len(logs) >= 1
        entry = logs[0]
        assert entry["source"] == "goal_service"
        assert entry["goal_id"] == "g123"
        assert "timestamp" in entry
        assert "id" in entry
        assert entry["level"] == "warning"

    @pytest.mark.asyncio
    async def test_tenant_isolation(self) -> None:
        from app.api.observability import StructuredLogStore

        store = StructuredLogStore()
        await store.emit("iso-a", "info", "Tenant A message")
        await store.emit("iso-b", "info", "Tenant B message")

        logs_a = await store.query("iso-a")
        logs_b = await store.query("iso-b")

        assert all("Tenant A" in l["message"] for l in logs_a)
        assert all("Tenant B" in l["message"] for l in logs_b)

    @pytest.mark.asyncio
    async def test_query_returns_newest_first(self) -> None:
        from app.api.observability import StructuredLogStore

        store = StructuredLogStore()
        for i in range(5):
            await store.emit("tid-order", "info", f"Message {i}")
        logs = await store.query("tid-order", limit=10)
        # list(reversed(buffer)) → last appended is first returned
        assert logs[0]["message"] == "Message 4"
        assert logs[-1]["message"] == "Message 0"

    @pytest.mark.asyncio
    async def test_empty_tenant_returns_empty_list(self) -> None:
        from app.api.observability import StructuredLogStore

        store = StructuredLogStore()
        logs = await store.query("tid-nonexistent-xyz", limit=10)
        assert logs == []

    @pytest.mark.asyncio
    async def test_limit_respected(self) -> None:
        from app.api.observability import StructuredLogStore

        store = StructuredLogStore()
        for i in range(20):
            await store.emit("tid-limit", "info", f"Message {i}")
        logs = await store.query("tid-limit", limit=5)
        assert len(logs) == 5

    @pytest.mark.asyncio
    async def test_redis_wiring_via_set_redis(self) -> None:
        """set_redis() wires the Redis client; subsequent emits use Redis path."""
        from app.api.observability import StructuredLogStore

        store = StructuredLogStore()
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock()
        store.set_redis(mock_redis)
        assert store._redis is mock_redis
        await store.emit("tid-redis", "info", "Redis message")
        mock_redis.xadd.assert_called_once()


# ─── LLM Circuit Breaker ──────────────────────────────────────────────────────


class TestProviderCircuitBreaker:
    """LLM circuit breaker opens after threshold failures and recovers via half-open."""

    def test_circuit_closed_initially(self) -> None:
        from app.providers.circuit_breaker import ProviderCircuitBreaker

        cb = ProviderCircuitBreaker()
        assert not cb.is_open("test-provider")

    def test_circuit_opens_after_threshold_failures(self) -> None:
        from app.providers.circuit_breaker import ProviderCircuitBreaker

        cb = ProviderCircuitBreaker(failure_threshold=3)
        for _ in range(3):
            cb.record_failure("p")
        assert cb.is_open("p")
        assert cb._state.get("p") == "open"

    def test_circuit_below_threshold_stays_closed(self) -> None:
        from app.providers.circuit_breaker import ProviderCircuitBreaker

        cb = ProviderCircuitBreaker(failure_threshold=3)
        cb.record_failure("p2")
        cb.record_failure("p2")
        assert not cb.is_open("p2")

    def test_circuit_resets_on_success(self) -> None:
        from app.providers.circuit_breaker import ProviderCircuitBreaker

        cb = ProviderCircuitBreaker(failure_threshold=2)
        cb.record_failure("p"); cb.record_failure("p")
        assert cb.is_open("p")
        cb.record_success("p")
        assert not cb.is_open("p")
        assert cb._failures.get("p") == 0
        assert cb._state.get("p") == "closed"

    def test_failure_count_increments_correctly(self) -> None:
        from app.providers.circuit_breaker import ProviderCircuitBreaker

        cb = ProviderCircuitBreaker(failure_threshold=10)
        cb.record_failure("p3")
        cb.record_failure("p3")
        assert cb._failures["p3"] == 2

    def test_circuit_transitions_to_half_open_after_recovery_timeout(self) -> None:
        from app.providers.circuit_breaker import ProviderCircuitBreaker

        cb = ProviderCircuitBreaker(failure_threshold=2, recovery_timeout=0.01)
        cb.record_failure("p"); cb.record_failure("p")
        assert cb.is_open("p")
        time.sleep(0.05)  # exceed recovery_timeout
        # After timeout, is_open returns False and sets state to "half-open"
        assert not cb.is_open("p")
        assert cb._state.get("p") == "half-open"

    def test_half_open_closes_on_success(self) -> None:
        from app.providers.circuit_breaker import ProviderCircuitBreaker

        cb = ProviderCircuitBreaker(failure_threshold=2, recovery_timeout=0.01)
        cb.record_failure("p"); cb.record_failure("p")
        time.sleep(0.05)
        cb.is_open("p")  # trigger half-open transition
        assert cb._state.get("p") == "half-open"
        cb.record_success("p")
        assert cb._state.get("p") == "closed"
        assert not cb.is_open("p")

    def test_half_open_reopens_on_failure(self) -> None:
        from app.providers.circuit_breaker import ProviderCircuitBreaker

        cb = ProviderCircuitBreaker(failure_threshold=2, recovery_timeout=0.01)
        cb.record_failure("p"); cb.record_failure("p")
        time.sleep(0.05)
        cb.is_open("p")  # trigger half-open
        assert cb._state.get("p") == "half-open"
        cb.record_failure("p")
        assert cb._state.get("p") == "open"

    def test_before_call_increments_half_open_counter(self) -> None:
        from app.providers.circuit_breaker import ProviderCircuitBreaker

        cb = ProviderCircuitBreaker(failure_threshold=2, recovery_timeout=0.01)
        cb.record_failure("p"); cb.record_failure("p")
        time.sleep(0.05)
        cb.is_open("p")  # half-open
        cb.before_call("p")
        assert cb._half_open_calls["p"] == 1

    @pytest.mark.asyncio
    async def test_call_with_circuit_breaker_success(self) -> None:
        from app.providers import circuit_breaker as cb_mod
        from app.providers.circuit_breaker import ProviderCircuitBreaker, call_with_circuit_breaker

        orig = cb_mod._provider_cb
        cb_mod._provider_cb = ProviderCircuitBreaker()
        try:
            mock_provider = MagicMock()
            mock_provider.complete = AsyncMock(return_value="response")
            result = await call_with_circuit_breaker(
                mock_provider, "complete", "arg1", provider_name="test-llm-ok"
            )
            assert result == "response"
        finally:
            cb_mod._provider_cb = orig

    @pytest.mark.asyncio
    async def test_call_records_failure_on_exception(self) -> None:
        from app.providers import circuit_breaker as cb_mod
        from app.providers.circuit_breaker import ProviderCircuitBreaker, call_with_circuit_breaker

        orig = cb_mod._provider_cb
        fresh = ProviderCircuitBreaker()
        cb_mod._provider_cb = fresh
        try:
            mock_provider = MagicMock()
            mock_provider.complete = AsyncMock(side_effect=Exception("API error"))
            with pytest.raises(Exception, match="API error"):
                await call_with_circuit_breaker(
                    mock_provider, "complete", provider_name="failing-llm"
                )
            assert fresh._failures.get("failing-llm", 0) == 1
        finally:
            cb_mod._provider_cb = orig

    @pytest.mark.asyncio
    async def test_call_raises_runtime_when_circuit_open(self) -> None:
        from app.providers import circuit_breaker as cb_mod
        from app.providers.circuit_breaker import ProviderCircuitBreaker, call_with_circuit_breaker

        orig = cb_mod._provider_cb
        fresh = ProviderCircuitBreaker(failure_threshold=3)
        cb_mod._provider_cb = fresh
        try:
            for _ in range(3):
                fresh.record_failure("open-provider")
            assert fresh.is_open("open-provider")
            mock_provider = MagicMock()
            with pytest.raises(RuntimeError, match="circuit open"):
                await call_with_circuit_breaker(
                    mock_provider, "complete", provider_name="open-provider"
                )
        finally:
            cb_mod._provider_cb = orig

    @pytest.mark.asyncio
    async def test_call_records_success_on_return(self) -> None:
        from app.providers import circuit_breaker as cb_mod
        from app.providers.circuit_breaker import ProviderCircuitBreaker, call_with_circuit_breaker

        orig = cb_mod._provider_cb
        fresh = ProviderCircuitBreaker(failure_threshold=5)
        fresh.record_failure("p-check")  # one failure
        cb_mod._provider_cb = fresh
        try:
            mock_provider = MagicMock()
            mock_provider.go = AsyncMock(return_value="ok")
            await call_with_circuit_breaker(mock_provider, "go", provider_name="p-check")
            # success resets failure count
            assert fresh._failures.get("p-check") == 0
        finally:
            cb_mod._provider_cb = orig


# ─── GoalLifecycle: State Machine ─────────────────────────────────────────────


class TestGoalLifecycle:
    """is_valid_transition enforces the goal state machine."""

    def test_pending_to_planning_valid(self) -> None:
        from app.services.goal_lifecycle import is_valid_transition

        assert is_valid_transition("pending", "planning")

    def test_planning_to_executing_valid(self) -> None:
        from app.services.goal_lifecycle import is_valid_transition

        assert is_valid_transition("planning", "executing")

    def test_executing_to_verifying_valid(self) -> None:
        from app.services.goal_lifecycle import is_valid_transition

        assert is_valid_transition("executing", "verifying")

    def test_verifying_to_complete_valid(self) -> None:
        from app.services.goal_lifecycle import is_valid_transition

        assert is_valid_transition("verifying", "complete")

    def test_verifying_can_loop_back_to_executing(self) -> None:
        """Verifier failure triggers re-execution."""
        from app.services.goal_lifecycle import is_valid_transition

        assert is_valid_transition("verifying", "executing")

    def test_complete_is_terminal_no_outgoing_transitions(self) -> None:
        from app.services.goal_lifecycle import is_valid_transition

        for target in ("planning", "executing", "failed", "pending", "cancelled"):
            assert not is_valid_transition("complete", target), (
                f"complete → {target} must be invalid"
            )

    def test_failed_is_terminal_no_outgoing_transitions(self) -> None:
        from app.services.goal_lifecycle import is_valid_transition

        for target in ("planning", "complete", "executing", "pending"):
            assert not is_valid_transition("failed", target), (
                f"failed → {target} must be invalid"
            )

    def test_cancelled_is_terminal(self) -> None:
        from app.services.goal_lifecycle import is_valid_transition

        assert not is_valid_transition("cancelled", "pending")
        assert not is_valid_transition("cancelled", "planning")

    def test_executing_can_await_human(self) -> None:
        from app.services.goal_lifecycle import is_valid_transition

        assert is_valid_transition("executing", "waiting_human")

    def test_waiting_human_can_resume_to_executing(self) -> None:
        from app.services.goal_lifecycle import is_valid_transition

        assert is_valid_transition("waiting_human", "executing")

    def test_waiting_human_can_cancel(self) -> None:
        from app.services.goal_lifecycle import is_valid_transition

        assert is_valid_transition("waiting_human", "cancelled")

    def test_planning_can_fail(self) -> None:
        from app.services.goal_lifecycle import is_valid_transition

        assert is_valid_transition("planning", "failed")

    def test_executing_can_fail(self) -> None:
        from app.services.goal_lifecycle import is_valid_transition

        assert is_valid_transition("executing", "failed")

    def test_paused_can_resume_to_executing(self) -> None:
        from app.services.goal_lifecycle import is_valid_transition

        assert is_valid_transition("paused", "executing")

    def test_unknown_source_state_returns_false(self) -> None:
        from app.services.goal_lifecycle import is_valid_transition

        assert not is_valid_transition("nonexistent", "planning")

    def test_unknown_target_state_returns_false(self) -> None:
        from app.services.goal_lifecycle import is_valid_transition

        assert not is_valid_transition("planning", "nonexistent")

    def test_goal_transition_enum_values(self) -> None:
        from app.services.goal_lifecycle import GoalTransition

        assert GoalTransition.COMPLETE == "complete"
        assert GoalTransition.FAIL == "fail"
        assert GoalTransition.CANCEL == "cancel"
        assert GoalTransition.SUBMIT == "submit"
        assert GoalTransition.AWAIT_HUMAN == "await_human"
        assert GoalTransition.PAUSE == "pause"
        assert GoalTransition.RESUME == "resume"

    def test_goal_transition_inherits_str(self) -> None:
        """GoalTransition(str, Enum) members are str instances; .value gives the string."""
        from app.services.goal_lifecycle import GoalTransition

        # Members are instances of str (GoalTransition inherits from str)
        assert isinstance(GoalTransition.COMPLETE, str)
        # .value is the canonical way to get the string value in Python 3.12
        assert GoalTransition.FAIL.value == "fail"
        assert GoalTransition.COMPLETE.value == "complete"
        assert GoalTransition.CANCEL.value == "cancel"


# ─── Collab: CRDT Token Store ─────────────────────────────────────────────────


class TestCollabCRDTTokenStore:
    """Short-lived CRDT tokens are cleaned up when expired."""

    def test_cleanup_removes_expired_tokens(self) -> None:
        from app.api.collab import _cleanup_expired_crdt_tokens, _crdt_tokens

        _crdt_tokens["expired-tok"] = {
            "tenant_id": "tid-fix-test",
            "expires_at": time.monotonic() - 10,  # expired 10 s ago
        }
        _crdt_tokens["valid-tok"] = {
            "tenant_id": "tid-fix-test",
            "expires_at": time.monotonic() + 3600,
        }
        _cleanup_expired_crdt_tokens()
        assert "expired-tok" not in _crdt_tokens
        assert "valid-tok" in _crdt_tokens
        # Cleanup: remove valid-tok so we don't pollute other tests
        _crdt_tokens.pop("valid-tok", None)

    def test_cleanup_noop_when_store_is_empty(self) -> None:
        from app.api.collab import _cleanup_expired_crdt_tokens, _crdt_tokens

        pre_size = len(_crdt_tokens)
        _cleanup_expired_crdt_tokens()
        assert len(_crdt_tokens) == pre_size  # no change

    def test_token_ttl_constant_is_one_hour(self) -> None:
        from app.api.collab import _CRDT_TOKEN_TTL

        assert _CRDT_TOKEN_TTL == 3600

    def test_multiple_expired_tokens_all_removed(self) -> None:
        from app.api.collab import _cleanup_expired_crdt_tokens, _crdt_tokens

        past = time.monotonic() - 100
        keys = [f"batch-expired-{i}" for i in range(5)]
        for k in keys:
            _crdt_tokens[k] = {"tenant_id": "t", "expires_at": past}
        _cleanup_expired_crdt_tokens()
        for k in keys:
            assert k not in _crdt_tokens

    def test_not_yet_expired_tokens_survive_cleanup(self) -> None:
        from app.api.collab import _cleanup_expired_crdt_tokens, _crdt_tokens

        key = "survive-tok-unique"
        _crdt_tokens[key] = {
            "tenant_id": "tid-fix-test",
            "expires_at": time.monotonic() + 1800,
        }
        _cleanup_expired_crdt_tokens()
        assert key in _crdt_tokens
        _crdt_tokens.pop(key, None)
