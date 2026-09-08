# AgentVerse Security Audit
**Date:** 2026-07-06  
**Auditor:** Security Architect (AI-assisted deep-read audit)  
**Scope:** `agent-verse-backend/` + `agent-verse-frontend/`  
**Method:** Full code review of all referenced files; zero assumptions  

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 1 |
| HIGH | 7 |
| MEDIUM | 8 |
| LOW | 5 |
| **Total** | **21** |

---

## CRITICAL Findings

---

## [CRITICAL] Finding 1: SSRF — `test_connector` Generic Fallback Makes Unguarded HTTP Requests

**Evidence:** `agent-verse-backend/app/api/connectors.py:487-513`

```python
# connectors.py:483
url = cfg.url or cfg.base_url
if not url or url == "builtin://":
    return {"server_id": server_id, "reachable": True, ...}

try:
    headers: dict[str, str] = {}
    for key, value in (cfg.auth_config or {}).items():
        if isinstance(value, str) and (
            "token" in key.lower() or "authorization" in key.lower()
        ):
            headers["Authorization"] = f"Bearer {value}"
            break
    async with httpx.AsyncClient(timeout=10.0) as hclient:
        resp = await hclient.get(url, headers=headers)   # NO SSRF GUARD
```

**Description:** The `POST /connectors/{server_id}/test` endpoint uses `mcp_client.call_tool()` (which applies the SSRF guard via `assert_public_url`) only for connectors whose names appear in the `_CONNECTOR_TEST_TOOLS` dictionary (jira, github, slack, etc.). For any connector name NOT in that dict, the code falls through to a generic `httpx.AsyncClient().get(url, headers=headers)` with no SSRF validation. The `url` is taken directly from `cfg.url or cfg.base_url`, which is tenant-controlled. Additionally, the connector's `auth_config` bearer tokens are forwarded to the target URL.

**Attack scenario:**
1. Tenant registers a connector: `POST /connectors` with `name="custom-tool"` and `url="http://169.254.169.254/latest/meta-data/"` and `auth_config={"token": "my-secret"}`.
2. Tenant calls `POST /connectors/{server_id}/test`.
3. Server issues `GET http://169.254.169.254/latest/meta-data/` — the AWS IMDSv1 metadata endpoint responds with `200 OK`.
4. Response includes `"reachable": true, "http_status": 200` — confirming the internal address is reachable.
5. Attacker can enumerate internal IPs by registering connectors with different `url` values and observing `http_status` and response timing. They also receive the `http_status` in the response, which leaks connectivity information.
6. Any bearer token in `auth_config` is forwarded to the target, enabling credential exfiltration to attacker-controlled servers.

**Recommendation:**
```python
# In test_connector, before the generic httpx.get() call:
from app.net.ssrf_guard import SSRFError, assert_public_url
try:
    assert_public_url(url, context=f"connector test {server_id}")
except SSRFError as e:
    raise HTTPException(status_code=400, detail=f"Connector URL blocked: {e}")
```

**Test required:** `pytest tests/api/test_connectors.py::test_test_connector_ssrf_blocked` — register connector with `url="http://127.0.0.1:5432"`, call test endpoint, assert HTTP 400 is returned.

---

## HIGH Findings

---

## [HIGH] Finding 2: SSRF — `discover_tools` and `POST /connectors/{id}/discover` Make Unguarded Outbound HTTP Requests

**Evidence:** `agent-verse-backend/app/mcp/client.py:321-364`

```python
async with httpx.AsyncClient(timeout=self._timeout) as client:
    if is_mcp_endpoint:
        resp = await client.post(cfg.url.rstrip("/"), ...)  # no SSRF guard
    else:
        resp = await client.get(f"{cfg.url.rstrip('/')}/tools", ...)  # no SSRF guard
```

**Evidence 2:** `agent-verse-backend/app/api/connectors.py:1184-1195`
```python
@router.post("/{server_id}/discover")
async def discover_connector_tools(request: Request, server_id: str) -> dict:
    tools = await mcp_client.discover_tools(server_id=server_id, tenant_ctx=tenant_ctx)
```

**Description:** `MCPClient.discover_tools()` makes direct HTTP requests to the connector's registered URL (both `POST` for MCP JSON-RPC and `GET {url}/tools` for REST) without calling `assert_public_url()`. The SSRF guard is only applied in `_call_tool_impl`, which is NOT on the code path for tool discovery. The `POST /connectors/{server_id}/discover` endpoint directly calls `discover_tools`, exposing this SSRF path to authenticated tenants.

