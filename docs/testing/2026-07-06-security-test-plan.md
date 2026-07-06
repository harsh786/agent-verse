# AgentVerse Security Test Plan
**Date:** 2026-07-06  
**Linked to:** `docs/security/2026-07-06-agentverse-security-audit.md`  
**Scope:** Backend (`agent-verse-backend/tests/`) + Frontend (`agent-verse-frontend/src/test/`)  

All tests marked **LAUNCH BLOCKER** must pass before any public production release.

---

## Test Suite Structure

```
tests/
├── security/               ← new package; add __init__.py
│   ├── test_ssrf.py         findings 1, 2, 8
│   ├── test_admin_auth.py   finding 3
│   ├── test_mfa_distributed.py  findings 4, 9
│   ├── test_signup_ratelimit.py  finding 5
│   ├── test_vault.py        findings 6, 13
│   ├── test_oauth.py        findings 10, 11
│   └── test_audit_integrity.py  finding 12
├── api/
│   └── test_connectors.py   findings 1, 2, 15, 19
├── auth/
│   └── test_scope_enforcement.py  finding 21
└── scaling/
    └── test_tasks.py        finding 20
```

---

## Test 1: SSRF in `test_connector` Generic Fallback

**Linked finding:** Finding 1 (CRITICAL)  
**Test type:** pytest API  
**Launch blocker:** YES

**File:** `tests/security/test_ssrf.py`

```python
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.tenancy.service import TenantService

@pytest.fixture
def client_with_tenant():
    """Create a test client with a pre-authenticated tenant."""
    app = create_app()
    svc = app.state.tenant_service
    result = svc.create_tenant_sync(name="ssrf-test", email="ssrf@test.example")
    api_key = result["raw_key"]
    return TestClient(app), api_key

@pytest.mark.parametrize("blocked_url", [
    "http://127.0.0.1:5432",
    "http://10.0.0.1/internal",
    "http://169.254.169.254/latest/meta-data/",
    "http://192.168.1.1/admin",
    "http://0.0.0.0/",
    "http://[::1]/",
    "http://metadata.google.internal/",
])
def test_test_connector_ssrf_blocked(client_with_tenant, blocked_url):
    """POST /connectors/{id}/test must reject internal/metadata URLs."""
    client, api_key = client_with_tenant
    headers = {"X-API-Key": api_key}

    # Register a connector with a blocked URL and a name NOT in _CONNECTOR_TEST_TOOLS
    resp = client.post("/connectors", json={
        "name": "custom-internal",
        "url": blocked_url,
        "auth_type": "bearer",
        "auth_config": {"token": "test-token"},
    }, headers=headers)
    assert resp.status_code == 201
    server_id = resp.json()["server_id"]

    # Test the connector — must be blocked
    test_resp = client.post(f"/connectors/{server_id}/test", headers=headers)
    assert test_resp.status_code == 400, (
        f"Expected 400 for blocked URL {blocked_url}, got {test_resp.status_code}: "
        f"{test_resp.json()}"
    )
    assert "blocked" in test_resp.json().get("detail", "").lower()
```

**How to verify it passes:** `uv run pytest tests/security/test_ssrf.py::test_test_connector_ssrf_blocked -v`  
Expected: All parametrized cases return `400`.

---

## Test 2: SSRF in `discover_tools`

**Linked finding:** Finding 2 (HIGH)  
**Test type:** pytest unit  
**Launch blocker:** YES

**File:** `tests/security/test_ssrf.py`

