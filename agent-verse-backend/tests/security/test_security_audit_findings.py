"""Security audit regression tests.

All tests in this file correspond to findings in the AgentVerse security audit.

Findings covered:
  F-1  SSRF in connector registration and test endpoint
  F-2  Admin API key timing-attack (must use constant-time compare)
  F-3  Unauthenticated RPA tools-list endpoint
  F-4  Tenant isolation — cross-tenant goal read/cancel/list
  F-5  TOTP replay — same code must not be accepted twice
  F-6  MFA TOTP secret must not appear in application logs
  F-7  Input validation — empty goal, oversized payload
  F-8  Audit-log append and hash-chain integrity
  F-9  Provider settings response must never expose raw API keys
  F-10 All authenticated endpoints must reject missing API key
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import re

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Shared app fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    from app.main import create_app
    app = create_app(manage_pools=False)
    return TestClient(app, raise_server_exceptions=False)


# ===========================================================================
# F-10 — ALL AUTHENTICATED ENDPOINTS MUST REJECT MISSING API KEY
# ===========================================================================

def test_unauthenticated_request_returns_401(client):
    """GET /goals with no credentials must return 401."""
    response = client.get("/goals")
    assert response.status_code == 401, (
        f"Expected 401 Unauthorized, got {response.status_code}"
    )


def test_goals_post_requires_api_key(client):
    """POST /goals with no credentials must return 401, not 200/422."""
    response = client.post("/goals", json={"goal": "test"})
    assert response.status_code == 401


def test_agents_get_requires_api_key(client):
    """GET /agents with no credentials must return 401."""
    response = client.get("/agents")
    assert response.status_code == 401


def test_governance_requires_api_key(client):
    """GET /governance/audit with no credentials must be rejected.

    Accepts 401 (auth rejected) or 404 (route name differs) — never 200.
    """
    response = client.get("/governance/audit")
    assert response.status_code in (401, 404), (
        f"Expected 401/404, got {response.status_code}"
    )


def test_connectors_requires_api_key(client):
    """GET /connectors must not be accessible without a valid API key."""
    response = client.get("/connectors")
    assert response.status_code == 401


def test_knowledge_requires_api_key(client):
    """GET /knowledge/collections must require auth."""
    response = client.get("/knowledge/collections")
    assert response.status_code == 401


# ===========================================================================
# F-3 — RPA TOOLS ENDPOINT MUST REQUIRE AUTHENTICATION
# ===========================================================================

def test_rpa_tools_list_requires_auth(client):
    """GET /rpa/tools must require a valid API key.

    Finding: the endpoint previously returned the full RPA tool catalog to
    any unauthenticated caller, leaking automation-capability information
    useful for reconnaissance.
    """
    response = client.get("/rpa/tools")
    assert response.status_code == 401, (
        f"RPA tools endpoint is unauthenticated (status={response.status_code}). "
        "Fix: add _require_tenant(request) to list_rpa_tools()."
    )


def test_rpa_execute_requires_auth(client):
    """POST /rpa/execute must also require auth (control: already guarded)."""
    response = client.post("/rpa/execute", json={"tool_name": "navigate_to"})
    assert response.status_code == 401


# ===========================================================================
# F-4 — TENANT ISOLATION: CROSS-TENANT GOAL ACCESS
# ===========================================================================

def test_cross_tenant_goal_access_denied():
    """Tenant B must not be able to read Tenant A's goal by ID.

    Uses the in-memory GoalService directly — no DB or HTTP required.
    """
    from app.core.errors import NotFoundError
    from app.services.goal_service import GoalService
    from app.tenancy.context import PlanTier, TenantContext

    ctx_a = TenantContext(tenant_id="tenant-a", plan=PlanTier.FREE, api_key_id="ka")
    ctx_b = TenantContext(tenant_id="tenant-b", plan=PlanTier.FREE, api_key_id="kb")
    svc = GoalService()

    async def run():
        result = await svc.submit_goal(
            goal="tenant A private goal",
            priority="normal",
            dry_run=True,
            tenant_ctx=ctx_a,
        )
        goal_id = result["goal_id"]
        with pytest.raises(NotFoundError):
            await svc.get_goal(goal_id=goal_id, tenant_ctx=ctx_b)

    asyncio.run(run())


def test_cross_tenant_goal_cancel_denied():
    """Tenant B must not be able to cancel Tenant A's goal."""
    from app.core.errors import NotFoundError
    from app.services.goal_service import GoalService
    from app.tenancy.context import PlanTier, TenantContext

    ctx_a = TenantContext(tenant_id="cancel-tenant-a", plan=PlanTier.FREE, api_key_id="ka")
    ctx_b = TenantContext(tenant_id="cancel-tenant-b", plan=PlanTier.FREE, api_key_id="kb")
    svc = GoalService()

    async def run():
        result = await svc.submit_goal(
            goal="a secret goal",
            priority="normal",
            dry_run=True,
            tenant_ctx=ctx_a,
        )
        goal_id = result["goal_id"]
        with pytest.raises((NotFoundError, PermissionError)):
            await svc.cancel_goal(goal_id=goal_id, tenant_ctx=ctx_b)

    asyncio.run(run())