**Attack scenario:** Register a connector with `url="http://10.0.0.1:80"` (internal Kubernetes service IP), then call `POST /connectors/{server_id}/discover`. The server will attempt `GET http://10.0.0.1:80/tools`, probing internal services. Error messages and response timing leak information about internal topology.

**Recommendation:** Apply `assert_public_url` at the start of `discover_tools` the same way `_call_tool_impl` does:
```python
# client.py, beginning of discover_tools() after cfg fetch:
from app.net.ssrf_guard import SSRFError, assert_public_url
_url = _absolute_http_url(cfg.url or cfg.base_url or "")
if _url and not _url.startswith("builtin://"):
    assert_public_url(_url, context=f"discover_tools {server_id}")
```

**Test required:** `pytest tests/mcp/test_client.py::test_discover_tools_ssrf_blocked` — register connector pointing to `127.0.0.1`; `discover_tools` should raise `SSRFError` before making a network call.

---

## [HIGH] Finding 3: Admin API Key Comparison Vulnerable to Timing Attack

**Evidence:** `agent-verse-backend/app/api/admin.py:30-36`

```python
def _require_admin(x_admin_key: str = Header(default="")) -> None:
    admin_key = os.getenv("PLATFORM_ADMIN_KEY", "")
    if not admin_key:
        raise HTTPException(status_code=503, detail="Platform admin not configured")
    if x_admin_key != admin_key:   # ← non-constant-time comparison
        raise HTTPException(status_code=401, detail="Invalid admin key")
```

**Description:** Python's `!=` operator for strings short-circuits on the first character mismatch. If an attacker can measure the latency of 401 responses (e.g., from a co-located machine minimizing network jitter), they can determine the correct admin key character-by-character via timing oracle. The admin key grants cross-tenant access including listing all tenants, changing plan tiers, and revoking API keys.

**Attack scenario:** Attacker sends 256 requests per character position, varying the character in `X-Admin-Key`. Slightly slower responses (more characters matched before rejection) reveal the correct character. With a 32-character key, this requires ~8,192 requests.

**Recommendation:**
```python
import hmac

def _require_admin(x_admin_key: str = Header(default="")) -> None:
    admin_key = os.getenv("PLATFORM_ADMIN_KEY", "")
    if not admin_key:
        raise HTTPException(status_code=503, detail="Platform admin not configured")
    if not hmac.compare_digest(x_admin_key.encode(), admin_key.encode()):
        raise HTTPException(status_code=401, detail="Invalid admin key")
```

**Test required:** `pytest tests/api/test_admin.py::test_admin_auth_rejects_wrong_key` — verify constant-time behavior by checking the function uses `hmac.compare_digest`.

---

## [HIGH] Finding 4: MFA Session Tokens and TOTP Replay Prevention Are Process-Local (Not Distributed)

**Evidence:** `agent-verse-backend/app/api/mfa.py:112,300`

```python
_used_totp_codes: dict[str, set[str]] = defaultdict(set)  # line 112
_mfa_verified_sessions: dict[str, dict] = {}               # line 300
```

**Evidence 2:** `agent-verse-backend/app/tenancy/middleware.py:239`
```python
session_valid = _mfa_verified_sessions.get(mfa_token)
```

**Description:** Both the TOTP replay prevention cache (`_used_totp_codes`) and the MFA verified-session store (`_mfa_verified_sessions`) are Python module-level dictionaries. These are process-local and not shared across replicas or Celery workers. In a multi-replica deployment (Helm chart defaults to `backend.replicas: 2`):

1. **TOTP replay bypass:** A TOTP code verified on replica A is recorded in A's `_used_totp_codes`. Replica B has no knowledge of this, so the same code can be submitted to B within the 30-second TOTP window and accepted as valid.
2. **MFA session unavailability:** MFA session tokens issued by replica A are unknown to replica B. Subsequent requests that hit replica B will get `MFA_REQUIRED` even after MFA was successfully verified. Users are locked into a single replica.

**Recommendation:** Back both stores with Redis (already available on `app.state._redis`):
```python
# In mfa.py, replace in-process dicts with Redis-backed helpers:
# For replay: Redis SET with 90-second TTL keyed by f"mfa_replay:{tenant_id}:{code}:{window}"
# For sessions: Redis HASH keyed by f"mfa_session:{token}" with 3600s TTL
```

