# Agent Identity — World-Class Specification

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Give every agent a cryptographic identity — verifiable, revocable, auditable — enabling attribution, delegation, and trust at millions-of-agents scale. Extensible to any domain (legal, healthcare, finance, education).

**Architecture:** RS256 JWT service-account tokens per agent, JWKS endpoint, Redis-backed credential cache, agent_credentials DB table (migration 0053), domain identity metadata in JSONB. Fix SSO ghost key, fix default admin role.

**Tech Stack:** Python 3.12 · FastAPI · python-jose · cryptography · Redis · SQLAlchemy · React 19 · TypeScript

---

## 1. Vision

Every agent in AgentVerse must have a verifiable, unforgeable identity. When an agent calls Jira, Salesforce, or a proprietary hospital EMR, the receiving system must know: **which agent made this call, on whose behalf, with what permissions, and when**. This enables:

- **Attribution**: forensic trail of exactly which agent accessed which resource
- **Delegation**: agent A spawning agent B transfers a scoped, time-limited credential
- **Revocation**: instantly invalidate a compromised agent's access across all systems
- **Domain trust**: a legal agent carries its bar association membership; a healthcare agent carries its NPI; a financial agent carries its trader ID

At millions of agents: credential lookup must be O(1) via Redis, JWT verification must be stateless (no DB lookup per request).

---

## 2. Current State Assessment

| Component | File | Status |
|-----------|------|--------|
| Agent UUID identity | `db/models/agent.py:29` | ✅ UUID hex, tenant-scoped |
| SSO tenant provisioning | `auth/keycloak.py:138-185` | ❌ `api_key_id=f"sso:{sub[:16]}"` — fake key |
| Default API key role | `services/tenant_service.py:226` | ❌ defaults to `("admin",)` — over-privileged |
| Agent credentials | (none) | ❌ Agents have no service account credentials |
| Agent JWT tokens | (none) | ❌ No per-agent token system |
| `sync_from_db()` at startup | `services/tenant_service.py:451` | ❌ Loads ALL tenants into memory — won't scale |
| Domain identity fields | (none) | ❌ No bar number, NPI, trader ID fields |
| Cryptographic key strength | `tenant_service.py:389` | ❌ `f"av_{uuid.uuid4().hex}"` — not NIST-compliant |

---

## 3. Backend Specification

### 3.1 New DB Table — agent_credentials (migration 0053)

```python
"""Add agent_credentials table for per-agent service account keys and JWT signing."""
# File: app/db/migrations/versions/0053_agent_credentials.py
revision = "0053"
down_revision = "0052"

def upgrade():
    op.execute("""
        CREATE TABLE agent_credentials (
            id           TEXT PRIMARY KEY,
            agent_id     TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
            tenant_id    TEXT NOT NULL,
            key_type     TEXT NOT NULL DEFAULT 'service_account',
            -- key_type: 'service_account' | 'delegated_user' | 'machine_token' | 'workload_identity'
            key_id       TEXT NOT NULL UNIQUE,      -- used as JWT 'kid' header
            public_key   TEXT,                       -- PEM-encoded RSA/EC public key
            private_key_ref TEXT,                    -- vault:// reference to encrypted private key
            scopes       TEXT[] NOT NULL DEFAULT '{}',
            expires_at   TIMESTAMPTZ,
            revoked_at   TIMESTAMPTZ,
            last_used_at TIMESTAMPTZ,
            created_by   TEXT NOT NULL,
            metadata     JSONB NOT NULL DEFAULT '{}',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX ix_agent_credentials_agent ON agent_credentials(agent_id);
        CREATE INDEX ix_agent_credentials_key_id ON agent_credentials(key_id);
        CREATE INDEX ix_agent_credentials_tenant ON agent_credentials(tenant_id);
        ALTER TABLE agent_credentials ENABLE ROW LEVEL SECURITY;
        ALTER TABLE agent_credentials FORCE ROW LEVEL SECURITY;
        CREATE POLICY agent_credentials_tenant ON agent_credentials
            USING (tenant_id = current_setting('app.tenant_id', TRUE))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE));
    """)
    # Add domain identity fields to agents table
    op.execute("""
        ALTER TABLE agents
            ADD COLUMN IF NOT EXISTS domain_context TEXT NOT NULL DEFAULT 'general',
            ADD COLUMN IF NOT EXISTS domain_metadata JSONB NOT NULL DEFAULT '{}';
        -- domain_context: 'general'|'legal'|'healthcare'|'finance'|'education'|'ecommerce'|'manufacturing'
        -- domain_metadata for legal: {"bar_number":"CA123","jurisdiction":"CA","clearance":"confidential"}
        -- domain_metadata for healthcare: {"npi":"1234567890","specialty":"cardiology","dea":"AB1234567"}
        -- domain_metadata for finance: {"trader_id":"T001","desk":"equity","reg_status":"licensed"}
        -- domain_metadata for education: {"institution_id":"MIT","faculty_type":"professor","course_ids":["CS101"]}
    """)
```