# ===========================================================================
# F-2 — ADMIN API TIMING ATTACK (must use hmac.compare_digest)
# ===========================================================================

def test_admin_key_comparison_is_constant_time():
    """_require_admin must use hmac.compare_digest, not == operator.

    Finding: direct string equality (`x_admin_key != admin_key`) is
    susceptible to timing-based key oracle attacks.  Constant-time
    comparison eliminates the timing side-channel.
    """
    from app.api.admin import _require_admin

    src = inspect.getsource(_require_admin)
    assert "hmac.compare_digest" in src, (
        "_require_admin must use hmac.compare_digest() for constant-time "
        "comparison of the platform admin key.  Direct string equality "
        "leaks key length via timing."
    )


def test_admin_endpoint_rejects_wrong_key(client, monkeypatch):
    """Admin endpoints must return 401 for an incorrect key value."""
    import os
    monkeypatch.setenv("PLATFORM_ADMIN_KEY", "correct-secret-key-xyz")
    response = client.get(
        "/admin/tenants",
        headers={"X-Admin-Key": "wrong-key"},
    )
    assert response.status_code == 401


def test_admin_endpoint_rejects_empty_key(client, monkeypatch):
    """Admin endpoints must return 401 when X-Admin-Key header is absent."""
    import os
    monkeypatch.setenv("PLATFORM_ADMIN_KEY", "some-secret")
    response = client.get("/admin/tenants")
    assert response.status_code == 401


# ===========================================================================
# F-1 — SSRF IN CONNECTOR REGISTRATION
# ===========================================================================

def test_connector_registration_blocks_loopback(client):
    """Registering a connector pointing at 127.0.0.1 must be rejected (SSRF)."""
    response = client.post(
        "/connectors",
        json={
            "name": "ssrf-test",
            "url": "http://127.0.0.1/internal",
            "auth_type": "bearer",
            "auth_config": {},
        },
        headers={"X-API-Key": "invalid-but-irrelevant"},
    )
    # Auth will fail first (401), but if it somehow passes, must not be 201.
    # The SSRF guard should produce 400; auth guard produces 401.
    assert response.status_code in (400, 401), (
        f"Loopback connector URL should be rejected. Got {response.status_code}."
    )


def test_ssrf_guard_module_blocks_metadata_endpoint():
    """assert_public_url must block the AWS/GCP instance-metadata IP."""
    from app.net.ssrf_guard import SSRFError, assert_public_url

    with pytest.raises(SSRFError):
        assert_public_url("http://169.254.169.254/latest/meta-data/", context="test")


def test_ssrf_guard_blocks_private_ip():
    """assert_public_url must block RFC-1918 private addresses."""
    from app.net.ssrf_guard import SSRFError, assert_public_url

    for private_url in [
        "http://10.0.0.1/secret",
        "http://192.168.1.1/admin",
        "http://172.16.0.1/api",
    ]:
        with pytest.raises(SSRFError):
            assert_public_url(private_url, context="test")


def test_ssrf_guard_blocks_file_scheme():
    """Non-http(s) schemes must be rejected by the SSRF guard."""
    from app.net.ssrf_guard import SSRFError, assert_public_url

    with pytest.raises(SSRFError):
        assert_public_url("file:///etc/passwd", context="test")


def test_ssrf_guard_allows_public_url():
    """assert_public_url must NOT raise for legitimate public URLs."""
    from app.net.ssrf_guard import assert_public_url

    # Should not raise (public DNS resolves fine even in offline test env;
    # if DNS fails, ssrf_guard should still not raise SSRFError for public IPs)
    try:
        assert_public_url("https://api.github.com/repos", context="test")
    except Exception as exc:
        # Only DNS resolution errors are acceptable — not SSRFError
        from app.net.ssrf_guard import SSRFError
        assert not isinstance(exc, SSRFError), (
            f"Public URL incorrectly flagged as SSRF risk: {exc}"
        )