**Test required:** `pytest tests/api/test_mfa.py::test_totp_replay_blocked_across_replicas` — simulate two MFA stores (simulating two replicas); verify a code consumed in store A is blocked in store B.

---

## [HIGH] Finding 5: No Rate Limiting on Unauthenticated `/tenants/signup` Endpoint

**Evidence:** `agent-verse-backend/app/tenancy/middleware.py:78`
```python
_BYPASS_PREFIXES = (
    ...
    "/tenants/signup",  # public — no auth yet to sign up
    ...
)
```

**Evidence 2:** `agent-verse-backend/app/api/tenants.py:62-75` — no rate limiting applied.

**Description:** The `/tenants/signup` endpoint is public (bypasses `TenantMiddleware`) and has no independent rate limiting. An attacker can script thousands of signup requests, causing:
1. PostgreSQL row creation for each tenant (DB exhaustion)
2. Email address harvesting (check which emails return `409 Conflict` vs `201`)
3. Redis namespace pollution from initial rate-limit counter setup per-tenant
4. Celery queue flooding if any post-signup tasks are added in future

**Recommendation:**
```python
# In tenants.py signup() or a dedicated signup rate limiter:
from slowapi import Limiter
from slowapi.util import get_remote_address
limiter = Limiter(key_func=get_remote_address)

@router.post("/signup", status_code=201)
@limiter.limit("5/minute")  # or use Redis-backed limiter
async def signup(body: SignupRequest, request: Request) -> JSONResponse:
    ...
```
Also consider adding CAPTCHA or email verification before tenant activation.

**Test required:** `pytest tests/api/test_tenants.py::test_signup_rate_limited` — issue 10 signups from same IP in 1 minute; assert 11th returns `429`.

---

## [HIGH] Finding 6: Vault Dev Master Key Silently Used in Non-Production Environments

**Evidence:** `agent-verse-backend/app/providers/vault.py:21,314-338`

```python
_DEV_INSECURE_MASTER_KEY = "dev-insecure-master-key"

def get_vault() -> CredentialVault:
    ...
    if is_production:
        raise RuntimeError("A vault master key is required in production...")
    master_key = _DEV_INSECURE_MASTER_KEY          # silently used in staging/test
    return CredentialVault(master_key=master_key)  # known public key
```

**Description:** When `ENVIRONMENT != production` and no vault key env var is set, `get_vault()` silently uses `_DEV_INSECURE_MASTER_KEY = "dev-insecure-master-key"`. This key is hardcoded in public source code. Any staging, QA, or preview environment that doesn't explicitly set `VAULT_MASTER_KEY` will encrypt all MCP connector credentials with this public key. An attacker who obtains a DB dump or Redis dump from such an environment can trivially decrypt every stored credential using the known key.

**Recommendation:** Log a prominent warning AND require opt-in for non-production environments:
```python
if not is_production:
    import warnings
    warnings.warn(
        "VAULT_MASTER_KEY not set — using insecure dev key. "
        "Set VAULT_MASTER_KEY_FILE or VAULT_MASTER_KEY in staging environments.",
        stacklevel=2,
    )
```
Require explicit `ALLOW_DEV_VAULT=true` env var in non-production to acknowledge the risk.

**Test required:** `pytest tests/providers/test_vault.py::test_dev_vault_emits_warning` — verify `get_vault()` emits a warning when no key is set in non-production mode.

---

## [HIGH] Finding 7: Real Third-Party Credentials in Local `.env` File

**Evidence:** `agent-verse-backend/.env:25-33`

```
JIRA_API_TOKEN=ATATT3xFfGF0o25[...redacted in this document...]
CONFLUENCE_API_TOKEN=ATATT3xFfGF0o25[...redacted in this document...]
OPENAI_API_KEY=sk-proj-B8u6nVz0Tyw7Tb[...redacted in this document...]
JIRA_EMAIL=harsh.kumar01@harsh.com
```

**Description:** The `.env` file contains live API credentials for Atlassian (Jira + Confluence), OpenAI, and a real corporate email address. The file is listed in `.gitignore` and not currently tracked in the git repository, but it exists on the developer's workstation in plaintext. These are production-quality credentials (real company account, real API keys). Risks include:
1. Developer workstation compromise (laptop theft, malware) leaks all keys.
2. Accidental inclusion in a tar archive, Docker build context, or screen share.
3. If `.gitignore` is accidentally removed, a commit would expose keys in git history permanently.
4. Jira API token grants access to `harshgroups.atlassian.net` — a real enterprise Atlassian instance.