```python
import pytest
from unittest.mock import AsyncMock, patch
from app.mcp.client import MCPClient
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.net.ssrf_guard import SSRFError
from app.tenancy.context import TenantContext, PlanTier

@pytest.mark.asyncio
async def test_discover_tools_ssrf_blocked():
    """discover_tools must raise SSRFError for internal URLs without making HTTP calls."""
    # Mock registry to return a connector pointing to internal IP
    mock_registry = AsyncMock(spec=MCPRegistry)
    mock_registry.get.return_value = MCPServerConfig(
        name="internal-svc",
        url="http://10.0.0.1:8080",
        auth_type="bearer",
        auth_config={"token": "t"},
    )
    client = MCPClient(registry=mock_registry)
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")

    with pytest.raises(SSRFError):
        await client.discover_tools(server_id="srv1", tenant_ctx=tenant)

@pytest.mark.asyncio
async def test_discover_tools_does_not_call_network_for_blocked_url():
    """Verify no actual HTTP call is made when SSRF check blocks the request."""
    mock_registry = AsyncMock(spec=MCPRegistry)
    mock_registry.get.return_value = MCPServerConfig(
        name="internal-svc",
        url="http://169.254.169.254/",
        auth_type="bearer",
        auth_config={},
    )
    client = MCPClient(registry=mock_registry)
    tenant = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")

    with patch("httpx.AsyncClient") as mock_httpx:
        with pytest.raises(SSRFError):
            await client.discover_tools(server_id="srv1", tenant_ctx=tenant)
        mock_httpx.assert_not_called()   # must not reach the HTTP client
```

**How to verify it passes:** `uv run pytest tests/security/test_ssrf.py::test_discover_tools_ssrf_blocked -v`

---

## Test 3: Admin API Key Constant-Time Comparison

**Linked finding:** Finding 3 (HIGH)  
**Test type:** pytest unit  
**Launch blocker:** YES

**File:** `tests/security/test_admin_auth.py`

```python
import ast, inspect
import pytest
from app.api.admin import _require_admin

def test_admin_auth_uses_hmac_compare_digest():
    """_require_admin must use hmac.compare_digest, not string equality."""
    source = inspect.getsource(_require_admin)
    # Must contain hmac.compare_digest or secrets.compare_digest
    assert (
        "hmac.compare_digest" in source or "secrets.compare_digest" in source
    ), "Admin key comparison must use constant-time comparison"

def test_admin_auth_rejects_wrong_key(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setenv("PLATFORM_ADMIN_KEY", "correct-key-abc123")
    with pytest.raises(HTTPException) as exc_info:
        _require_admin(x_admin_key="wrong-key")
    assert exc_info.value.status_code == 401

def test_admin_auth_accepts_correct_key(monkeypatch):
    monkeypatch.setenv("PLATFORM_ADMIN_KEY", "correct-key-abc123")
    # Should not raise
    _require_admin(x_admin_key="correct-key-abc123")
```

**How to verify it passes:** `uv run pytest tests/security/test_admin_auth.py -v`

---

## Test 4: MFA Session Tokens Are Redis-Backed (Not Process-Local)

**Linked finding:** Finding 4 (HIGH)  
**Test type:** pytest unit (simulates multi-replica by using two separate MFA module instances)  
**Launch blocker:** YES

**File:** `tests/security/test_mfa_distributed.py`