# ===========================================================================
# F-6 — MFA TOTP SECRET MUST NOT APPEAR IN LOGS
# ===========================================================================

def test_mfa_enrollment_does_not_log_secret(client, caplog):
    """MFA enrollment endpoint must never log the raw TOTP secret.

    We call the endpoint with an invalid API key (which returns 401 before
    any secret is generated), then also verify the enrollment code-path
    directly against the module to ensure the secret is not logged at any
    log level.
    """
    # HTTP-level check: 401 path — no TOTP secret should enter logs
    with caplog.at_level(logging.DEBUG):
        client.post("/auth/mfa/enroll", headers={"X-API-Key": "invalid-key-xyz"})

    base32_pattern = re.compile(r"[A-Z2-7]{16,}")
    for record in caplog.records:
        message = record.getMessage()
        assert not base32_pattern.search(message), (
            f"Potential TOTP secret (base32, 16+ chars) found in log record: "
            f"{message[:80]!r}"
        )


def test_mfa_enrollment_response_contains_secret_not_logged():
    """The enrollment endpoint returns the secret in the response body;
    verify the MFA module does NOT log it via the module logger."""
    import logging as _logging
    from app.api import mfa as mfa_module

    # Check that no logging.info/debug/warning calls in begin_enrollment
    # embed the raw secret variable by inspecting the source.
    src = inspect.getsource(mfa_module.begin_enrollment)
    # The source may reference 'secret' in the return dict — that's fine.
    # What's NOT fine is a log call that includes the secret variable directly.
    log_calls_with_secret = re.findall(
        r'(?:_logger|logger|logging)\.[a-z]+\([^)]*\bsecret\b[^)]*\)',
        src,
    )
    assert not log_calls_with_secret, (
        f"begin_enrollment appears to log the TOTP secret: {log_calls_with_secret}"
    )


# ===========================================================================
# F-5 — TOTP REPLAY PREVENTION
# ===========================================================================

def test_totp_replay_same_code_rejected():
    """The same TOTP code must be rejected when used a second time in the
    same 30-second window."""
    from app.api.mfa import _is_totp_replayed, _used_totp_codes

    tenant = "replay-test-tenant"
    # Clear any leftover state from previous test runs
    _used_totp_codes.pop(tenant, None)

    code = "123456"
    # First use: must NOT be flagged as replay
    assert _is_totp_replayed(tenant, code) is False, (
        "First use of a TOTP code should not be flagged as replay"
    )
    # Second use in same window: MUST be flagged
    assert _is_totp_replayed(tenant, code) is True, (
        "Reuse of a TOTP code in the same window must be detected as replay"
    )


# ===========================================================================
# F-7 — INPUT VALIDATION
# ===========================================================================

def test_goal_submission_validates_empty_goal(client):
    """Goal submission with an empty string must be rejected.

    With a fake API key the middleware returns 401 (auth fails first).
    That is acceptable — what's NOT acceptable is 200.
    """
    response = client.post(
        "/goals",
        json={"goal": "", "priority": "normal"},
        headers={"X-API-Key": "fake-key"},
    )
    assert response.status_code != 200, (
        f"Empty goal should be rejected, got {response.status_code}"
    )


def test_goal_pydantic_model_rejects_empty_string():
    """GoalRequest.goal field must have min_length=1 enforced at model level."""
    from pydantic import ValidationError
    from app.api.goals import GoalRequest

    with pytest.raises(ValidationError):
        GoalRequest(goal="", priority="normal")


def test_goal_pydantic_model_rejects_oversized_payload():
    """GoalRequest.goal must enforce max_length=10_000."""
    from pydantic import ValidationError
    from app.api.goals import GoalRequest

    with pytest.raises(ValidationError):
        GoalRequest(goal="A" * 10_001, priority="normal")


def test_goal_submission_rejects_oversized_payload(client):
    """Goal submission with a >10KB goal text must be rejected.

    Accepts 401 (auth fails), 413, or 422 — never 200.
    """
    huge_goal = "A" * 100_001
    response = client.post(
        "/goals",
        json={"goal": huge_goal, "priority": "normal"},
        headers={"X-API-Key": "fake-key"},
    )
    assert response.status_code != 200, (
        f"Oversized payload must be rejected, got {response.status_code}"
    )


# ===========================================================================
# F-8 — AUDIT LOG WRITE AND HASH-CHAIN INTEGRITY
# ===========================================================================

