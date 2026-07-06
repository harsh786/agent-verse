# AgentVerse Threat Model
**Date:** 2026-07-06  
**Scope:** Backend (`agent-verse-backend/`) + Frontend (`agent-verse-frontend/`)  
**Excludes:** SDK packages (`agent-verse-sdk-python/`, `agent-verse-sdk-typescript/`, `agent-verse-github-action/`)

---

## 1. Assets

| Asset | Location | Sensitivity |
|-------|----------|-------------|
| Tenant API keys (hashed SHA-256) | PostgreSQL `api_keys` table | Critical |
| LLM provider API keys (Anthropic, OpenAI, Voyage, Google) | Environment / Vault (AES-256-GCM Fernet) | Critical |
| MCP connector credentials (tokens, passwords, OAuth access/refresh tokens) | Redis (`mcp:connector_secrets:*`) + `oauth_tokens` DB table, Fernet-encrypted | Critical |
| Vault master key | `VAULT_MASTER_KEY` env var or file secret | Critical |
| TOTP secrets (MFA) | `tenant_mfa.encrypted_secret`, Fernet-encrypted | Critical |
| Goal execution data (user instructions, tool arguments) | PostgreSQL `goals`, `goal_events` tables | High |
| Audit chain (hash-linked immutable log) | PostgreSQL `audit_events` table | High |
| Platform admin key (`PLATFORM_ADMIN_KEY`) | Environment variable | High |
| Tenant PII (name, email) | PostgreSQL `tenants` table | High |
| Agent configurations (system prompts, tool lists) | PostgreSQL `agents` table | Medium |
| Knowledge base / RAG documents | PostgreSQL `knowledge_documents` + pgvector | Medium |
| Long-term memory (cross-goal learnings) | PostgreSQL `memories` table | Medium |
| OAuth state tokens (in-flight PKCE flows) | Process memory (`_oauth_states` dict) | Medium |
| MFA session tokens (post-verification) | Process memory (`_mfa_verified_sessions` dict) | Medium |
| Rate-limit counters | Redis sorted sets | Low |
| Celery task queue contents | Redis (`goals.free/starter/professional/enterprise` queues) | Low |

---

## 2. Actors

| Actor | Description | Trust Level |
|-------|-------------|-------------|
| Authenticated Tenant User | Holds a valid API key or Keycloak JWT. Accesses only their own data. | Medium trust (within own tenant boundary) |
| Platform Admin | Holds `PLATFORM_ADMIN_KEY`. Cross-tenant read/write via `/admin/*`. | High trust |
| Keycloak SSO User | Authenticated via OIDC JWT. Tenant resolved from JWT claims. | Medium trust (within own tenant boundary) |
| Celery Worker | Background process. No API key. Reads from Redis queue and DB. | High trust (infrastructure-internal) |
| External MCP Server | Registered HTTP endpoint called by the platform to execute tools. | Low trust (external, user-controlled) |
| Webhook Caller (Slack, Zapier) | Calls `/integrations/*` endpoints with their own signing secrets. | Low trust |
| Unauthenticated Actor | No API key. Can only reach `_BYPASS_PREFIXES` paths. | No trust |
| Malicious Tenant | Authenticated but attempting cross-tenant data access or platform abuse. | Adversarial within own auth boundary |

---

## 3. Trust Boundaries