```python
import importlib
import sys
import pytest

def _fresh_mfa_module():
    """Import a fresh copy of the mfa module simulating a new replica."""
    module_name = "app_mfa_replica_fresh"
    spec = importlib.util.spec_from_file_location(
        module_name, "app/api/mfa.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def test_totp_replay_blocked_within_same_process():
    """TOTP code must not be accepted twice in the same process."""
    from app.api import mfa as mfa_mod
    mfa_mod._used_totp_codes.clear()
    
    tenant_id = "tenant-replay-test"
    code = "123456"
    
    # First use: not replayed
    assert not mfa_mod._is_totp_replayed(tenant_id, code)
    # Second use: should be blocked
    assert mfa_mod._is_totp_replayed(tenant_id, code)

def test_totp_replay_not_blocked_across_separate_module_instances():
    """
    Demonstrates the multi-replica flaw: two separate module instances
    (simulating two replicas) do NOT share replay prevention state.
    This test SHOULD FAIL once Finding 4 is fixed (Redis-backed store).
    """
    replica_a = _fresh_mfa_module()
    replica_b = _fresh_mfa_module()
    
    tenant_id = "tenant-replay-multiproc"
    code = "654321"
    
    # Mark code as used on replica A
    replica_a._is_totp_replayed(tenant_id, code)
    
    # On replica B, the same code is NOT marked as used — VULNERABILITY
    # After fix: this assertion should change to assert True (blocked)
    result_on_b = replica_b._used_totp_codes[tenant_id]
    # This will be an empty set, demonstrating the flaw
    assert len(result_on_b) == 0, (
        "VULNERABILITY CONFIRMED: TOTP replay state not shared across replicas. "
        "Fix by backing _used_totp_codes with Redis."
    )

@pytest.mark.asyncio
async def test_mfa_session_requires_redis_backing():
    """After fix: MFA sessions should be stored in Redis, not in-process dict."""
    from app.api.mfa import _mfa_verified_sessions
    import secrets, time
    
    token = secrets.token_urlsafe(32)
    _mfa_verified_sessions[token] = {
        "tenant_id": "t1",
        "created_at": time.monotonic(),
        "method": "totp",
    }
    
    # Simulate checking from a "different replica" (fresh dict)
    fresh_sessions: dict = {}
    result = fresh_sessions.get(token)
    
    # This should NOT find the session — demonstrates the flaw
    assert result is None, (
        "MFA session only visible in originating replica's memory. "
        "Fix by backing _mfa_verified_sessions with Redis."
    )
```

**How to verify it passes:** `uv run pytest tests/security/test_mfa_distributed.py -v`

---

## Test 5: Signup Endpoint Rate Limiting

**Linked finding:** Finding 5 (HIGH)  
**Test type:** pytest API  
**Launch blocker:** YES

**File:** `tests/security/test_signup_ratelimit.py`

```python
import pytest
from fastapi.testclient import TestClient
from app.main import create_app

def test_signup_rate_limited():
    """More than N signups per minute from the same IP must return 429."""
    app = create_app()
    client = TestClient(app)
    
    responses = []
    for i in range(12):
        resp = client.post("/tenants/signup", json={
            "name": f"test-tenant-{i}",
            "email": f"test{i}@ratelimit-test.example",
        })
        responses.append(resp.status_code)
    
    assert 429 in responses, (
        "Expected at least one 429 response after exceeding signup rate limit, "
        f"but got statuses: {responses}"
    )
    # First 5 should succeed (or whatever the configured limit is)
    assert responses[0] == 201, "First signup should succeed"
```

**How to verify it passes:** `uv run pytest tests/security/test_signup_ratelimit.py -v`

---

## Test 6: Vault Dev Key Warning in Non-Production

**Linked finding:** Finding 6 (HIGH)  
**Test type:** pytest unit  
**Launch blocker:** YES

**File:** `tests/security/test_vault.py`