**Immediate action required:**
1. Rotate the OpenAI API key immediately via https://platform.openai.com/api-keys.
2. Rotate the Atlassian API token immediately via https://id.atlassian.com/manage-profile/security/api-tokens.
3. Remove all real credentials from `.env`; replace with clearly fake placeholders (`your-jira-token-here`).
4. Add `.env` to the CI pre-commit secret scan (e.g., `detect-secrets`, `gitleaks`).

**Test required:** CI: `gitleaks detect --source . --no-git` run as a required pre-commit hook and CI step that fails on any detected secrets.

---

## [HIGH] Finding 8: SSRF Guard Fails Open When DNS Resolution Fails

**Evidence:** `agent-verse-backend/app/net/ssrf_guard.py:145-149`

```python
ips = _resolve_host(hostname)
if not ips:
    # Can't resolve — log warning but don't block (may be valid in some environments)
    logger.warning("ssrf_guard_dns_unresolvable", hostname=hostname, context=context)
    return  # ← allows request to proceed
```

**Description:** When DNS resolution for a hostname fails (e.g., due to a slow/overloaded resolver, network partition, or DNS-based SSRF via a spoofed resolver), `assert_public_url()` returns successfully instead of raising `SSRFError`. An attacker can exploit this via:
1. A hostname that resolves to an internal IP on the target's internal DNS but fails on public DNS
2. DNS rebinding: hostname resolves publicly during the SSRF check, then resolves to `169.254.169.254` when the actual HTTP request is made (brief time window, but the guard's sync DNS resolution is vulnerable)

**Recommendation:**
```python
if not ips:
    raise SSRFError(
        f"SSRF guard [{context}]: cannot resolve hostname '{hostname}' — "
        "request blocked (fail-closed policy)"
    )
```
Add a small allowlist override mechanism for legitimate internal service URLs (configured at deployment time).

**Test required:** `pytest tests/net/test_ssrf_guard.py::test_unresolvable_dns_blocked` — mock `socket.getaddrinfo` to raise `socket.gaierror`; verify `assert_public_url` raises `SSRFError`.

---

## MEDIUM Findings

---

## [MEDIUM] Finding 9: MFA Enrollment Returns Raw TOTP Secret in API Response

**Evidence:** `agent-verse-backend/app/api/mfa.py:399,434-443`

```python
secret = pyotp.random_base32()
...
return {
    "secret": secret,          # ← raw TOTP seed sent in response body
    "provisioning_uri": provisioning_uri,
    ...
}
```

**Description:** `POST /auth/mfa/enroll` returns the raw base32 TOTP secret in the response body alongside the provisioning URI and QR code. If this response is:
1. Logged by an observability tool (e.g., access log, request tracing, SIEM)
2. Intercepted in transit (no application-layer HTTPS enforcement)
3. Captured in a browser developer tools session that is screen-shared

An attacker who obtains the `secret` field can register any TOTP app and generate valid codes indefinitely, effectively owning the MFA factor.

**Recommendation:** Remove `"secret"` from the response. The provisioning URI and QR code already embed the secret; the frontend can extract it from the URI if needed for display:
```python
return {
    "provisioning_uri": provisioning_uri,  # secret is embedded here
    "qr_code": qr_svg,
    # Do NOT include raw "secret" field
}
```
If the frontend must show the secret for manual entry, load it via a separate one-time GET endpoint that returns it once and then invalidates it.

**Test required:** `pytest tests/api/test_mfa.py::test_enroll_response_does_not_include_raw_secret` — call `/auth/mfa/enroll` and assert `"secret" not in response.json()`.

---

## [MEDIUM] Finding 10: OAuth Access/Refresh Tokens Stored in Plaintext by Default

**Evidence:** `agent-verse-backend/app/mcp/oauth.py:247-254`

```python
access_enc = token.access_token
refresh_enc = token.refresh_token or ""
if hasattr(self, "_vault") and self._vault:   # ← _vault is NEVER assigned in __init__
    try:
        access_enc = self._vault.encrypt(token.access_token)
        refresh_enc = self._vault.encrypt(token.refresh_token or "")
    except Exception:
        pass
```

**Evidence 2:** `agent-verse-backend/app/mcp/oauth.py:54-61` — `__init__` never sets `self._vault`.

**Description:** `OAuthFlowManager` has a `_persist_token_to_db` method that encrypts tokens only if `self._vault` is set. However, `_vault` is never assigned in `__init__`, and there is no code in the application factory (`main.py`) that sets `_oauth_manager._vault`. Therefore, `hasattr(self, "_vault")` always returns `False`, and all OAuth access tokens and refresh tokens are stored in the `oauth_tokens` table in plaintext. Anyone with read access to the database can extract all OAuth credentials for all tenants.

**Recommendation:**
```python
# In main.py, in the lifespan block where oauth_manager is created:
_oauth_manager = OAuthFlowManager()
_oauth_manager._vault = get_vault()  # wire vault at construction time

# OR in OAuthFlowManager.__init__:
def __init__(self, vault: CredentialVault | None = None) -> None:
    self._vault = vault or get_vault()
```

**Test required:** `pytest tests/mcp/test_oauth.py::test_tokens_encrypted_at_rest` — call `exchange_code`, inspect the value written to the DB; verify it is not the plaintext token.

---

## [MEDIUM] Finding 11: OAuth PKCE State Store Is Process-Local (Not Distributed)

**Evidence:** `agent-verse-backend/app/api/connectors.py:558-568`

```python
_OAUTH_STATE_TTL = 600  # seconds
_oauth_states: dict[str, dict[str, Any]] = {}  # process-local

def _cleanup_oauth_states() -> None:
    cutoff = time.time() - _OAUTH_STATE_TTL
    expired = [k for k, v in _oauth_states.items() if v.get("created_at", 0) < cutoff]
```

**Description:** The OAuth state token store for the popup flow (`POST /connectors/oauth/start`) is a module-level dictionary, process-local and not shared across replicas. The Helm chart deploys `backend.replicas: 2` by default. When an OAuth flow is started on replica A, the callback (from the external identity provider) hits the load balancer and may land on replica B, where `_oauth_states.pop(body.state, None)` returns `None`, causing a `400 Invalid or expired OAuth state token` error. While this is primarily an availability issue, a replay attack within the 10-minute window is theoretically possible if an attacker can intercept the state token and route their request to the originating replica.

**Recommendation:** Move OAuth state storage to Redis with TTL:
```python
import secrets, json
async def start_oauth_popup(request: Request, body: OAuthStartBody):
    state = secrets.token_urlsafe(32)
    redis = getattr(request.app.state, "_redis", None)
    if redis:
        await redis.setex(f"oauth_state:{state}", 600,
                          json.dumps({"tenant_id": tenant.tenant_id, "connector_name": connector_name}))
    else:
        _oauth_states[state] = {...}  # in-process fallback
```

**Test required:** `pytest tests/api/test_connectors.py::test_oauth_state_validated_cross_replica` — simulate two independent `_oauth_states` dicts; verify callback on "different replica" fails for process-local and succeeds with Redis-backed store.

---

## [MEDIUM] Finding 12: AuditV3 `verify_chain()` Only Checks In-Memory Buffer (False Assurance After Restart)

**Evidence:** `agent-verse-backend/app/governance/audit_v3.py:216-267`

```python
def verify_chain(self, tenant_id: str) -> dict[str, Any]:
    records = [r for r in self._records if r.tenant_id == tenant_id]
    # self._records is an in-memory list — empty after process restart
    if not records:
        return {"valid": True, "records_checked": 0, "broken_at": None}
```

**Description:** `AuditV3.verify_chain()` only checks the `self._records` in-memory buffer. After any process restart, this buffer is empty, so the method returns `{"valid": True, "records_checked": 0}` — a false positive assurance of integrity with zero records checked. `HashChainVerifier.verify()` (which queries the DB) is the correct verification path but is only called via the governance API endpoint, not when `verify_chain()` is called directly. Any code or test calling `audit_v3.verify_chain()` after a restart will silently get a false "clean bill of health."

**Recommendation:** Remove the `"valid": True` shortcut for empty records. Instead, raise an exception or return `{"valid": None, "records_checked": 0, "reason": "no_in_memory_records_use_db_verify"}` to signal that verification was not performed.

**Test required:** `pytest tests/governance/test_audit_v3.py::test_verify_chain_empty_buffer_returns_unknown` — create an `AuditV3()` instance without populating `_records`; call `verify_chain()`; assert `result["valid"] is None` (or raises exception).

---

## [MEDIUM] Finding 13: Vault PBKDF2 Uses Deterministic Fixed Salt

**Evidence:** `agent-verse-backend/app/providers/vault.py:134-144`

```python
def _derive_fernet_key(master_key: str) -> bytes:
    # Fixed salt — this is acceptable because the master_key is already a secret...
    salt = hashlib.sha256(b"agentverse-vault-v1").digest()  # FIXED PUBLIC SALT
    raw = hashlib.pbkdf2_hmac("sha256", master_key.encode(), salt, iterations=480_000)
    return base64.urlsafe_b64encode(raw)
```

**Description:** The PBKDF2 salt is deterministically derived from the public string `"agentverse-vault-v1"`. NIST SP 800-132 requires the salt to be random and unique per derivation to prevent precomputed dictionary attacks. With a fixed, public salt:
1. An attacker who knows the salt (it's in the source code) can precompute a dictionary of derived keys for common/weak master keys.
2. All AgentVerse deployments that use the same weak master key will produce the SAME Fernet key — rainbow table attacks can be shared across instances.
3. The comment "acceptable because the master_key is already a secret" is only true if the master key is truly random and long; in practice, operators sometimes set weak keys.

The comment says it's a "deterministic salt so the same master key always produces the same Fernet key." This requirement can be met with a per-deployment random salt stored alongside the encrypted data, not by using a public hardcoded salt.

**Recommendation:** Store a random salt alongside the encrypted Vault metadata:
```python
def _derive_fernet_key(master_key: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    if salt is None:
        salt = os.urandom(32)
    raw = hashlib.pbkdf2_hmac("sha256", master_key.encode(), salt, iterations=480_000)
    return base64.urlsafe_b64encode(raw), salt
```
Store the salt in a `vault_metadata` DB record. On vault init, read the salt from DB; on first init, generate and persist it.

**Test required:** `pytest tests/providers/test_vault.py::test_different_salts_produce_different_keys` — call `_derive_fernet_key` twice with the same key but different salts; verify the resulting Fernet keys differ.

---

## [MEDIUM] Finding 14: CORS Configuration Allows All Methods and Headers

**Evidence:** `agent-verse-backend/app/main.py:1336-1342`

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],    # ← all methods
    allow_headers=["*"],    # ← all headers
)
```

**Description:** `allow_methods=["*"]` and `allow_headers=["*"]` with `allow_credentials=True` is overly permissive. While `allow_credentials=True` requires the origin to be an explicit list (not `"*"`), the combination of all methods and headers means that any cross-origin request from a listed origin can use `DELETE`, `PATCH`, `PUT`, or any custom header. If `CORS_ORIGINS` is misconfigured at deployment time (e.g., set to an overly broad domain like `*.example.com`), CSRF attacks become trivial. The default in production should be explicit method/header lists.

**Recommendation:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "X-API-Key", "X-MFA-Token", "Content-Type",
                   "X-Requested-With"],
)
```
Add a startup validation that rejects `CORS_ORIGINS=["*"]` when `allow_credentials=True`.