```
Internet
  │
  ├──► HTTPS → Load Balancer
  │                │
  │                ▼
  │         FastAPI Application
  │         ┌─────────────────────────────────────────────────┐
  │         │  TenantMiddleware (API-key / JWT auth)          │
  │         │  ScopeEnforcementMiddleware (RBAC)              │
  │         │  SecurityHeadersMiddleware                       │
  │         │  CORSMiddleware                                 │
  │         │                                                 │
  │         │  [Trust Boundary: Authenticated Tenant Space]   │
  │         │  ┌─────────────────────────────────────────┐   │
  │         │  │  API Routers (~60 routers)              │   │
  │         │  │  RLS context set per-request via GUC    │   │
  │         │  └─────────────────────────────────────────┘   │
  │         └─────────────────────────────────────────────────┘
  │                │                    │
  │                ▼                    ▼
  │         PostgreSQL + pgvector    Redis (rate-limit, cache, pub/sub)
  │         (RLS enforced)           (no auth in dev)
  │                │
  │                ▼
  │         [Trust Boundary: Celery Worker Space]
  │         ┌───────────────────────────────────────┐
  │         │  run_goal / run_scheduled_goal tasks  │
  │         │  TenantContext synthesised from args  │
  │         │  system_session used for cross-tenant  │
  │         │  DLQ/maintenance writes               │
  │         └───────────────────────────────────────┘
  │
  └──► MCP Tool Calls
       [Trust Boundary: External MCP Servers — SSRF guarded]
       ┌────────────────────────────────────────────┐
       │  call_tool → _call_tool_impl               │
       │  SSRF guard: assert_public_url()           │
       │  discover_tools → NO SSRF GUARD ←── gap   │
       └────────────────────────────────────────────┘
```

---

## 4. Attack Surfaces

| Surface | Exposure | Notes |
|---------|----------|-------|
| REST API (HTTP) | Public internet | ~60 routers; ~800+ endpoints; TenantMiddleware guards all except `_BYPASS_PREFIXES` |
| `/tenants/signup` (unauthenticated) | Public internet | No rate limiting; creates real tenants |
| `/integrations/*` (unauthenticated) | Public internet | Entire prefix bypassed; relies on per-integration secrets |
| `/health`, `/metrics`, `/docs` (unauthenticated) | Public internet | Operational — no tenant data exposed |
| OAuth callback endpoints (`/auth/callback`, `/connectors/oauth/callback`) | Public internet | State validation required |
| SSE streams (`/goals/{id}/stream`) | Authenticated | Per-tenant subscription |
| WebSocket (`/collab/*`) | Authenticated | Collaborative editing |
| Celery task queue (Redis) | Internal | Tasks carry `tenant_id` — worker synthesises TenantContext |
| MCP tool HTTP egress | Outbound from server | SSRF-guarded for `call_tool`; NOT guarded for `discover_tools` and `test_connector` fallback |
| Admin API (`/admin/*`) | Authenticated via `X-Admin-Key` header | Cross-tenant; single long-lived static key |

---

## 5. Top 15 Threat Scenarios