### 3.2 Agent JWT Token Structure

Every agent gets RS256-signed JWTs valid for 15 minutes:

```python
# app/auth/agent_identity.py
import uuid
from datetime import datetime, timezone, timedelta
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from jose import jwt

JWT_ALGORITHM = "RS256"
JWT_EXPIRY_MINUTES = 15

def generate_agent_keypair() -> tuple[str, str]:
    """Generate RSA-2048 keypair for agent JWT signing. Returns (private_pem, public_pem)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem

def issue_agent_token(
    agent_id: str,
    tenant_id: str,
    key_id: str,
    private_key_pem: str,
    scopes: list[str],
    autonomy_mode: str,
    domain_context: str = "general",
    parent_goal_id: str | None = None,
    delegated_by: str | None = None,
    expiry_minutes: int = JWT_EXPIRY_MINUTES,
) -> str:
    """Issue a signed JWT for an agent service account."""
    now = datetime.now(timezone.utc)
    payload = {
        "iss": f"agentverse:{tenant_id}",
        "sub": f"agent:{agent_id}",
        "aud": ["agentverse-api", "mcp-tools"],
        "exp": int((now + timedelta(minutes=expiry_minutes)).timestamp()),
        "iat": int(now.timestamp()),
        "jti": uuid.uuid4().hex,
        "agent_id": agent_id,
        "tenant_id": tenant_id,
        "autonomy_mode": autonomy_mode,
        "scopes": scopes,
        "domain_context": domain_context,
    }
    if parent_goal_id:
        payload["parent_goal_id"] = parent_goal_id
    if delegated_by:
        payload["delegated_by"] = delegated_by
    return jwt.encode(payload, private_key_pem, algorithm=JWT_ALGORITHM, headers={"kid": key_id})

def verify_agent_token(token: str, public_key_pem: str, tenant_id: str) -> dict:
    """Verify agent JWT. Raises jose.JWTError on failure."""
    return jwt.decode(
        token,
        public_key_pem,
        algorithms=[JWT_ALGORITHM],
        audience="agentverse-api",
        issuer=f"agentverse:{tenant_id}",
    )
```

### 3.3 JWKS Endpoint

```python
# In app/api/system.py (add alongside /health and /metrics):
@router.get("/.well-known/jwks.json")
async def jwks(request: Request) -> dict:
    """Public key set for JWT verification by external systems."""
    # Read all active public keys from agent_credentials
    # Cache in Redis for 10 minutes
    keys = await _build_jwks(request.app.state.goal_service._db)
    return {"keys": keys}

# Cache invalidation: when a credential is revoked, publish to Redis channel jwks_invalidated
```

### 3.4 API Endpoints — Agent Credentials

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/agents/{id}/credentials` | `agents:read` | List credentials (no private key) |
| `POST` | `/agents/{id}/credentials` | `agents:admin` | Issue new service account key |
| `DELETE` | `/agents/{id}/credentials/{key_id}` | `agents:admin` | Revoke credential (immediate) |
| `POST` | `/agents/{id}/token` | credential key | Exchange service key for 15-min JWT |
| `GET` | `/.well-known/jwks.json` | public | JWKS for JWT verification |
| `GET` | `/.well-known/agent-card.json` | public | A2A agent discovery card |

**POST /agents/{id}/credentials — Request:**
```json
{
  "key_type": "service_account",
  "scopes": ["goals:execute", "knowledge:read", "tools:jira.*"],
  "expires_in_days": 90,
  "description": "CI/CD pipeline agent key"
}
```

**Response:**
```json
{
  "key_id": "kid_abc123",
  "private_key_pem": "-----BEGIN PRIVATE KEY-----\n...",
  "public_key_pem": "-----BEGIN PUBLIC KEY-----\n...",
  "scopes": ["goals:execute", "knowledge:read", "tools:jira.*"],
  "expires_at": "2025-09-27T00:00:00Z",
  "warning": "Private key shown ONCE — save it securely."
}
```

**Error responses:**
- `401 Unauthorized` — no API key
- `403 Forbidden` — tenant doesn't own this agent, or insufficient scope
- `404 Not Found` — agent not found
- `409 Conflict` — agent already has max credentials (10 per agent)
- `422 Unprocessable` — invalid scopes or key_type

### 3.5 Fix: SSO Ghost Key

**File: `app/auth/keycloak.py`**, in `resolve_tenant_from_jwt()`:

```python
# After creating or fetching tenant:
# BEFORE (broken — fake api_key_id):
# return TenantContext(tenant_id=tenant_id, plan=plan, api_key_id=f"sso:{sub[:16]}")