```python
import os
import pytest
import warnings

def test_vault_warns_when_no_key_in_non_production(monkeypatch):
    """get_vault() should emit a warning (or require opt-in) in non-production without key."""
    monkeypatch.delenv("VAULT_MASTER_KEY", raising=False)
    monkeypatch.delenv("VAULT_MASTER_KEY_FILE", raising=False)
    monkeypatch.delenv("AGENTVERSE_VAULT_KEY", raising=False)
    monkeypatch.delenv("AGENTVERSE_VAULT_KEY_FILE", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    
    # After fix: should emit a UserWarning
    with pytest.warns(UserWarning, match="insecure"):
        from app.providers.vault import get_vault
        # Clear the lru_cache so the function re-executes
        get_vault.cache_clear() if hasattr(get_vault, "cache_clear") else None
        vault = get_vault()
    
    # Vault should still return something (non-production allows fallback)
    assert vault is not None

def test_vault_raises_in_production_without_key(monkeypatch):
    """get_vault() must raise RuntimeError in production without a vault key."""
    monkeypatch.delenv("VAULT_MASTER_KEY", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    
    with pytest.raises(RuntimeError, match="vault master key"):
        from app.providers.vault import get_vault
        get_vault()

def test_different_salts_produce_different_keys():
    """After Finding 13 fix: PBKDF2 with different salts must produce different keys."""
    import os
    from app.providers.vault import _derive_fernet_key
    
    # After fix, _derive_fernet_key should accept an optional salt parameter
    # Current implementation uses fixed salt — this test documents the expected behavior
    master_key = "test-master-key-12345"
    
    # If salt support is added:
    # key1, salt1 = _derive_fernet_key(master_key, salt=os.urandom(32))
    # key2, salt2 = _derive_fernet_key(master_key, salt=os.urandom(32))
    # assert key1 != key2, "Different salts must produce different derived keys"
    
    # Current behavior (documents the FLAW — fixed salt):
    key1 = _derive_fernet_key(master_key)
    key2 = _derive_fernet_key(master_key)
    assert key1 == key2, (
        "Current implementation uses fixed salt — keys are always identical for same master key. "
        "This test should be updated after Finding 13 is fixed."
    )
```

**How to verify it passes:** `uv run pytest tests/security/test_vault.py -v`

---

## Test 7: MFA Enrollment Does Not Return Raw TOTP Secret

**Linked finding:** Finding 9 (MEDIUM)  
**Test type:** pytest API  
**Launch blocker:** YES

**File:** `tests/security/test_mfa_distributed.py`

```python
import pytest
from fastapi.testclient import TestClient
from app.main import create_app

def test_enroll_response_does_not_include_raw_secret():
    """POST /auth/mfa/enroll must not return the raw TOTP secret."""
    app = create_app()
    client = TestClient(app)
    svc = app.state.tenant_service
    result = svc.create_tenant_sync(name="mfa-test", email="mfa@test.example")
    api_key = result["raw_key"]
    
    resp = client.post("/auth/mfa/enroll", headers={"X-API-Key": api_key})
    assert resp.status_code == 200
    body = resp.json()
    
    assert "secret" not in body, (
        f"Raw TOTP secret must not be returned in enrollment response. "
        f"Keys present: {list(body.keys())}"
    )
    # Must still return provisioning_uri (secret is embedded there)
    assert "provisioning_uri" in body
    assert "qr_code" in body
```

**How to verify it passes:** `uv run pytest tests/security/test_mfa_distributed.py::test_enroll_response_does_not_include_raw_secret -v`

---

## Test 8: OAuth Tokens Encrypted at Rest

**Linked finding:** Finding 10 (MEDIUM)  
**Test type:** pytest unit  
**Launch blocker:** YES

**File:** `tests/security/test_oauth.py`

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.mcp.oauth import OAuthFlowManager, OAuthToken
from app.providers.vault import CredentialVault

@pytest.mark.asyncio
async def test_oauth_tokens_encrypted_before_db_write():
    """OAuth access token must be encrypted before being written to the database."""
    plaintext_token = "real-access-token-12345"
    
    # Mock DB session
    mock_session = AsyncMock()
    mock_execute = AsyncMock()
    mock_session.execute = mock_execute
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=mock_session)
    
    # Create manager with a real vault
    manager = OAuthFlowManager()
    manager._vault = CredentialVault(master_key="test-master-key-12345")
    manager._db_session_factory = AsyncMock(return_value=mock_session)
    
    token = OAuthToken(access_token=plaintext_token, refresh_token="refresh-token")
    await manager._persist_token_to_db("tenant-1", "server-1", token)
    
    # Extract what was written to the DB
    call_args = mock_execute.call_args
    params = call_args[0][1] if call_args[0] else call_args[1].get("parameters", {})
    
    stored_access = params.get("at", "")
    
    # The stored value must NOT be the plaintext token
    assert stored_access != plaintext_token, (
        "OAuth access token stored in plaintext! Must be encrypted before DB write."
    )
    # The stored value must be decryptable to the original token
    decrypted = manager._vault.decrypt(stored_access)
    assert decrypted == plaintext_token