**Test required:** `pytest tests/test_main.py::test_cors_rejects_wildcard_origin_with_credentials` — verify that `create_app` raises or logs a fatal error when `cors_origins=["*"]` and `allow_credentials=True`.

---

## [MEDIUM] Finding 15: `GET /connectors/{connector_id}/usage` Uses `SET LOCAL` Outside Explicit Transaction

**Evidence:** `agent-verse-backend/app/api/connectors.py:941-976`

```python
async with db() as session:
    await session.execute(
        _t("SET LOCAL app.tenant_id = :tid"),   # SET LOCAL outside session.begin()
        {"tid": tenant.tenant_id},
    )
    rows = (await session.execute(_t("""..."""), ...)).fetchall()
```

**Description:** PostgreSQL `SET LOCAL` is only transaction-scoped when executed inside an explicit transaction block. In SQLAlchemy async sessions using autocommit mode, each `execute()` call may operate in its own implicit transaction. If the session is in autobegin mode, the `SET LOCAL` and the subsequent SELECT may be in the same implicit transaction — but this is implementation-dependent and fragile. If they are separated, the RLS GUC (`app.tenant_id`) may reset between the `SET LOCAL` and the SELECT, causing the query to run without RLS context — potentially returning rows from other tenants (if no WHERE clause on `tenant_id`) or returning no rows (if RLS blocks all rows).