# AFTER (correct — real DB key record):
import secrets, hashlib
sso_key_id = f"sso_{hashlib.sha256(sub.encode()).hexdigest()[:16]}"
# Check if SSO key record already exists
existing_key = await tenant_service.get_key_by_sso_sub(sso_sub=sub, tenant_id=tenant_id)
if existing_key is None:
    # Create a real API key record with SSO source
    raw_key = f"av_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await tenant_service.create_key(
        tenant_id=tenant_id,
        key_hash=key_hash,
        name=f"SSO:{email}",
        source="sso",
        sso_sub=sub,
        roles=["operator"],  # SSO users default to operator, NOT admin
    )
    existing_key = await tenant_service.get_key_by_sso_sub(sso_sub=sub, tenant_id=tenant_id)
return TenantContext(tenant_id=tenant_id, plan=plan, api_key_id=existing_key["key_id"])
```

### 3.6 Fix: Default Role — admin → operator

**File: `app/services/tenant_service.py` line 226:**
```python
# BEFORE:
key_roles = tuple(key.get("roles", ("admin",))) or ("admin",)
# AFTER:
key_roles = tuple(key.get("roles", ("operator",))) or ("operator",)
```

### 3.7 Fix: Cryptographic API Keys

**File: `app/services/tenant_service.py` line 389:**
```python
# BEFORE (UUID — predictable, only 122 bits entropy):
api_key = f"av_{uuid.uuid4().hex}"

# AFTER (NIST-compliant — 256 bits from os.urandom):
import secrets
raw_key = f"av_{secrets.token_urlsafe(32)}"  # 256 bits
key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
# Store key_hash in DB, return raw_key ONCE to caller
```

### 3.8 Fix: sync_from_db — Redis-Backed LRU Cache

**File: `app/services/tenant_service.py`:**
```python
async def resolve_api_key(self, raw_key: str) -> TenantContext | None:
    """Resolve API key with Redis L1 cache (5-min TTL) + in-memory L2."""
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    cache_key = f"api_key:{key_hash}"
    
    # L1: Redis cache
    if self._redis:
        cached = await self._redis.get(cache_key)
        if cached:
            data = json.loads(cached)
            return TenantContext(**data)
    
    # L2: DB lookup
    ctx = await self._db_resolve_key(key_hash)
    if ctx and self._redis:
        await self._redis.setex(cache_key, 300, ctx.to_json())  # 5-min TTL
    return ctx

# REMOVE: startup sync_from_db — no longer loading all tenants into memory
# ADD: background cache warming for recently-active tenants only
```

### 3.9 Domain Identity Validation

```python
# app/api/agents.py — when creating/updating agents with domain_context:
DOMAIN_METADATA_SCHEMAS = {
    "legal": {
        "type": "object",
        "properties": {
            "bar_number": {"type": "string"},
            "jurisdiction": {"type": "string"},
            "clearance_level": {"enum": ["public", "confidential", "restricted"]},
        }
    },
    "healthcare": {
        "type": "object",
        "properties": {
            "npi": {"type": "string", "pattern": "^\\d{10}$"},
            "specialty": {"type": "string"},
            "dea": {"type": "string"},
        }
    },
    "finance": {
        "type": "object",
        "properties": {
            "trader_id": {"type": "string"},
            "desk": {"type": "string"},
            "reg_status": {"enum": ["licensed", "trainee", "restricted"]},
        }
    },
}
```

### 3.10 main.py Wiring

Add to `create_app()` after existing service binding:
```python
# Identity service (JWKS, credential management)
from app.auth.agent_identity import AgentIdentityService
app.state.agent_identity_service = AgentIdentityService(
    db=None,  # upgraded in lifespan
    vault=get_vault(),
    redis=_fake_redis,
)
```

Wire in lifespan: `app.state.agent_identity_service.set_db(db_factory); app.state.agent_identity_service.set_redis(real_redis)`

---

## 4. Frontend Specification

### 4.1 Agent Identity Center at /agents/:id/identity

```typescript
// New tab in AgentDetailPage OR standalone page
// Route: /agents/:agentId/identity