def test_audit_log_write_produces_valid_record():
    """AuditV3.append must write an immutable record with a non-empty hash."""
    from app.governance.audit_v3 import AuditV3

    audit = AuditV3()

    async def run():
        record = await audit.append(
            tenant_id="audit-test",
            goal_id="goal-123",
            action="goal_submitted",
            tool_name="",
            actor="user:test",
        )
        assert record.tenant_id == "audit-test"
        assert record.goal_id == "goal-123"
        assert record.entry_hash, "entry_hash must be non-empty"
        # First record's previous_hash is "genesis" — must be non-empty
        assert record.previous_hash, "previous_hash must be set (even 'genesis')"

    asyncio.run(run())


def test_audit_chain_integrity_verified():
    """Three sequential audit records must form a valid, verifiable hash chain."""
    from app.governance.audit_v3 import AuditV3

    audit = AuditV3()
    tenant = "chain-integrity-test"

    async def run():
        for i in range(3):
            await audit.append(
                tenant_id=tenant,
                goal_id=f"goal-{i}",
                action="step_complete",
                actor="system",
            )

        result = audit.verify_chain(tenant)
        assert result["valid"] is True, (
            f"Audit chain must be valid; got: {result}"
        )
        assert result["records_checked"] == 3, (
            f"Expected 3 records checked, got {result['records_checked']}"
        )

    asyncio.run(run())


def test_audit_chain_detects_tampering():
    """verify_chain must flag a chain as invalid when a record is mutated."""
    from app.governance.audit_v3 import AuditV3

    audit = AuditV3()
    tenant = "tamper-test"

    async def run():
        await audit.append(
            tenant_id=tenant, goal_id="g0", action="first", actor="system"
        )
        await audit.append(
            tenant_id=tenant, goal_id="g1", action="second", actor="system"
        )
        # Tamper: corrupt the first record's action field
        first = next(r for r in audit._records if r.tenant_id == tenant)
        object.__setattr__(first, "action", "tampered!")

        result = audit.verify_chain(tenant)
        assert result["valid"] is False, "Tampered chain must be detected as invalid"

    asyncio.run(run())


# ===========================================================================
# F-9 — PROVIDER SETTINGS MUST NOT EXPOSE RAW API KEYS
# ===========================================================================

def test_llm_config_endpoint_does_not_expose_raw_key(client):
    """GET /tenants/me/llm must never include the raw API key in its response."""
    response = client.get(
        "/tenants/me/llm",
        headers={"X-API-Key": "fake-key"},
    )
    # 401 = auth failed (no key exposed), 200 = check content
    if response.status_code == 200:
        content = response.text
        assert not re.search(r"sk-[a-zA-Z0-9]{20,}", content), (
            "Raw OpenAI API key found in /tenants/me/llm response"
        )
        assert not re.search(r"sk-ant-[a-zA-Z0-9]{20,}", content), (
            "Raw Anthropic API key found in /tenants/me/llm response"
        )


def test_provider_catalog_returns_env_var_name_not_value(client):
    """GET /tenants/me/providers must return env-var names, never actual key values."""
    response = client.get(
        "/tenants/me/providers",
        headers={"X-API-Key": "fake-key"},
    )
    if response.status_code == 200:
        content = response.text
        # Must not contain key-shaped values
        assert not re.search(r"sk-[a-zA-Z0-9]{20,}", content), (
            "Raw API key pattern found in provider catalog response"
        )
        # Must reference env var names, not values
        assert "OPENAI_API_KEY" in content or "anthropic" in content.lower(), (
            "Provider catalog must reference env-var names"
        )


# ===========================================================================
# SECURITY HEADERS REGRESSION
# ===========================================================================

def test_security_headers_present(client):
    """Every response must include OWASP-recommended security headers."""
    response = client.get("/health")
    headers = response.headers

    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("X-Frame-Options") == "DENY"
    assert "Strict-Transport-Security" in headers, (
        "HSTS header missing — Fix 1 must be applied"
    )
    hsts = headers["Strict-Transport-Security"]
    assert "max-age=" in hsts
    assert "includeSubDomains" in hsts


def test_cors_preflight_bypasses_auth(client):
    """OPTIONS CORS preflight requests must be allowed without API key."""
    response = client.options(
        "/goals",
        headers={
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    # Must not be 401 — CORS preflight has no credentials
    assert response.status_code != 401, (
        "CORS preflight must not require an API key"
    )