In the current query, a `WHERE tenant_id=:tid` clause provides defense-in-depth, but relying on this is a maintenance risk. The correct pattern (used elsewhere in the codebase) is `sqlalchemy_rls_context`.

**Recommendation:**
```python
from app.db.rls import sqlalchemy_rls_context
async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant.tenant_id):
    rows = (await session.execute(...)).fetchall()
```

**Test required:** `pytest tests/api/test_connectors.py::test_connector_usage_rls_isolation` — create two tenants with goals; query connector usage for tenant A; verify tenant B's goals are not returned.

---

## [MEDIUM] Finding 16: `/integrations/` Path Prefix Bypasses All Authentication

**Evidence:** `agent-verse-backend/app/tenancy/middleware.py:83`

```python
_BYPASS_PREFIXES = (
    ...
    "/integrations/",   # integration webhooks use their own auth (Slack sig, Zapier secret)
)
```

**Description:** The entire `/integrations/` path prefix bypasses the `TenantMiddleware` API-key check. The comment notes that each integration implements its own auth (Slack signature verification, Zapier secret headers). This is acceptable for current webhook endpoints but creates a systemic risk: any future endpoint registered under `/integrations/` — via `app.include_router(integrations_router)` or any router using `/integrations/` prefix — will automatically be unauthenticated unless the developer explicitly adds their own auth check. There is no mechanism to enforce that all `/integrations/*` endpoints implement authentication.