export function AgentIdentityPage() {
  // Sections:
  // 1. Credential Cards (service account keys)
  // 2. Issue New Credential modal
  // 3. JWT Preview (decode current token)
  // 4. Domain Identity fields
  // 5. Revocation history
}
```

**Credential Card:**
```typescript
interface CredentialCard {
  keyId: string;
  keyType: "service_account" | "delegated_user" | "machine_token";
  scopes: string[];
  expiresAt: string | null;
  lastUsedAt: string | null;
  revokedAt: string | null;
}
// Display:
// - Key ID (truncated + copy button)
// - Type badge
// - Scope chips (first 3, then "+N more")
// - Expires countdown: "Expires in 87 days" / "EXPIRED" / "Never"
// - Last used: "2 hours ago" or "Never used"
// - Revoke button (ConfirmModal, variant="danger")
```

**Issue New Credential modal:**
- Key type selector (4 options with descriptions)
- Scope tree: hierarchical checkbox tree grouped by resource
- Expiry: preset buttons (30d / 90d / 1y / Never) + custom date
- Description input
- Submit → shows private key **once** with copy button + warning banner

**JWT Preview panel:**
```typescript
// Shows decoded JWT for the selected credential
// Updates in real-time with expiry countdown
// Fields highlighted: agent_id, scopes, exp, domain_context
// Copy raw token button
// "Test this token" button → calls GET /auth/me with the token
```

**Domain Identity section:**
Dynamic form based on `agent.domain_context`:
- General: no extra fields
- Legal: Bar Number, Jurisdiction (state dropdown), Clearance Level
- Healthcare: NPI (10-digit validated), Specialty, DEA Number
- Finance: Trader ID, Desk, Regulatory Status
- Education: Institution, Faculty Type, Course IDs (multi-value)

### 4.2 Domain Context Selector in AgentCreatePage

```typescript
const DOMAIN_OPTIONS = [
  { value: "general",        label: "General Purpose",  icon: "⚡", description: "No domain-specific restrictions" },
  { value: "legal",          label: "Legal",            icon: "⚖️", description: "Attorney-client privilege, court filing rules" },
  { value: "healthcare",     label: "Healthcare",       icon: "🏥", description: "HIPAA PHI handling, clinical workflows" },
  { value: "finance",        label: "Finance",          icon: "📊", description: "Trading rules, SOX compliance, PCI" },
  { value: "education",      label: "Education",        icon: "🎓", description: "FERPA student data, academic integrity" },
  { value: "ecommerce",      label: "E-Commerce",       icon: "🛒", description: "PCI DSS, customer data protection" },
  { value: "manufacturing",  label: "Manufacturing",    icon: "🏭", description: "Safety protocols, ISO standards" },
];
// Animated card grid selection; selected card scales up + ring
```

### 4.3 TypeScript Interfaces (add to client.ts)

```typescript
export interface AgentCredential {
  id: string;
  agent_id: string;
  key_id: string;
  key_type: "service_account" | "delegated_user" | "machine_token" | "workload_identity";
  scopes: string[];
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
  created_by: string;
  created_at: string;
  description?: string;
}

export interface IssueCredentialRequest {
  key_type: AgentCredential["key_type"];
  scopes: string[];
  expires_in_days?: number;
  description?: string;
}

export interface IssuedCredential extends AgentCredential {
  private_key_pem: string;  // only in create response
  warning: string;
}