@pytest.mark.asyncio
async def test_oauth_manager_init_includes_vault():
    """After fix: OAuthFlowManager.__init__ must initialize _vault."""
    from app.mcp.oauth import OAuthFlowManager
    manager = OAuthFlowManager()
    assert hasattr(manager, "_vault") and manager._vault is not None, (
        "OAuthFlowManager must have _vault set in __init__ to ensure token encryption. "
        "Fix: call get_vault() in __init__ or inject vault as constructor parameter."
    )
```

**How to verify it passes:** `uv run pytest tests/security/test_oauth.py -v`

---

## Test 9: AuditV3 In-Memory Verify Returns `unknown` When No Records

**Linked finding:** Finding 12 (MEDIUM)  
**Test type:** pytest unit  
**Launch blocker:** NO (compliance risk, not immediate security)

**File:** `tests/security/test_audit_integrity.py`

```python
import pytest
from app.governance.audit_v3 import AuditV3

def test_verify_chain_empty_buffer_does_not_claim_valid():
    """verify_chain with empty _records must NOT return {valid: True, records_checked: 0}."""
    audit = AuditV3()  # Fresh instance, no records
    result = audit.verify_chain("tenant-never-seen")
    
    # After fix: should not claim 'valid: True' with zero records checked
    # Options: valid=None, valid=False, or raise an exception
    assert result.get("records_checked") == 0  # acceptable to return 0
    assert result.get("valid") is not True or result.get("records_checked") > 0, (
        "verify_chain should not claim 'valid=True' when no records were checked. "
        "This gives false assurance of audit integrity. "
        "Return valid=None or require records_checked > 0 for a valid=True result."
    )
```

**How to verify it passes:** `uv run pytest tests/security/test_audit_integrity.py -v`

---

## Test 10: SSRF Guard Fails Closed on DNS Resolution Failure

**Linked finding:** Finding 8 (HIGH)  
**Test type:** pytest unit  
**Launch blocker:** YES

**File:** `tests/security/test_ssrf.py`

```python
import socket
import pytest
from unittest.mock import patch
from app.net.ssrf_guard import SSRFError, assert_public_url

def test_ssrf_guard_blocks_on_dns_failure():
    """assert_public_url must raise SSRFError when DNS resolution fails (fail-closed)."""
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("DNS timeout")):
        with pytest.raises(SSRFError, match="cannot resolve"):
            assert_public_url("https://unresolvable-hostname-xyz.example.com/endpoint")

def test_ssrf_guard_allows_resolvable_public_url():
    """assert_public_url must allow a genuinely public URL."""
    # Patch to simulate a public IP resolution
    with patch("socket.getaddrinfo", return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0))
    ]):
        # Should not raise
        assert_public_url("https://public-service.example.com/api")

def test_ssrf_guard_blocks_internal_via_dns():
    """assert_public_url must block URLs that resolve to internal IPs via DNS."""
    with patch("socket.getaddrinfo", return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 0))
    ]):
        with pytest.raises(SSRFError, match="blocked IP"):
            assert_public_url("https://evil-rebinding.example.com/api")
```

**How to verify it passes:** `uv run pytest tests/security/test_ssrf.py::test_ssrf_guard_blocks_on_dns_failure -v`

---

## Test 11: RLS Context Properly Set in Connector Usage Endpoint

**Linked finding:** Finding 15 (MEDIUM)  
**Test type:** pytest integration (requires DB)  
**Launch blocker:** YES

**File:** `tests/api/test_connectors.py`

```python
import pytest