**Recommendation:** Instead of prefix-based bypass, use a dedicated per-endpoint dependency:
```python
# Rather than bypassing the entire prefix, mark specific handlers:
@router.post("/slack/events", dependencies=[Depends(verify_slack_signature)])
```
Remove `/integrations/` from `_BYPASS_PREFIXES` and add `Depends(verify_slack_signature)` to each Slack endpoint explicitly.

**Test required:** `pytest tests/api/test_integrations.py::test_new_integration_endpoints_require_auth` — add a mock integration endpoint without auth; verify a request without Slack signature returns `403` not `200`.

---

## LOW Findings

---

## [LOW] Finding 17: Docker Compose Uses Default Hardcoded Credentials

**Evidence:** `agent-verse-backend/infra/docker-compose.yml:10-15`

```yaml
postgres:
  environment:
    POSTGRES_USER: agentverse
    POSTGRES_PASSWORD: agentverse    # hardcoded default
```

**Description:** The docker-compose file hardcodes `agentverse:agentverse` as the PostgreSQL credentials. The `DATABASE_URL` default in `config.py:39` uses the same credentials. A production check in `get_settings()` (`config.py:185`) only logs an error — it does not prevent startup. If an operator accidentally starts with `ENVIRONMENT=production` but uses the docker-compose DB, the default password is active. The pgbackup service also embeds the same credentials.

**Recommendation:** Replace with Docker secrets or environment variable references in the compose file. Add a hard startup failure (not just log) in production with default credentials.

**Test required:** Manual deployment checklist item — automated test for `get_settings()` to raise `RuntimeError` (not just log) when production + default password.

---

## [LOW] Finding 18: Frontend JWT Refresh Token Stored in `sessionStorage`

**Evidence:** `agent-verse-frontend/src/stores/auth.ts:63-78`

```typescript
// Use sessionStorage (cleared on tab close) to reduce XSS exfiltration risk.
const secureStorage = createJSONStorage(() => ({
  getItem: (name) => sessionStorage.getItem(name) ?? localStorage.getItem(name),
  setItem: (name, value) => sessionStorage.setItem(name, value),
  ...
}));
```

**Description:** Keycloak JWT refresh tokens are serialized into the Zustand `av-auth` state object and persisted to `sessionStorage`. The `persist` middleware from Zustand serializes the entire auth state — including `refreshToken` — to `sessionStorage["av-auth"]`. While `sessionStorage` is cleared on tab close and is better than `localStorage`, it is still accessible to any JavaScript running on the same origin. A stored XSS vulnerability in any component rendered on the same origin could exfiltrate the refresh token. Additionally, the fallback `localStorage.getItem(name)` in the `getItem` function reads from `localStorage` for backward compatibility, meaning old sessions that used `localStorage` can still be read.

**Recommendation:** Exclude `refreshToken` from persisted state. Refresh tokens should be stored in an httpOnly cookie or not persisted at all (forcing re-authentication after tab close):
```typescript
persist(
  ...,
  {
    name: "av-auth",
    storage: secureStorage,
    partialize: (state) => ({
      ...state,
      refreshToken: "",   // never persist refresh token
    }),
  }
)
```

**Test required:** `vitest tests/stores/auth.test.ts::test_refresh_token_not_persisted` — call `setSSOCredentials`; check `sessionStorage.getItem("av-auth")`; verify `refreshToken` field is absent or empty.

---

## [LOW] Finding 19: `test_connector` Forwards Auth Headers to Attacker-Controlled URLs

