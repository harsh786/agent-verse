# AgentVerse OS — Security, Identity & Compliance

**Document status:** Living reference — updated with each compliance milestone
**Audience:** Security engineers, compliance officers, enterprise architects, platform engineers
**Last substantive revision:** See git log

---

## Table of Contents

1. [Identity Architecture](#1-identity-architecture)
   - 1.1 Agent Service Account Credentials
   - 1.2 Tenant Authentication
   - 1.3 Role-Based Access Control
   - 1.4 Scope System
2. [Guardrails — Multi-Layer Content Safety](#2-guardrails--multi-layer-content-safety)
   - 2.1 Six-Layer Architecture
   - 2.2 Pattern Library
   - 2.3 PII Detection & Redaction
   - 2.4 LLM-as-Judge
3. [Compliance Frameworks](#3-compliance-frameworks)
   - 3.1 GDPR
   - 3.2 HIPAA
   - 3.3 SOC2 Type II
   - 3.4 Legal Holds
4. [Encryption & Key Management](#4-encryption--key-management)
5. [Threat Model & Attack Surface](#5-threat-model--attack-surface)
6. [Incident Response & Audit](#6-incident-response--audit)
7. [Security Hardening Checklist](#7-security-hardening-checklist)

---

## Overview

Security in AgentVerse is not an afterthought — it is woven into every layer of the platform, from the database row to the HTTP response header. AI agents operate with real-world tools and real credentials; a breach or a prompt injection is not just a data problem, it is a potential business catastrophe. This document describes in full detail how AgentVerse prevents, detects, and responds to every major class of security failure.

The security model rests on five pillars:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    AgentVerse Security Pillars                      │
├─────────────────────┬───────────────────────────────────────────────┤
│ 1. IDENTITY         │ Who are you? Prove it. Every request, every   │
│                     │ agent, every tenant.                          │
├─────────────────────┼───────────────────────────────────────────────┤
│ 2. AUTHORIZATION    │ What are you allowed to do? Scopes, RBAC,     │
│                     │ ABAC. Enforced at every layer.                │
├─────────────────────┼───────────────────────────────────────────────┤
│ 3. GUARDRAILS       │ What can the LLM say and do? 6-layer content  │
│                     │ safety + semantic policy evaluation.          │
├─────────────────────┼───────────────────────────────────────────────┤
│ 4. COMPLIANCE       │ What data laws apply? GDPR, HIPAA, SOC2,      │
│                     │ legal holds — all codified in the DB.         │
├─────────────────────┼───────────────────────────────────────────────┤
│ 5. AUDIT            │ Who did what, when, and why? Immutable,       │
│                     │ cryptographically-chained event log.          │
└─────────────────────┴───────────────────────────────────────────────┘
```

---

## 1. Identity Architecture

### 1.1 Agent Service Account Credentials

Every agent in AgentVerse is a first-class identity principal. Agents do not borrow user credentials — they possess their own service account credentials with explicit, scoped permissions. This design prevents privilege escalation via agent compromise and enables fine-grained audit trails that show exactly which agent performed which action.

#### Credential Schema

```sql
-- app/db/models/credentials.py (simplified DDL)
CREATE TABLE agent_credentials (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id      UUID         NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    tenant_id     UUID         NOT NULL REFERENCES tenants(id),
    key_type      TEXT         NOT NULL CHECK (key_type IN (
                      'service_account',    -- long-lived agent identity
                      'delegated_user',     -- acting on behalf of a human
                      'machine_token',      -- CI/CD + automation contexts
                      'workload_identity'   -- k8s pod identity federation
                  )),
    key_id        TEXT         NOT NULL,           -- kid in JWT header
    public_key_pem TEXT        NOT NULL,           -- RSA 2048 public key
    private_key_ref TEXT       NOT NULL,           -- vault://tenant/agent/key_id
    scopes        TEXT[]       NOT NULL DEFAULT '{}',
    expires_at    TIMESTAMPTZ,                     -- NULL = non-expiring
    revoked_at    TIMESTAMPTZ,                     -- soft delete, audit preserved
    last_used_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_by    UUID         REFERENCES users(id)
);

CREATE INDEX agent_credentials_agent_idx ON agent_credentials(agent_id)
    WHERE revoked_at IS NULL;
CREATE INDEX agent_credentials_key_id_idx ON agent_credentials(key_id)
    WHERE revoked_at IS NULL;
```

#### JWT Token Design

Agents authenticate API calls via short-lived RS256 JWTs. The 15-minute TTL is deliberate: a stolen token has a narrow exploitability window without needing a revocation check on every request (which would add database latency to every hot path).

```json
{
  "header": {
    "alg": "RS256",
    "typ": "JWT",
    "kid": "tenant_abc/agent_xyz/key_20240601"
  },
  "payload": {
    "iss": "https://agentverse.example.com",
    "sub": "agent:uuid-of-agent",
    "aud": "agentverse-api",
    "iat": 1717200000,
    "exp": 1717200900,
    "jti": "unique-token-id",
    "tenant_id": "uuid-of-tenant",
    "scopes": ["goals:submit", "tools:execute", "knowledge:read"],
    "domain_context": {
      "type": "legal",
      "bar_number": "CA-12345",
      "jurisdiction": "california",
      "clearance_level": "standard"
    }
  }
}
```

The `domain_context` claim carries verified domain metadata embedded at issuance time, so downstream services and guardrails can enforce domain-specific policies without a database lookup on every request.

#### Domain Identity Metadata

Different deployment verticals attach domain-specific identity claims:

| Domain | Claims | Verification |
|--------|--------|-------------|
| Legal | `bar_number`, `jurisdiction`, `clearance_level`, `matter_ids[]` | State bar API at provisioning |
| Healthcare | `npi`, `specialty`, `dea_number`, `baa_verified` | NPI registry lookup |
| Finance | `trader_id`, `desk_id`, `authorized_instruments[]`, `fin_clearance` | Internal HR system |
| Government | `security_clearance`, `agency`, `cac_fingerprint` | CAC card validation |

#### JWKS Endpoint

```
GET /.well-known/jwks.json
```

The JWKS endpoint serves all active public keys in RFC 7517 format, Redis-cached for 10 minutes. When a key is revoked, the Redis cache is invalidated via pub/sub immediately. The cache TTL is a ceiling, not a floor — revocation is instant.

```python
# app/auth/jwks.py
async def get_jwks(redis: Redis) -> dict:
    cached = await redis.get("jwks:public")
    if cached:
        return json.loads(cached)

    keys = await db.fetch_active_public_keys()  # non-expired, non-revoked
    jwks = {"keys": [format_jwk(k) for k in keys]}

    await redis.setex("jwks:public", 600, json.dumps(jwks))
    return jwks

async def revoke_credential(key_id: str, redis: Redis):
    await db.mark_revoked(key_id)
    await redis.delete("jwks:public")              # immediate cache bust
    await redis.publish("jwks:invalidated", key_id) # notify replicas
```

#### Rate-Limited Issuance

Token issuance is rate-limited at 10 tokens/minute per agent using a sliding-window Redis counter. This prevents a compromised orchestration layer from spinning up an unlimited number of short-lived credentials for lateral movement.

```python
# Sliding window rate limit (Redis sorted set)
async def check_issuance_rate_limit(agent_id: str, redis: Redis) -> bool:
    key = f"rate:credential:{agent_id}"
    now = time.time()
    window_start = now - 60  # 1-minute window

    async with redis.pipeline() as pipe:
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zadd(key, {str(uuid4()): now})
        pipe.zcard(key)
        pipe.expire(key, 120)
        _, _, count, _ = await pipe.execute()

    return count <= 10
```

---

### 1.2 Tenant Authentication

#### API Key Design

```
tenants.api_key_hash = SHA256(secrets.token_urlsafe(32))
```

The raw key (`sk_live_...`) is returned exactly once at creation and never stored. Only the SHA-256 hash persists in the database. This means:
- A DB breach does not expose usable API keys
- Key verification is a single SHA-256 hash + DB lookup (fast)
- Compromise requires the attacker to have the actual key

Keys have 256-bit entropy (from `token_urlsafe(32)` which produces 32 bytes = 256 bits of randomness), exceeding NIST SP 800-131A recommendations for symmetric keys.

```python
# app/auth/api_keys.py
import hashlib, secrets

def generate_api_key() -> tuple[str, str]:
    """Returns (raw_key_for_user, hash_for_db)"""
    raw = "sk_live_" + secrets.token_urlsafe(32)
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    return raw, hashed

async def verify_api_key(raw_key: str, redis: Redis, db: AsyncSession) -> Tenant | None:
    hashed = hashlib.sha256(raw_key.encode()).hexdigest()

    # L1: Redis cache (5-min TTL)
    cached = await redis.get(f"apikey:{hashed}")
    if cached:
        return Tenant.model_validate_json(cached)

    # L2: DB lookup
    tenant = await db.scalar(
        select(Tenant).where(Tenant.api_key_hash == hashed, Tenant.is_active == True)
    )
    if tenant:
        await redis.setex(f"apikey:{hashed}", 300, tenant.model_dump_json())

    return tenant
```

#### SSO via Keycloak OIDC

Enterprise customers authenticate via OpenID Connect through Keycloak. The integration supports:

- **JWKS-validated JWTs**: Public keys fetched from Keycloak's JWKS endpoint, cached 10 minutes
- **JIT provisioning**: First login creates a user record automatically with default tenant role
- **Rolling refresh tokens**: Refresh tokens rotated on every use (Keycloak default for public clients)
- **Logout propagation**: Keycloak back-channel logout hits AgentVerse's `/auth/sso/logout` endpoint, invalidating Redis session

```
OIDC Authorization Code Flow:

User Browser          AgentVerse          Keycloak
     │                    │                  │
     │ GET /auth/sso/login│                  │
     │────────────────────>                  │
     │ 302 to Keycloak    │                  │
     │<────────────────────                  │
     │ Login form         │                  │
     │──────────────────────────────────────>│
     │ Auth code          │                  │
     │<──────────────────────────────────────│
     │ POST /auth/sso/callback?code=...      │
     │────────────────────>                  │
     │                    │ Token exchange   │
     │                    │──────────────────>
     │                    │ id_token+access  │
     │                    │<──────────────────
     │                    │ Validate JWT sig │
     │                    │ JIT provision    │
     │ Set-Cookie: session│                  │
     │<────────────────────                  │
```

#### SAML 2.0

For enterprises with existing SAML IdPs (Okta, Azure AD, PingFederate), three binding flows are supported:

1. **SP-Initiated POST**: User hits AgentVerse, redirected to IdP with SAMLRequest
2. **IdP-Initiated POST**: IdP sends SAMLResponse directly (unsolicited)
3. **Artifact Binding**: IdP sends artifact reference, AgentVerse resolves via ArtifactResolve

HMAC replay protection is implemented via `InResponseTo` tracking in Redis with a 5-minute TTL.

#### SCIM 2.0 Provisioning

```
POST /scim/v2/Users          → create user
GET  /scim/v2/Users/{id}     → read user
PUT  /scim/v2/Users/{id}     → update user (full replace)
PATCH /scim/v2/Users/{id}    → update user (partial)
DELETE /scim/v2/Users/{id}   → deprovision (soft delete + role removal)
POST /scim/v2/Groups         → create group → maps to tenant role
```

Automatic de-provisioning is the most important SCIM feature: when an employee is terminated in the corporate IdP, SCIM DELETE fires within minutes, removing their AgentVerse access without any manual steps.

---

### 1.3 Role-Based Access Control

#### Built-in Roles

| Role | Scopes | Intended For |
|------|--------|-------------|
| `admin` | `admin:*` (all scopes) | Platform administrators |
| `operator` | `goals:*`, `agents:*`, `knowledge:*`, `triggers:*` | DevOps, ML engineers |
| `viewer` | `*.read` on all resources | Auditors, stakeholders |
| `approver` | `governance:hitl`, `governance:audit` | Risk officers, compliance |
| `agent_service` | `goals:submit`, `tools:execute`, `knowledge:read` | Agent service accounts |

#### Custom Roles with Inheritance

```python
# app/tenancy/rbac.py
@dataclass
class TenantRole:
    name: str
    parent: str | None        # inherits all parent scopes
    scopes: list[str]         # additional scopes beyond parent
    conditions: dict | None   # ABAC conditions

# Example: "senior_attorney" extends "attorney" and adds privileged access
senior_attorney = TenantRole(
    name="senior_attorney",
    parent="attorney",
    scopes=["legal:privileged_access", "knowledge:admin"],
    conditions={
        "bar_years": {"gte": 10},
        "clearance_level": {"in": ["partner", "senior_counsel"]}
    }
)
```

#### Redis-Backed Role Cache

Role lookups happen on every authenticated request. The cache strategy uses a two-level model:

```
Request → Redis "role:{tenant_id}:{user_id}" (5-min TTL)
             ↓ miss
          Postgres roles JOIN role_scopes JOIN user_roles
             ↓
          Build flattened scope set (resolve parent chains)
             ↓
          Write back to Redis
```

When a role is modified, a Redis pub/sub message `role:invalidated:{tenant_id}` fires, causing all replicas to drop their cached role entries for that tenant.

#### ABAC Conditions

Attribute-Based Access Control adds resource-context checks beyond static scopes:

```python
# app/tenancy/abac.py
ABAC_CONDITIONS = {
    "resource.owner_id == request.user_id": {
        "description": "Users can only modify their own resources",
        "applies_to": ["goals", "api_keys", "knowledge"]
    },
    "request.time.hour IN [9, 17] AND request.ip IN tenant.allowed_cidrs": {
        "description": "Business hours + IP allowlist for financial tenants",
        "applies_to": ["finance:trading_access"]
    },
    "user.department == resource.department": {
        "description": "Cross-department data isolation for enterprise",
        "applies_to": ["knowledge:read", "agents:read"]
    }
}
```

#### IP Allowlist Enforcement

Tenant-level CIDR allowlists are enforced in `TenantMiddleware` before any route handler runs:

```python
# app/tenancy/middleware.py
async def check_ip_allowlist(request: Request, tenant: Tenant, redis: Redis):
    if not tenant.ip_allowlist:
        return  # no restriction configured

    client_ip = get_client_ip(request)  # respects X-Forwarded-For with max_hop config

    cache_key = f"ipcheck:{tenant.id}:{client_ip}"
    cached = await redis.get(cache_key)
    if cached is not None:
        if cached == "0":
            raise HTTPException(403, "IP not in tenant allowlist")
        return

    allowed = any(
        ip_address(client_ip) in ip_network(cidr)
        for cidr in tenant.ip_allowlist
    )
    await redis.setex(cache_key, 300, "1" if allowed else "0")
    if not allowed:
        raise HTTPException(403, "IP not in tenant allowlist")
```

---

### 1.4 Scope System

The scope system uses a `Resource:Action` naming convention with 30+ defined scopes.

#### Scope Taxonomy

```
goals:submit         goals:read          goals:cancel        goals:admin
agents:create        agents:read         agents:update       agents:delete      agents:admin
knowledge:ingest     knowledge:read      knowledge:delete    knowledge:admin
governance:policy    governance:hitl     governance:audit    governance:admin
triggers:create      triggers:read       triggers:delete
tools:execute        tools:list
credentials:issue    credentials:revoke
tenants:read         tenants:admin
billing:read         billing:admin
mcp:connect          mcp:admin
---
Domain-specific:
legal:privileged_access       (attorney-client privilege gating)
healthcare:phi_access         (HIPAA PHI data access)
finance:trading_access        (financial instrument operations)
government:classified_access  (cleared-only resources)
```

#### Endpoint → Scope Mapping

Every FastAPI route declares its required scope as a dependency:

```python
# app/goals/router.py
@router.post("/goals", dependencies=[Depends(require_scope("goals:submit"))])
async def submit_goal(goal: GoalCreate, auth: AuthContext = Depends(get_auth)):
    ...

@router.delete("/goals/{goal_id}", dependencies=[Depends(require_scope("goals:cancel"))])
async def cancel_goal(goal_id: UUID, auth: AuthContext = Depends(get_auth)):
    ...

# app/auth/dependencies.py
def require_scope(scope: str) -> Callable:
    async def check(auth: AuthContext = Depends(get_auth_context)):
        if scope not in auth.scopes and f"{scope.split(':')[0]}:admin" not in auth.scopes:
            raise HTTPException(403, f"Missing required scope: {scope}")
    return check
```

#### Wildcard Scopes for MCP Connectors

MCP connectors expose their tools under a connector-specific namespace. The `jira.*` scope grants access to all Jira tools without enumerating each one:

```python
# app/mcp/policy.py
def is_tool_allowed(tool_name: str, scopes: list[str]) -> bool:
    """
    tool_name: "jira.create_issue"
    Checks: exact match OR wildcard (jira.*) OR admin:*
    """
    if "admin:*" in scopes:
        return True
    prefix = tool_name.split(".")[0]
    return tool_name in scopes or f"{prefix}.*" in scopes
```

---

## 2. Guardrails — Multi-Layer Content Safety

Guardrails are the AI safety layer. They sit between the agent's LLM calls and the real world, preventing prompt injection, information exfiltration, dangerous tool execution, and PII leakage. The design principle is **defense in depth**: no single layer is assumed to be perfect, and the layers collectively provide redundancy.

### 2.1 Six-Layer Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      Guardrail Execution Flow                           │
│                                                                         │
│  User Input → [Layer 1: GOAL] → Planning                               │
│                    │                                                    │
│                    ▼                                                    │
│              [Layer 2: PLAN] → Step scheduling                         │
│                    │                                                    │
│                    ▼                                                    │
│              [Layer 3: STEP] → Tool selection                          │
│                    │                                                    │
│                    ▼                                                    │
│              [Layer 4: TOOL_ARGS] → Tool execution   ← RECURSIVE SCAN │
│                    │                                                    │
│              Tool runs (external system)                               │
│                    │                                                    │
│                    ▼                                                    │
│              [Layer 5: TOOL_OUTPUT] → PII redaction                   │
│                    │                                                    │
│                    ▼                                                    │
│              [Layer 6: FINAL] → Response to user   ← SYSTEM PROMPT    │
│                                                       LEAK DETECTION   │
└─────────────────────────────────────────────────────────────────────────┘
```

#### Layer 1 — Goal Guardrail

Applied to the raw user input before any LLM call. Catches:
- Direct instruction overrides ("Ignore all previous instructions")
- Role-play bypass attempts ("You are now DAN")
- Clearly destructive goals ("Delete all production data")
- Social engineering ("My CEO said it's okay to...")

This is the cheapest layer — purely regex-based, sub-millisecond latency.

#### Layer 2 — Plan Guardrail

After the Planner LLM generates a step-by-step plan, the plan text is scanned. This catches cases where a seemingly innocent goal generates a suspicious plan:

- Goal: "Optimize database performance"
- Plan includes: "DROP INDEX all; VACUUM FULL; TRUNCATE temp_data" → BLOCKED

#### Layer 3 — Step Guardrail

Before each individual step executes, the step description is scanned. This catches adversarial content injected via retrieved knowledge base documents (indirect injection).

#### Layer 4 — Tool Arguments Guardrail (Recursive Scan)

This is the most critical layer. Before any tool is called, all arguments are recursively scanned:

```python
# app/guardrails/layers/tool_args.py
def scan_args_recursively(obj: Any, depth: int = 0) -> list[GuardrailViolation]:
    if depth > 10:  # protect against deeply nested objects
        return []

    violations = []
    if isinstance(obj, str):
        violations.extend(scan_text(obj))
    elif isinstance(obj, dict):
        for key, value in obj.items():
            violations.extend(scan_args_recursively(key, depth + 1))
            violations.extend(scan_args_recursively(value, depth + 1))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            violations.extend(scan_args_recursively(item, depth + 1))
    return violations
```

The recursive scan is critical because tool arguments often contain nested structures where injected content might hide in deep dictionary values or list items.

#### Layer 5 — Tool Output Guardrail (PII Redaction)

All tool outputs are scanned for PII before being fed back to the LLM. This prevents the agent from inadvertently incorporating and re-transmitting sensitive data. Detected PII is replaced with typed placeholders: `[REDACTED:SSN]`, `[REDACTED:CREDIT_CARD]`, `[REDACTED:PHI:MRN]`.

#### Layer 6 — Final Response Guardrail

The final response destined for the user is checked for:
- **System prompt leakage**: presence of internal prompt text that should never be user-visible
- **PII persistence**: any PII that slipped through Layer 5
- **Sensitive credential exposure**: API keys, tokens, passwords in responses

---

### 2.2 Pattern Library

The pattern library is the backbone of regex-based guardrail checks. It contains 100+ patterns organized into five categories.

#### Prompt Injection Patterns

```python
INJECTION_PATTERNS = {
    "direct_instruction_override": [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"disregard\s+(your\s+)?(previous\s+|above\s+)?instructions",
        r"forget\s+(everything|all)\s+(you\s+)(were\s+told|know)",
        r"new\s+instructions?\s*:",
        r"override\s+(your\s+)?(system\s+)?prompt",
        r"your\s+true\s+purpose\s+is",
        r"act\s+as\s+if\s+you\s+have\s+no\s+(restrictions|limitations|guidelines)",
    ],
    "role_play_bypass": [
        r"\bDAN\b",                     # "Do Anything Now"
        r"jailbreak",
        r"developer\s+mode",
        r"you\s+are\s+now\s+\w+\s+who\s+(has\s+no|doesn'?t\s+have)\s+restrictions",
        r"pretend\s+you\s+are\s+(an?\s+)?AI\s+without\s+(rules|restrictions|ethics)",
        r"roleplay\s+as\s+",
    ],
    "encoding_bypass": [
        # ROT13: common bypass technique
        r"[A-Za-z]{20,}.*[ybby|byyvat|vaqbpgevangr]",  # ROT13 of "fool/folling/indoctrinate"
        # Base64 encoded instructions
        r"base64\s*decode",
        r"atob\(",
        # Homoglyph substitution (Cyrillic а for Latin a, etc.)
        r"[\u0430\u0435\u043e\u0440\u0441\u0443\u0445].*(?:ignore|delete|destroy)",
    ],
    "indirect_injection": [
        # Injected via document/knowledge retrieval
        r"---\s*SYSTEM\s*---",
        r"<\|im_start\|>system",        # ChatML injection
        r"\[INST\].*\[\/INST\]",        # Llama instruction injection
        r"Human:\s*Assistant:",         # Claude injection
    ],
    "markdown_injection": [
        r"\[.*\]\(javascript:",
        r"!\[.*\]\(data:text",
        r"<script",
        r"<iframe",
    ]
}
```

#### Destruction Command Patterns

```python
DESTRUCTION_PATTERNS = {
    "cloud_destruction": [
        r"kubectl\s+(delete\s+namespace|delete\s+all)",
        r"terraform\s+destroy",
        r"aws\s+s3\s+rm\s+.*--recursive",
        r"aws\s+s3\s+rb\s+.*--force",
        r"gcloud\s+projects?\s+delete",
        r"az\s+(group\s+delete|ad\s+tenant\s+delete)",
        r"eksctl\s+delete\s+cluster",
        r"gcloud\s+container\s+clusters\s+delete",
    ],
    "database_destruction": [
        r"\bDROP\s+(TABLE|DATABASE|SCHEMA|INDEX)\b",
        r"\bTRUNCATE\b(?!\s+TABLE\s+\w+\s+WHERE)",  # TRUNCATE without WHERE is always destructive
        r"\bDELETE\s+FROM\b(?!\s+\w+\s+WHERE)",      # DELETE without WHERE
        r"DROP\s+ALL\s+TABLES",
        r"RESET\s+DATABASE",
    ],
    "filesystem_destruction": [
        r"rm\s+-[rf]+\s+[/~]",
        r"rm\s+-[rf]+\s+\*",
        r"format\s+[c-zC-Z]:",
        r"mkfs\s+",
        r"dd\s+if=/dev/zero\s+of=",
        r"shred\s+-[uvz]+",
        r"> /etc/passwd",
    ],
    "network_exfiltration": [
        r"curl\s+.*\s+-d\s+",           # POST data to external URL
        r"wget\s+--post-data",
        r"nc\s+.*\s+\d{1,5}\s*<",      # netcat piping data
        r"python\s+-c\s+.*socket",      # python socket exfil
        r"base64.*\|\s*curl",           # encoded data to external URL
    ],
    "privilege_escalation": [
        r"\bsudo\b",
        r"chmod\s+[0-9]*7[0-9]*",       # world-writable permissions
        r"chown\s+root",
        r"passwd\s+root",
        r"/etc/sudoers",
        r"visudo",
    ],
    "crypto_mining": [
        r"xmrig",
        r"stratum\+tcp://",
        r"cryptonight",
        r"minerd\s+-o",
        r"cpuminer",
    ]
}
```

---

### 2.3 PII Detection & Redaction

PII detection uses compiled regular expressions with context windows to minimize false positives.

#### PII Pattern Coverage

| Category | Identifiers | Standard |
|----------|------------|---------|
| US Tax | SSN (XXX-XX-XXXX, XXXXXXXXX) | IRS |
| UK Identity | National Insurance (XX 99 99 99 X) | HMRC |
| Financial | IBAN (all 34 country formats), all major credit card types (Visa/MC/Amex/Discover/JCB/UnionPay) | ISO 13616, Luhn validation |
| HIPAA Safe Harbor | MRN, NPI, DOB, email, IP, phone, full address, passport, fax, URL, account numbers, certificate/license numbers, VINs, device identifiers, biometric identifiers, photos | 45 CFR §164.514(b) |
| GDPR Article 9 | Health data, genetic data, biometric data, racial/ethnic origin, political opinions, religious beliefs, sexual orientation, trade union membership | GDPR Art. 9 |

```python
# app/guardrails/pii.py
PII_PATTERNS = {
    "SSN": re.compile(
        r"\b(?!000|666|9\d\d)\d{3}[-\s]?(?!00)\d{2}[-\s]?(?!0000)\d{4}\b"
    ),
    "CREDIT_CARD": re.compile(
        r"\b(?:4[0-9]{12}(?:[0-9]{3})?|"       # Visa
        r"5[1-5][0-9]{14}|"                      # Mastercard
        r"3[47][0-9]{13}|"                        # Amex
        r"3(?:0[0-5]|[68][0-9])[0-9]{11}|"      # Diners
        r"6(?:011|5[0-9]{2})[0-9]{12})\b"        # Discover
    ),
    "UK_NI": re.compile(
        r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b",
        re.IGNORECASE
    ),
    "IBAN": re.compile(
        r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}(?:[A-Z0-9]?){0,16}\b"
    ),
    "PHI_EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    "PHI_PHONE": re.compile(r"\b(?:\+?1[-.]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"),
    "PHI_IP": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}

def redact_pii(text: str, sensitivity: str = "standard") -> tuple[str, list[str]]:
    """Returns (redacted_text, list_of_redacted_types)"""
    redacted_types = []
    for label, pattern in PII_PATTERNS.items():
        if pattern.search(text):
            text = pattern.sub(f"[REDACTED:{label}]", text)
            redacted_types.append(label)
    return text, redacted_types
```

---

### 2.4 LLM-as-Judge

Some policies cannot be expressed as regex patterns — they require semantic understanding of context. For example: "Is this legal advice being given to a non-client?" or "Does this response reveal strategy covered by attorney-client privilege?"

```python
# app/guardrails/llm_judge.py
class LLMJudge:
    """
    Uses a fast LLM (haiku/flash) to evaluate semantic policy compliance.
    Cached by content hash to avoid re-evaluating identical content.
    """

    async def evaluate(
        self,
        content: str,
        policy: GuardrailPolicy,
        context: dict
    ) -> JudgeVerdict:

        cache_key = f"judge:{hashlib.md5((content + policy.id).encode()).hexdigest()}"
        cached = await self.redis.get(cache_key)
        if cached:
            return JudgeVerdict.model_validate_json(cached)

        prompt = self._build_prompt(content, policy, context)

        try:
            response = await self.llm.complete(
                model="claude-haiku-3-5",   # fast + cheap for guardrails
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                timeout=3.0                 # hard 3-second deadline
            )
            verdict = self._parse_verdict(response.text)

        except (TimeoutError, LLMError):
            # Fail posture depends on policy severity
            if policy.severity in ("HIGH", "CRITICAL"):
                verdict = JudgeVerdict(allowed=False, reason="LLM judge unavailable — fail closed")
            else:
                verdict = JudgeVerdict(allowed=True, reason="LLM judge unavailable — fail open")

        await self.redis.setex(cache_key, 3600, verdict.model_dump_json())
        return verdict
```

The fail-closed/fail-open decision by severity is deliberate:
- `HIGH/CRITICAL` policies (e.g., privilege detection, PHI blocking): **fail closed** — better to block a legitimate request than allow a compliance violation
- `LOW/MEDIUM` policies (e.g., tone guidelines, brand voice): **fail open** — a guardrail outage should not halt all operations

---

## 3. Compliance Frameworks

### 3.1 GDPR

AgentVerse implements GDPR compliance as first-class platform features, not as an audit-time checklist.

#### Right of Access (Article 15)

```python
# app/governance/compliance.py
async def export_tenant_data(tenant_id: UUID) -> DataExportJob:
    """
    Async Celery task — no 500-record pagination truncation.
    Exports: goals, agents, knowledge, audit events, cost records,
             stored credentials, consent records, user profiles.
    """
    job = await celery.send_task(
        "governance.export_gdpr_data",
        kwargs={"tenant_id": str(tenant_id)},
        queue="compliance"  # dedicated compliance worker, not shared
    )
    return DataExportJob(job_id=job.id, status="queued", estimated_minutes=5)
```

The async design is critical: for large tenants with millions of records, a synchronous export would time out. Celery ensures the export completes regardless of data volume, with the user polling for completion.

#### Right to Erasure (Article 17)

```python
# 26-table cascade delete (ordered to respect FK constraints)
DELETION_ORDER = [
    "goal_tool_calls", "goal_steps", "goal_events", "goals",
    "knowledge_chunks", "knowledge_documents", "knowledge_bases",
    "agent_credentials", "agent_tool_configs", "agents",
    "cost_ledger", "audit_events",
    "trigger_executions", "triggers",
    "hitl_requests", "policy_violations",
    "eval_results", "eval_runs",
    "memory_entries", "session_memories",
    "consent_records", "user_profiles",
    "tenant_roles", "user_role_assignments",
    "mcp_connections", "tenants",
]

async def erase_tenant_data(tenant_id: UUID, requested_by: UUID) -> ErasureResult:
    # Pre-condition: check for legal holds
    holds = await get_active_legal_holds(tenant_id)
    if holds:
        raise LegalHoldViolation(
            f"Cannot erase: {len(holds)} active legal hold(s) prevent deletion",
            holds=holds
        )

    # Cascade delete in FK-safe order
    deleted_counts = {}
    for table in DELETION_ORDER:
        count = await db.execute(
            text(f"DELETE FROM {table} WHERE tenant_id = :tid"),
            {"tid": tenant_id}
        )
        deleted_counts[table] = count.rowcount

    # Audit the erasure itself (to the platform audit log, not tenant's)
    await platform_audit.log("gdpr.erasure.complete", {
        "tenant_id": str(tenant_id),
        "requested_by": str(requested_by),
        "deleted_counts": deleted_counts
    })
    return ErasureResult(success=True, deleted_counts=deleted_counts)
```

#### Consent Management

```sql
CREATE TABLE consent_records (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    user_id         UUID,                    -- NULL for tenant-level consent
    purpose         TEXT NOT NULL,           -- "goal_processing", "analytics", "marketing"
    legal_basis     TEXT NOT NULL,           -- "consent", "legitimate_interest", "contract", "legal_obligation"
    granted_at      TIMESTAMPTZ NOT NULL,
    withdrawn_at    TIMESTAMPTZ,
    version         TEXT NOT NULL,           -- privacy policy version at time of consent
    ip_address      TEXT,
    user_agent      TEXT
);
```

---

### 3.2 HIPAA

HIPAA compliance in AgentVerse covers three rule areas: Privacy Rule, Security Rule, and Breach Notification Rule.

#### Business Associate Agreement Tracking

```python
@dataclass
class BAAVerification:
    """Required before any PHI processing can occur."""
    tenant_id: UUID
    baa_signed_at: datetime
    baa_version: str
    covered_entity_name: str
    contact_email: str

async def check_baa_before_phi(tenant: Tenant) -> None:
    if not tenant.baa_verified:
        raise ComplianceViolation(
            "Business Associate Agreement required before PHI processing. "
            "Contact your AgentVerse account manager to execute a BAA."
        )
```

#### PHI Access Log

Every access to data classified as PHI is logged with the 18 Safe Harbor identifiers:

```python
PHI_IDENTIFIERS = [
    "name", "geographic_data", "dates", "phone_numbers", "fax_numbers",
    "email_addresses", "ssn", "mrn", "health_plan_beneficiary_numbers",
    "account_numbers", "certificate_license_numbers", "vehicle_identifiers",
    "device_identifiers", "web_urls", "ip_addresses", "biometric_identifiers",
    "full_face_photographs", "any_other_unique_identifying_number"
]

async def log_phi_access(
    agent_id: UUID,
    patient_identifier: str,
    data_types_accessed: list[str],
    purpose: str
) -> None:
    await db.execute(
        insert(PHIAccessLog).values(
            agent_id=agent_id,
            patient_identifier_hash=sha256(patient_identifier),  # hashed for audit, not plaintext
            data_types=data_types_accessed,
            purpose=purpose,
            accessed_at=datetime.utcnow()
        )
    )
```

#### Minimum Necessary Enforcement

The Minimum Necessary principle (45 CFR §164.502(b)) requires that PHI access is limited to the minimum information needed. AgentVerse enforces this via knowledge base filtering:

```python
# When an agent with healthcare context queries the knowledge base,
# results are filtered to the fields needed for the stated purpose.
async def search_knowledge_minimum_necessary(
    query: str,
    agent_context: AgentContext,
    purpose: str,
    kb_id: UUID
) -> list[KnowledgeChunk]:
    results = await kb.search(query, kb_id)

    if agent_context.domain == "healthcare" and purpose != "treatment":
        # For non-treatment purposes, strip identifiers
        results = [strip_phi_identifiers(r, keep=["age_range", "diagnosis_category"])
                   for r in results]
    return results
```

---

### 3.3 SOC2 Type II

SOC2 Type II certification requires demonstrating that controls have been operating effectively over a period of time (typically 12 months). AgentVerse tracks certification status in the database — not as hardcoded booleans.

```sql
CREATE TABLE compliance_certifications (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID NOT NULL REFERENCES tenants(id),
    framework        TEXT NOT NULL CHECK (framework IN ('SOC2_TYPE2', 'ISO27001', 'PCI_DSS', 'FEDRAMP')),
    status           TEXT NOT NULL CHECK (status IN ('not_started', 'in_progress', 'certified', 'expired')),
    auditor_firm     TEXT,
    report_url       TEXT,                  -- secure link to audit report
    issued_at        TIMESTAMPTZ,
    expires_at       TIMESTAMPTZ,
    scope_description TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

```python
# WRONG (hardcoded) — never do this:
def is_soc2_certified(tenant: Tenant) -> bool:
    return True  # ← This is a lie and a compliance violation

# CORRECT — reads from database:
async def is_soc2_certified(tenant_id: UUID, db: AsyncSession) -> bool:
    cert = await db.scalar(
        select(ComplianceCertification)
        .where(
            ComplianceCertification.tenant_id == tenant_id,
            ComplianceCertification.framework == "SOC2_TYPE2",
            ComplianceCertification.status == "certified",
            ComplianceCertification.expires_at > func.now()
        )
    )
    return cert is not None
```

#### SOC2 Control Mapping

| SOC2 Trust Criterion | AgentVerse Implementation |
|---------------------|--------------------------|
| CC6.1 (Logical access) | RBAC + ABAC, scope enforcement on every endpoint |
| CC6.2 (Authentication) | MFA via SSO, API key entropy, JWT short TTL |
| CC6.3 (Authorization removal) | SCIM de-provisioning, role cache invalidation |
| CC7.1 (System monitoring) | Prometheus + Grafana, Jaeger tracing, structured logs |
| CC7.2 (Anomaly detection) | Rate limiting violations, unusual access patterns |
| CC9.2 (Vendor management) | MCP connector credential vault, BAA tracking |
| A1.1 (Availability) | Health endpoint, circuit breakers, retry strategies |
| PI1.1 (Processing integrity) | Audit event chain, goal state machine |
| C1.1 (Confidentiality) | RLS at DB layer, PII redaction in guardrails |
| P1.1 (Privacy) | GDPR right-of-access/erasure, consent records |

---

### 3.4 Legal Holds

Legal holds prevent data deletion during litigation or regulatory investigation. They are implemented as first-class entities that block deletion operations at the API level.

```sql
CREATE TABLE legal_holds (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    reason          TEXT NOT NULL,              -- "Litigation: Smith v Acme Corp"
    scope           TEXT NOT NULL DEFAULT 'all', -- 'all', 'goals', 'knowledge', 'audit'
    expires_at      TIMESTAMPTZ,                -- NULL = indefinite
    created_by      UUID NOT NULL REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    released_at     TIMESTAMPTZ,
    released_by     UUID REFERENCES users(id)
);

CREATE INDEX legal_holds_tenant_active_idx ON legal_holds(tenant_id)
    WHERE released_at IS NULL
    AND (expires_at IS NULL OR expires_at > NOW());
```

The check is lightweight — Redis-cached for O(1) lookup — and is called from every deletion endpoint:

```python
# app/governance/holds.py
async def is_under_legal_hold(
    tenant_id: UUID,
    scope: str,
    redis: Redis,
    db: AsyncSession
) -> bool:
    cache_key = f"legalhold:{tenant_id}:{scope}"
    cached = await redis.get(cache_key)
    if cached is not None:
        return cached == "1"

    hold = await db.scalar(
        select(LegalHold).where(
            LegalHold.tenant_id == tenant_id,
            LegalHold.released_at.is_(None),
            or_(LegalHold.scope == "all", LegalHold.scope == scope),
            or_(LegalHold.expires_at.is_(None), LegalHold.expires_at > func.now())
        ).limit(1)
    )

    result = "1" if hold else "0"
    await redis.setex(cache_key, 60, result)  # short TTL: holds can be added
    return hold is not None

# Applied in every deletion router:
@router.delete("/knowledge/{doc_id}")
async def delete_document(doc_id: UUID, auth: AuthContext = Depends()):
    if await is_under_legal_hold(auth.tenant_id, "knowledge", redis, db):
        raise HTTPException(
            status_code=409,
            detail="Cannot delete: tenant is under a legal hold. Contact legal@agentverse.example.com"
        )
    await knowledge_store.delete(doc_id)
```

---

## 4. Encryption & Key Management

### 4.1 Vault Service

The Vault service is the encrypted credential store for MCP connector secrets and agent private keys. No secret is stored in plaintext in the database — only `vault://` URI references.

```
vault://acme-corp/github-connector/oauth_token
         ▲           ▲                ▲
      tenant       connector         key name
```

```python
# app/providers/vault.py
class VaultService:
    """
    Wraps encrypted storage for connector credentials.
    Per-tenant encryption keys derived from master key via HKDF.
    """

    def __init__(self, master_key: bytes):
        self.master_key = master_key

    def _derive_tenant_key(self, tenant_id: str) -> bytes:
        """HKDF-SHA256 key derivation per tenant."""
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=f"agentverse:tenant:{tenant_id}".encode()
        ).derive(self.master_key)

    async def store(self, uri: str, secret: str, tenant_id: str) -> None:
        key = self.derive_tenant_key(tenant_id)
        fernet = Fernet(base64.urlsafe_b64encode(key))
        encrypted = fernet.encrypt(secret.encode())
        await db.upsert_vault_entry(uri=uri, encrypted_value=encrypted)

    async def retrieve(self, uri: str, tenant_id: str) -> str:
        key = self.derive_tenant_key(tenant_id)
        fernet = Fernet(base64.urlsafe_b64encode(key))
        encrypted = await db.get_vault_entry(uri)
        return fernet.decrypt(encrypted).decode()
```

Key rotation is performed without downtime by maintaining two generations of encrypted values during the rotation window. Reads try the new key first, fall back to the old key, and re-encrypt with the new key on successful fallback.

### 4.2 Transport Security

```
Production TLS termination flow:

Internet → [Nginx TLS 1.3] → [PgBouncer :6432] → PostgreSQL
                            → [FastAPI :8000]
                            → [Redis :6379 TLS]
                            → [MinIO :9000 TLS]
```

**Nginx configuration key points:**
```nginx
ssl_protocols TLSv1.2 TLSv1.3;
ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:...;
ssl_prefer_server_ciphers off;     # client decides cipher order (TLS 1.3)
ssl_session_timeout 1d;
ssl_session_cache shared:SSL:10m;
ssl_stapling on;
ssl_stapling_verify on;

# Security headers
add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-Content-Type-Options "nosniff" always;
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; ..." always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Permissions-Policy "geolocation=(), microphone=(), camera=()" always;
```

---

## 5. Threat Model & Attack Surface

### 5.1 Threat Actors

| Actor | Motivation | Likely Attack Vector |
|-------|-----------|---------------------|
| External attacker | Data theft, ransomware | API brute force, credential stuffing |
| Compromised agent | Lateral movement | Scope escalation, tool abuse |
| Malicious tenant | Data exfiltration | RLS bypass attempt, API key sharing |
| Insider threat | Data theft, sabotage | Legitimate credentials, elevated access |
| Supply chain | Code execution | Compromised MCP connector, dependency confusion |
| Adversarial user | Prompt injection | Crafted goals, knowledge base poisoning |

### 5.2 Defense Map

```
THREAT                    → DEFENSE
─────────────────────────────────────────────────────────────────────
Prompt injection          → 6-layer guardrails + LLM judge
API key theft             → SHA-256 hashing (plaintext never stored)
JWT theft                 → 15-min TTL + RS256 (no symmetric HMAC)
Privilege escalation      → Scope checks on every endpoint, ABAC
RLS bypass                → app.tenant_id GUC + SET LOCAL isolation
Cross-tenant data access  → RLS + tenant_id FK on every table
Insider DB access         → RLS + field-level encryption for PII
DDoS/abuse                → Rate limiting (Redis sliding window)
Credential exposure       → Vault (Fernet encryption), never plaintext
Replay attacks            → JWT jti tracking, SAML InResponseTo
Supply chain via MCP      → Connector sandboxing, tool scope limits
Data persistence post-delete → Legal hold checks + 26-table cascade
```

---

## 6. Incident Response & Audit

### 6.1 Immutable Audit Trail

Every significant event generates an immutable audit record. The audit log uses a cryptographic chain where each record hashes the previous record's ID, making retrospective tampering detectable.

```sql
CREATE TABLE audit_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    event_type      TEXT NOT NULL,          -- "goal.submitted", "rbac.access_denied"
    actor_type      TEXT NOT NULL,          -- "user", "agent", "system"
    actor_id        UUID,
    resource_type   TEXT,
    resource_id     UUID,
    metadata        JSONB,
    ip_address      INET,
    prev_hash       TEXT,                   -- SHA-256 of previous event
    event_hash      TEXT NOT NULL,          -- SHA-256 of this event's content
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
) PARTITION BY RANGE (created_at);

-- Monthly partitions for performance at scale
CREATE TABLE audit_events_2024_01 PARTITION OF audit_events
    FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
```

### 6.2 SIEM Integration

Audit events are forwarded to external SIEM systems in real time. Supported sinks:

| SIEM | Format | Protocol |
|------|--------|---------|
| Splunk | HEC JSON | HTTPS POST |
| Azure Sentinel | DCR Logs Ingestion | HTTPS POST |
| AWS CloudWatch | PutLogEvents | boto3 |
| Elastic / OpenSearch | Bulk Index | HTTPS POST |
| Generic Webhook | JSON | HTTPS POST |

### 6.3 30-Day Minimum Retention

Audit events are retained for a minimum of 30 days before any archival or deletion. Enterprise plans can configure longer retention periods (1 year, 7 years for financial/legal compliance).

---

## 7. Security Hardening Checklist

### Production Deployment Checklist

```
AUTHENTICATION
[ ] API keys >= 256-bit entropy
[ ] JWT TTL <= 15 minutes
[ ] JWKS cache TTL = 10 minutes with immediate revocation on breach
[ ] SSO configured with MFA required
[ ] SCIM connected for automated de-provisioning

AUTHORIZATION
[ ] RLS enabled on all 67+ tables
[ ] Every endpoint has require_scope() dependency
[ ] ABAC conditions configured for sensitive resources
[ ] IP allowlists configured for enterprise tenants
[ ] Agent scopes follow least privilege

GUARDRAILS
[ ] All 6 layers enabled (not in bypass mode)
[ ] Custom tenant patterns loaded
[ ] LLM judge configured with fast model
[ ] PII redaction tested with HIPAA PHI set
[ ] System prompt leak detection tested

ENCRYPTION
[ ] Vault master key in HSM or Secrets Manager (NOT in .env)
[ ] TLS 1.2+ enforced (no TLS 1.0/1.1)
[ ] HSTS headers present
[ ] All Redis traffic TLS-encrypted
[ ] Database connection TLS-required

COMPLIANCE
[ ] GDPR consent records schema migrated
[ ] BAA signed and recorded in DB (healthcare tenants)
[ ] SOC2 controls documented
[ ] Legal hold infrastructure tested
[ ] Audit retention policy configured

OPERATIONS
[ ] Rate limiting configured per-plan
[ ] Security headers verified with securityheaders.com
[ ] Dependency vulnerability scan (pip audit, npm audit)
[ ] Secret scanning in CI (no plaintext secrets in code)
[ ] Incident response runbook documented
```

---

*This document is part of the AgentVerse OS architecture reference suite. For implementation details, refer to `app/auth/`, `app/guardrails/`, `app/governance/`, and `app/tenancy/` in the backend codebase.*