// Add to agentsApi:
// listCredentials: (agentId) => request<AgentCredential[]>(`/agents/${agentId}/credentials`),
// issueCredential: (agentId, req) => request<IssuedCredential>(`/agents/${agentId}/credentials`, {method:"POST",...}),
// revokeCredential: (agentId, keyId) => request<void>(`/agents/${agentId}/credentials/${keyId}`, {method:"DELETE"}),
// getAgentToken: (agentId, keyId) => request<{token:string;expires_at:string}>(`/agents/${agentId}/token`, {method:"POST",...}),
```

### 4.4 Animations

**Credential issue entrance:**
```css
@keyframes credentialIssue {
  0%   { transform: scale(0.8) translateY(10px); opacity: 0; }
  70%  { transform: scale(1.05) translateY(-2px); opacity: 0.9; }
  100% { transform: scale(1) translateY(0); opacity: 1; }
}
.credential-card-entering { animation: credentialIssue 350ms cubic-bezier(0.34,1.56,0.64,1) forwards; }
```

**Revoke animation:**
```css
@keyframes credentialRevoke {
  0%   { opacity: 1; transform: scale(1); }
  30%  { background: rgba(239,68,68,0.1); }
  100% { opacity: 0.4; transform: scale(0.97); filter: grayscale(1); }
}
.credential-card-revoked { animation: credentialRevoke 500ms ease-out forwards; }
```

**JWT expiry countdown:** `requestAnimationFrame` countdown from `exp - Date.now()/1000`, displays `MM:SS` in red when < 60 seconds.

**Domain selection pulse:** selected domain card pulses with `box-shadow: 0 0 0 3px hsl(var(--primary))` once (300ms).

---

## 5. Scale Architecture

| Component | Solution | Performance |
|-----------|----------|-------------|
| API key lookup | Redis hash `api_key:{hash}` 5-min TTL | < 1ms O(1) |
| JWT verification | Stateless RS256 (no DB lookup) | < 2ms cryptographic |
| JWKS endpoint | Redis cached 10-min, pub/sub invalidation | < 5ms cached |
| Credential list | DB query + Redis cache per agent 60s | < 10ms |
| Key revocation | Redis immediate + async DB write | < 5ms |

**At millions of agents:** JWT verification is always stateless. Redis cache eliminates DB lookup for 99.9% of requests. Only cold cache misses hit DB.

---

## 6. Testing Strategy

```python
# tests/auth/test_agent_identity.py
def test_issue_credential_generates_valid_jwt():
    private_pem, public_pem = generate_agent_keypair()
    token = issue_agent_token(
        agent_id="agent-123", tenant_id="t1", key_id="kid1",
        private_key_pem=private_pem, scopes=["goals:execute"], autonomy_mode="bounded-autonomous"
    )
    claims = verify_agent_token(token, public_pem, tenant_id="t1")
    assert claims["sub"] == "agent:agent-123"
    assert "goals:execute" in claims["scopes"]

def test_revoked_credential_cannot_issue_tokens():
    # Issue → revoke → attempt token → assert 401

def test_sso_creates_real_db_key_record():
    # Mock SSO callback → assert api_keys table has row with source='sso'
    # Assert api_key_id is NOT "sso:{sub[:16]}" ghost key

def test_domain_metadata_validates_for_legal():
    # Create legal agent without bar_number → assert 422
    # Create legal agent with bar_number → assert 201

def test_redis_cache_avoids_db_on_second_lookup():
    # First lookup: hits DB (mock DB called once)
    # Second lookup: hits Redis (mock DB NOT called again)

def test_default_role_is_operator_not_admin():
    # Create API key → assert roles == ["operator"]
    # Assert admin scope check fails for new key

def test_cryptographic_key_passes_entropy_check():
    from collections import Counter
    key = generate_api_key()
    # Test that key has high entropy (not UUID-based)
    assert len(key) >= 44  # base64url of 32 bytes
    assert key.startswith("av_")

def test_sync_from_db_replaced_with_redis_cache():
    # Mock Redis, call resolve_api_key twice
    # Assert DB only called once (cache hit on second call)
```

---

## 7. Domain Extensibility Framework

The `domain_context` + `domain_metadata` JSONB pattern allows any domain to add identity attributes without schema migrations:

```python
# Adding a new domain requires ONLY:
# 1. Add to DOMAIN_METADATA_SCHEMAS dict (validation)
# 2. Add to DOMAIN_OPTIONS frontend array (UI)
# 3. (Optional) Add domain-specific guardrail rules

# Example: Adding "government" domain:
DOMAIN_METADATA_SCHEMAS["government"] = {
    "type": "object",
    "properties": {
        "clearance_level": {"enum": ["public", "secret", "top_secret"]},
        "agency_code": {"type": "string"},
        "position_id": {"type": "string"},
    }
}
```

All domain identity attributes are queryable via `metadata->>'bar_number'` in PostgreSQL, indexed via `GIN(domain_metadata)`.