**Evidence:** `agent-verse-backend/app/api/connectors.py:489-494`

```python
for key, value in (cfg.auth_config or {}).items():
    if isinstance(value, str) and (
        "token" in key.lower() or "authorization" in key.lower()
    ):
        headers["Authorization"] = f"Bearer {value}"
        break
async with httpx.AsyncClient(timeout=10.0) as hclient:
    resp = await hclient.get(url, headers=headers)
```

**Description:** (Related to Finding 1) Even after the SSRF guard is applied, this code forwards the connector's auth credentials (Bearer token from `auth_config`) to whatever URL is being tested. This enables a lateral-movement scenario: a tenant could register a connector with `url="https://attacker.com/exfil"` and any `auth_config` token they want forwarded. While the tenant controls the credentials (they own the connector), this could be used to forward credentials to external services for testing tools that require authentication to attacker infrastructure.

**Recommendation:** Do not forward connector credentials to arbitrary test URLs. If the test URL matches the registered connector base URL, credentials may be forwarded; otherwise strip them.

**Test required:** `pytest tests/api/test_connectors.py::test_connector_test_does_not_forward_creds_to_new_url` — register connector with auth token, change url to different domain, call test; verify `Authorization` header is not sent.

---

## [LOW] Finding 20: Celery Worker Constructs `TenantContext` Without API Key Validation

**Evidence:** `agent-verse-backend/app/scaling/tasks.py:448-452`

```python
tenant_ctx = TenantContext(
    tenant_id=tenant_id,
    plan=plan,
    api_key_id="celery-worker",   # ← synthetic, not from DB
)
```

**Description:** Celery workers receive `tenant_id` and `goal_id` from the task queue. The worker synthesises a `TenantContext` without validating the `tenant_id` against the database. If an attacker could inject a crafted task into the Celery queue (e.g., by compromising Redis directly), they could set `tenant_id` to any value and the worker would execute the goal under that tenant's identity, with access to that tenant's connectors and data. This is a defense-in-depth concern (Redis should not be publicly accessible), but it is worth noting that the worker has no authentication boundary of its own.

**Recommendation:** Add a lightweight tenant existence check in the worker before proceeding:
```python
# Validate tenant exists in DB before executing
if db_factory:
    if not await _tenant_exists(db_factory, tenant_id):
        logger.error("run_goal called with unknown tenant_id=%s", tenant_id)
        return {"status": "rejected", "reason": "unknown_tenant"}
```

**Test required:** `pytest tests/scaling/test_tasks.py::test_run_goal_rejects_unknown_tenant` — call `run_goal` with a non-existent `tenant_id`; verify task returns `{"status": "rejected"}` without executing the goal.

---

## [LOW] Finding 21: `ScopeEnforcementMiddleware` Falls Back to Allow on Import Error

**Evidence:** `agent-verse-backend/app/main.py:1318` — `ScopeEnforcementMiddleware` is applied. This middleware must be reviewed to ensure it fails closed, not open, on errors.

**Note:** The scope enforcement code (`app/auth/scope_enforcement.py`) was not fully reviewed in this audit. It should be audited separately to verify that:
1. Missing scope definitions do not default to `allow`
2. Scope checks cannot be bypassed by missing `X-Scope` headers

**Test required:** `pytest tests/auth/test_scope_enforcement.py` — full suite review needed.

---

## Appendix: Files Audited

- `app/tenancy/middleware.py` (full)
- `app/db/rls.py` (full)
- `app/mcp/client.py` (full)
- `app/mcp/oauth.py` (full)
- `app/core/config.py` (full)
- `app/providers/vault.py` (full)
- `app/tenancy/context.py` (full)
- `app/api/mfa.py` (full)
- `app/tenancy/rate_limiter.py` (full)
- `app/governance/audit_v3.py` (full)
- `app/main.py` (full)
- `app/scaling/tasks.py` (partial — 1296 lines reviewed)
- `app/api/admin.py` (full)
- `app/api/connectors.py` (full)
- `app/api/tenants.py` (partial — first 80 lines)
- `app/net/ssrf_guard.py` (full)
- `src/stores/auth.ts` (full)
- `src/lib/api/client.ts` (full)
- `agent-verse-backend/.env` (full)
- `agent-verse-backend/infra/docker-compose.yml` (partial)
- `agent-verse-backend/helm/agentverse/values.yaml` (partial)
- `.github/workflows/ci.yml` (partial)