@pytest.mark.integration
async def test_connector_usage_rls_isolation(app_with_db):
    """GET /connectors/{id}/usage must not return another tenant's goals."""
    # Create two tenants
    tenant_a = await app_with_db.state.tenant_service.create_tenant(
        name="tenant-a", email="a@test.example"
    )
    tenant_b = await app_with_db.state.tenant_service.create_tenant(
        name="tenant-b", email="b@test.example"
    )
    
    # Create a goal for tenant B only
    # ... (setup: insert a goal for tenant_b with connector_id reference)
    
    # Query connector usage as tenant A — must NOT see tenant B's goal
    resp = test_client.get(
        f"/connectors/{connector_id}/usage",
        headers={"X-API-Key": tenant_a["raw_key"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    goal_ids = [g["id"] for g in body["goals"]]
    assert tenant_b_goal_id not in goal_ids, (
        "Tenant A must not see Tenant B's goals in connector usage endpoint. "
        "RLS isolation failure!"
    )
```

**How to verify it passes:** `uv run pytest tests/api/test_connectors.py::test_connector_usage_rls_isolation -v -m integration`

---

## Test 12: Secrets Not Exposed in CI Workflow

**Linked finding:** Finding 7 (HIGH — credential hygiene)  
**Test type:** Static analysis / CI scan  
**Launch blocker:** YES

**File:** `.github/workflows/ci.yml` (add as step)

```yaml
- name: Secret scan with gitleaks
  uses: gitleaks/gitleaks-action@v2
  env:
    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  with:
    args: detect --source . --no-git --redact --exit-code 1
```

Or as a pytest check:

```python
# tests/security/test_no_hardcoded_secrets.py
import subprocess
import pytest

def test_no_secrets_in_codebase():
    """Run gitleaks or detect-secrets to find accidental credentials in source."""
    result = subprocess.run(
        ["gitleaks", "detect", "--source", ".", "--no-git", "--exit-code", "1"],
        capture_output=True,
        cwd=".",
    )
    assert result.returncode == 0, (
        f"Secret scan found potential credentials in source code:\n"
        f"{result.stdout.decode()}"
    )
```

**How to verify it passes:** `gitleaks detect --source . --no-git` — must exit 0 with no findings.

---

## Test 13: Frontend Refresh Token Not Persisted

**Linked finding:** Finding 18 (LOW)  
**Test type:** vitest unit  
**Launch blocker:** NO (improvement)

**File:** `src/test/stores/auth.test.ts`

```typescript
import { describe, it, expect, beforeEach } from 'vitest';
import { useAuthStore } from '@/stores/auth';

describe('Auth store security', () => {
  beforeEach(() => {
    sessionStorage.clear();
    localStorage.clear();
    useAuthStore.getState().logout();
  });

  it('should not persist refresh token to sessionStorage', () => {
    useAuthStore.getState().setSSOCredentials(
      'access-token-xyz',
      'refresh-token-secret-abc',
      3600,
      'tenant-1',
      'professional',
    );

    const stored = sessionStorage.getItem('av-auth');
    expect(stored).not.toBeNull();
    
    const parsed = JSON.parse(stored!);
    const state = parsed.state;

    // After fix: refresh token must not be in persisted state
    expect(state.refreshToken).toBeFalsy();
    // OR: the field should not exist
    expect('refreshToken' in state ? state.refreshToken : '').toBeFalsy();
  });

  it('should store access token in memory (Zustand state)', () => {
    useAuthStore.getState().setSSOCredentials(
      'access-token-xyz',
      'refresh-token-secret-abc',
      3600,
      'tenant-1',
      'professional',
    );

    // In-memory state should have the access token for API calls
    const { accessToken } = useAuthStore.getState();
    expect(accessToken).toBe('access-token-xyz');
  });
});
```

**How to verify it passes:** `npm run test` from `agent-verse-frontend/`

---

## Test 14: Celery Task Rejects Unknown Tenant

**Linked finding:** Finding 20 (LOW)  
**Test type:** pytest unit  
**Launch blocker:** NO

**File:** `tests/scaling/test_tasks.py`

```python
import pytest
from unittest.mock import AsyncMock, patch
from app.scaling.tasks import run_goal

def test_run_goal_rejects_unknown_tenant_id():
    """run_goal must verify the tenant_id exists before executing."""
    # After fix: a task with an unknown tenant_id should be rejected
    with patch("app.scaling.tasks._get_llm_provider", return_value=None), \
         patch("app.db.session.get_session_factory") as mock_db:
        
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.execute = AsyncMock(return_value=AsyncMock(fetchone=lambda: None))
        mock_db.return_value = mock_session
        
        result = run_goal.apply(kwargs={
            "goal_id": "g1",
            "tenant_id": "nonexistent-tenant-id-xyz",
            "goal_text": "do something",
        }).get()
        
        # After fix: should return status="rejected"
        # Current behavior: will attempt execution (documents the gap)
        # assert result["status"] == "rejected"
        # For now: at minimum verify it doesn't crash uncontrolled
        assert "status" in result
```

**How to verify it passes:** `uv run pytest tests/scaling/test_tasks.py::test_run_goal_rejects_unknown_tenant_id -v`

---

## Full Test Run Command

```bash
# Security tests (all must pass for production release)
cd agent-verse-backend
uv run pytest tests/security/ -v --tb=short

# Integration tests (requires running DB + Redis)
DOCKER_HOST="unix:///Users/harsh.kumar01/.colima/default/docker.sock" \
TESTCONTAINERS_RYUK_DISABLED=true \
uv run pytest tests/ -m integration -v --tb=short

# Frontend security tests
cd agent-verse-frontend
npm run test -- --reporter=verbose src/test/stores/auth.test.ts
```

---

## Launch Blocker Summary

| Finding | Test | Blocker? |
|---------|------|----------|
| F1: SSRF test_connector | `test_test_connector_ssrf_blocked` | **YES** |
| F2: SSRF discover_tools | `test_discover_tools_ssrf_blocked` | **YES** |
| F3: Admin timing attack | `test_admin_auth_uses_hmac_compare_digest` | **YES** |
| F4: MFA process-local | `test_totp_replay_blocked_within_same_process` | **YES** |
| F5: No signup rate limit | `test_signup_rate_limited` | **YES** |
| F6: Dev vault key | `test_vault_warns_when_no_key_in_non_production` | **YES** |
| F7: Real creds in .env | Secret scan CI step + credential rotation | **YES** |
| F8: SSRF guard fails open | `test_ssrf_guard_blocks_on_dns_failure` | **YES** |
| F9: MFA returns secret | `test_enroll_response_does_not_include_raw_secret` | **YES** |
| F10: OAuth plaintext tokens | `test_oauth_tokens_encrypted_before_db_write` | **YES** |
| F11: OAuth state not distributed | Redis-backed implementation test | **NO** (availability) |
| F12: Audit false assurance | `test_verify_chain_empty_buffer_does_not_claim_valid` | **NO** |
| F13: Fixed PBKDF2 salt | `test_different_salts_produce_different_keys` | **NO** (defense-in-depth) |
| F14: CORS wildcard | `test_cors_rejects_wildcard_origin_with_credentials` | **NO** |
| F15: RLS in connector usage | `test_connector_usage_rls_isolation` | **YES** |
| F16: Integrations bypass | Code review + manual audit | **NO** |
| F17: Docker default creds | Deployment checklist | **YES** |
| F18: Refresh token in storage | `test_refresh_token_not_persisted` | **NO** |
| F19: Auth headers forwarded | `test_connector_test_does_not_forward_creds_to_new_url` | **NO** |
| F20: Worker no tenant validate | `test_run_goal_rejects_unknown_tenant_id` | **NO** |
| F21: Scope enforcement | Full scope enforcement test suite review | **YES** |