| # | Threat Scenario | STRIDE Category | Severity | Likelihood | Current Mitigation | Remaining Risk |
|---|----------------|-----------------|----------|------------|-------------------|----------------|
| T1 | Attacker registers a connector with `url=http://169.254.169.254/` and calls `POST /connectors/{id}/test` — generic fallback sends unauthenticated GET to cloud metadata service | Information Disclosure / SSRF | **HIGH** | Medium | SSRF guard exists in `_call_tool_impl`; NOT applied in generic test fallback or `discover_tools` | Full cloud metadata exfiltration; internal network probing |
| T2 | Attacker forges Admin API key via timing-attack brute-force against `x_admin_key != admin_key` (non-constant-time string comparison) | Elevation of Privilege | **HIGH** | Low | Admin key checked; short-circuit on first mismatch enables timing oracle | Cross-tenant data access; plan manipulation |
| T3 | Attacker spams `POST /tenants/signup` (unauthenticated, no rate limit) to exhaust DB, Celery queue, or enumerate valid email addresses | Denial of Service / Reconnaissance | **HIGH** | High | None | Resource exhaustion; tenant spam |
| T4 | MFA session tokens stored in process memory only: legitimate user completes MFA on replica A; subsequent request hits replica B and fails MFA check; OR TOTP code used on replica A is replayed on replica B | Broken Authentication | **HIGH** | High (any multi-replica deploy) | In-process rate limiting and replay prevention | MFA availability failure; TOTP replay in multi-replica |
| T5 | Real Jira API token, OpenAI API key, and email address present in plaintext in `.env` file on developer workstation | Information Disclosure | **HIGH** | High (developer machine) | `.env` is in `.gitignore` | Credential theft if workstation is compromised or file shared inadvertently |
| T6 | Attacker submits malicious `openapi_spec` to `POST /connectors/import-openapi` with crafted tool definitions containing injected `http_path` or `base_url` values that cause the platform to call attacker-controlled endpoints | SSRF / Spoofing | **MEDIUM** | Low | Base URL must be public; SSRF guard on `call_tool` but NOT on `_dispatch_openapi_tool` path if called before guard | Internal endpoint invocation if base_url bypasses guard |
| T7 | Tenant submits a goal containing prompt injection to manipulate agent into executing `call_tool` with crafted arguments, e.g. tool args that include exfiltration payloads (`check_tool_args_for_exfil` blocked but bypassable via encoding) | Tampering / Data Exfiltration | **MEDIUM** | Medium | `exfil_guard.check_tool_args_for_exfil`; HITL gate for high-risk tools | Prompt injection via encoding or obfuscation; no complete defense |
| T8 | OAuth PKCE state tokens stored process-locally; in multi-replica deploy, OAuth callback hits different pod than OAuth start, causing state validation to fail, silently registering a connector with `pending_oauth` status that later fails all tool calls | Broken Authentication / DoS | **MEDIUM** | High (multi-replica) | 10-minute TTL on state tokens | OAuth connector unusable in multi-replica; state confusion |
| T9 | `POST /auth/mfa/enroll` returns raw `secret` TOTP key in response body; if response is intercepted (no HTTPS enforcement at the app layer) or logged by an observability tool, attacker can generate valid TOTP codes | Information Disclosure | **MEDIUM** | Medium | Secret is ephemeral (only in response once); requires HTTPS interception | TOTP account takeover if secret leaked |
| T10 | Attacker obtains dev Vault master key (`dev-insecure-master-key`) and decrypts all MCP connector credentials stored in a staging/QA environment that didn't set `VAULT_MASTER_KEY` | Information Disclosure | **MEDIUM** | Medium (non-production envs) | Warning logged only in production mode | Plaintext connector credentials for all staging tenants |
| T11 | Postgres DB credential `agentverse:agentverse` hardcoded in `docker-compose.yml` and config default; if warning bypassed or `ENVIRONMENT` not set to `production`, default password in prod DB | Elevation of Privilege | **MEDIUM** | Low | Production guard logs error if default password detected | Full DB compromise if password not rotated before production |
| T12 | `system_session` RLS bypass used in audit writes (`audit_v3.py:188`) and Celery DLQ tasks; a bug in those code paths could write cross-tenant audit records or goals | Tampering | **MEDIUM** | Low | `system_session` is narrowly scoped; only used in system-level operations | Cross-tenant data corruption if logic bug introduced |
| T13 | OAuth access/refresh tokens stored in plaintext in DB when `OAuthFlowManager._vault` is not set (conditional encryption at `oauth.py:247-254`; `_vault` never assigned in `__init__`) | Information Disclosure | **MEDIUM** | High (default behavior) | DB RLS filters per-tenant; Fernet encryption exists but not activated by default | Connector credentials exposed in DB dump |
| T14 | AuditV3 `verify_chain()` only checks in-memory `_records` buffer; after process restart the buffer is empty, returning `{"valid": True, "records_checked": 0}` — tampered DB records undetected | Repudiation / Tampering | **LOW** | Medium (any restart) | `HashChainVerifier.verify()` reads DB; in-memory shortcut misleads | False chain integrity assurance in governance UI |
| T15 | Tenant with high-volume workload bypasses per-replica rate limiting by load-balancing requests across replicas; in-process fallback counters are per-process, not shared across replicas | Denial of Service | **LOW** | Medium (multi-replica) | Redis-backed sliding window rate limiter is primary; in-process is fallback | Rate limit enforcement degraded if Redis is unavailable in multi-replica |
