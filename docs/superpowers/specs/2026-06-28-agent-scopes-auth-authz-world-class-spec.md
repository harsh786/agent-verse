# Agent Scopes, Authentication & Authorization — World-Class Specification

**Area 2 · Migration 0054 · Version 1.0 · 2026-06-28**

---

## 1. Vision

AgentVerse is designed to serve organizations ranging from solo developers to Fortune 500 enterprises operating in regulated industries. The current authentication and authorization subsystem, while functional for simple single-tenant scenarios, contains several critical deficiencies that will block enterprise adoption: scopes are defined but never enforced at middleware level, the IP allowlist schema exists purely as database data with zero enforcement logic, every request triggers a fresh role lookup from Postgres adding 5–15 ms of latency at scale, the default tenant role is `admin` creating an over-privileged blast radius for any compromise, and the flat 4-role hierarchy (`admin`, `developer`, `viewer`, `agent`) cannot model the complex organizational structures found in law firms, hospital networks, financial institutions, or educational consortia.

This specification replaces the ad-hoc permission checks scattered across 25+ route handlers with a single, coherent Attribute-Based Access Control (ABAC) engine that enforces scopes at the middleware boundary, caches resolved permissions in Redis with a 5-minute TTL and an LRU eviction cap of 50,000 entries, validates source IPs against per-tenant allowlists before any authentication processing, supports arbitrary role hierarchies with inheritance chains, and ships domain-specific role templates so a healthcare network can deploy HIPAA-compliant `phi_reader`, `prescribing_physician`, and `care_coordinator` roles in minutes rather than months. At one million tenants with 10 API calls per second per active tenant, the Redis role cache eliminates 99.7% of Postgres role lookups, keeping p99 auth overhead below 2 ms.

---

## 2. Current State Assessment

| Component | Current State | Gap | Severity |
|-----------|---------------|-----|----------|
| Scope enforcement | Scopes in `api_keys.scopes` column, never read by middleware | Zero enforcement — any valid key accesses all endpoints | CRITICAL |
| IP allowlist | `tenant_settings.ip_allowlist` JSON column | Data stored, never checked in request path | CRITICAL |
| Role cache | None — hits Postgres on every request | 5–15 ms per request; O(n) DB load at scale | HIGH |
| Default role | `admin` in tenant creation flow | Over-privileged blast radius on key compromise | HIGH |
| Role hierarchy | 4 flat roles hardcoded in Enum | Cannot model orgs with >4 permission levels | HIGH |
| Custom roles | Not supported | Enterprise adoption blocker | HIGH |
| ABAC conditions | Not supported | Cannot express "can read goals created by own dept" | MEDIUM |
| Scope explorer UI | None | Developers cannot discover what scopes their key has | MEDIUM |
| Domain role templates | None | Healthcare/legal/finance must build from scratch | MEDIUM |
| Token rotation | Not implemented | Long-lived tokens cannot be cycled without downtime | MEDIUM |
| Auth failure audit | Partial | Auth failures not in append-only audit trail | MEDIUM |
| Permission inheritance | Not implemented | Role A cannot extend Role B's permissions | LOW |

---

## 3. Backend Architecture

### 3.1 Database Schema — Migration 0054

```sql
-- =============================================================================
-- Migration 0054: Custom roles, role assignments, API key scopes, IP allowlist
-- Author: AgentVerse Platform Team
-- Date: 2026-06-28
-- =============================================================================

BEGIN;

-- --------------------------------------------------------
-- Table: custom_roles
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS custom_roles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    display_name    TEXT NOT NULL,
    description     TEXT,
    parent_role_id  UUID REFERENCES custom_roles(id) ON DELETE SET NULL,
    system_role     TEXT,
    permissions     JSONB NOT NULL DEFAULT '[]'::jsonb,
    conditions      JSONB NOT NULL DEFAULT '{}'::jsonb,
    domain          TEXT,
    is_template     BOOLEAN NOT NULL DEFAULT FALSE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ,
    CONSTRAINT uq_custom_role_tenant_name UNIQUE (tenant_id, name),
    CONSTRAINT chk_custom_role_name CHECK (name ~ '^[a-z][a-z0-9_]{1,62}[a-z0-9]$')
);

CREATE INDEX idx_custom_roles_tenant
    ON custom_roles(tenant_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_custom_roles_domain
    ON custom_roles(domain) WHERE is_template = TRUE;
CREATE INDEX idx_custom_roles_parent
    ON custom_roles(parent_role_id) WHERE parent_role_id IS NOT NULL;

ALTER TABLE custom_roles ENABLE ROW LEVEL SECURITY;
CREATE POLICY custom_roles_isolation ON custom_roles
    USING (
        tenant_id = current_setting('app.tenant_id', TRUE)::uuid
        OR is_template = TRUE
    );

-- --------------------------------------------------------
-- Table: role_assignments
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS role_assignments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id         UUID REFERENCES custom_roles(id) ON DELETE CASCADE,
    system_role     TEXT,
    resource_type   TEXT,
    resource_id     UUID,
    conditions      JSONB DEFAULT '{}'::jsonb,
    granted_by      UUID REFERENCES users(id),
    granted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ,
    revoked_by      UUID REFERENCES users(id),
    revoke_reason   TEXT,
    CONSTRAINT chk_ra_role CHECK (
        (role_id IS NOT NULL AND system_role IS NULL) OR
        (role_id IS NULL AND system_role IS NOT NULL)
    ),
    CONSTRAINT chk_ra_resource CHECK (
        (resource_type IS NULL AND resource_id IS NULL) OR
        (resource_type IS NOT NULL)
    )
);

CREATE INDEX idx_role_assignments_user
    ON role_assignments(user_id, tenant_id)
    WHERE revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now());
CREATE INDEX idx_role_assignments_resource
    ON role_assignments(resource_type, resource_id)
    WHERE revoked_at IS NULL;

ALTER TABLE role_assignments ENABLE ROW LEVEL SECURITY;
CREATE POLICY role_assignments_isolation ON role_assignments
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- --------------------------------------------------------
-- Table: api_key_scopes (normalized, replaces JSONB array)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS api_key_scopes (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    api_key_id  UUID NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    scope       TEXT NOT NULL,
    resource_id UUID,
    granted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ,
    CONSTRAINT uq_api_key_scope UNIQUE (api_key_id, scope, resource_id)
);

CREATE INDEX idx_api_key_scopes_key ON api_key_scopes(api_key_id);
CREATE INDEX idx_api_key_scopes_scope ON api_key_scopes(scope);

ALTER TABLE api_key_scopes ENABLE ROW LEVEL SECURITY;
CREATE POLICY api_key_scopes_isolation ON api_key_scopes
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- --------------------------------------------------------
-- Table: ip_allowlist_entries
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS ip_allowlist_entries (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    cidr        CIDR NOT NULL,
    label       TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_by  UUID REFERENCES users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ
);

CREATE INDEX idx_ip_allowlist_tenant
    ON ip_allowlist_entries(tenant_id) WHERE is_active = TRUE;

ALTER TABLE ip_allowlist_entries ENABLE ROW LEVEL SECURITY;
CREATE POLICY ip_allowlist_isolation ON ip_allowlist_entries
    USING (tenant_id = current_setting('app.tenant_id', TRUE)::uuid);

-- --------------------------------------------------------
-- Table: scope_definitions (canonical registry)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS scope_definitions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope       TEXT NOT NULL UNIQUE,
    resource    TEXT NOT NULL,
    action      TEXT NOT NULL,
    description TEXT NOT NULL,
    risk_level  TEXT NOT NULL DEFAULT 'low'
                CHECK (risk_level IN ('low','medium','high','critical')),
    domain      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO scope_definitions (scope, resource, action, description, risk_level) VALUES
('goals:read',          'goals',      'read',    'List and retrieve goals',                  'low'),
('goals:write',         'goals',      'write',   'Create and update goals',                  'medium'),
('goals:delete',        'goals',      'delete',  'Permanently delete goals',                 'high'),
('goals:execute',       'goals',      'execute', 'Trigger goal execution',                   'high'),
('agents:read',         'agents',     'read',    'List and retrieve agent configs',           'low'),
('agents:write',        'agents',     'write',   'Create and update agent configs',           'medium'),
('agents:delete',       'agents',     'delete',  'Delete agent configurations',              'high'),
('knowledge:read',      'knowledge',  'read',    'Query knowledge bases',                    'low'),
('knowledge:write',     'knowledge',  'write',   'Ingest documents into knowledge bases',    'medium'),
('knowledge:delete',    'knowledge',  'delete',  'Remove knowledge base documents',          'high'),
('governance:read',     'governance', 'read',    'View policies and HITL approvals',         'low'),
('governance:write',    'governance', 'write',   'Create and modify governance policies',    'high'),
('governance:approve',  'governance', 'approve', 'Approve HITL requests',                   'critical'),
('tenancy:read',        'tenancy',    'read',    'Read tenant settings and configuration',   'low'),
('tenancy:write',       'tenancy',    'write',   'Modify tenant settings',                   'critical'),
('audit:read',          'audit',      'read',    'Read audit log entries',                   'medium'),
('audit:export',        'audit',      'export',  'Export audit logs to file or SIEM',        'high'),
('costs:read',          'costs',      'read',    'View cost and token usage data',           'low'),
('costs:admin',         'costs',      'admin',   'Set budgets and alert thresholds',         'high'),
('mcp:read',            'mcp',        'read',    'List MCP connectors',                      'low'),
('mcp:write',           'mcp',        'write',   'Configure MCP connectors',                 'high')
ON CONFLICT (scope) DO NOTHING;

-- --------------------------------------------------------
-- Alter api_keys: safe default, rotation tracking
-- --------------------------------------------------------
ALTER TABLE api_keys
    ALTER COLUMN default_role SET DEFAULT 'viewer',
    ADD COLUMN IF NOT EXISTS rotated_from UUID REFERENCES api_keys(id),
    ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS use_count BIGINT NOT NULL DEFAULT 0;

-- Downgrade any existing over-privileged default keys
UPDATE api_keys SET default_role = 'viewer' WHERE default_role = 'admin';

COMMIT;
```

### 3.2 Alembic Migration File

```python
# agent-verse-backend/app/db/migrations/versions/0054_custom_roles_authz.py
"""custom_roles, role_assignments, api_key_scopes, ip_allowlist_entries, scope_definitions

Revision ID: 0054
Revises: 0053
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, CIDR, TIMESTAMPTZ

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_roles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("parent_role_id", UUID(as_uuid=True),
                  sa.ForeignKey("custom_roles.id", ondelete="SET NULL")),
        sa.Column("system_role", sa.Text()),
        sa.Column("permissions", JSONB(), nullable=False, server_default="'[]'"),
        sa.Column("conditions", JSONB(), nullable=False, server_default="'{}'"),
        sa.Column("domain", sa.Text()),
        sa.Column("is_template", sa.Boolean(), nullable=False, server_default="FALSE"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="TRUE"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", TIMESTAMPTZ()),
        sa.UniqueConstraint("tenant_id", "name", name="uq_custom_role_tenant_name"),
    )
    op.create_index("idx_custom_roles_tenant", "custom_roles", ["tenant_id"],
                    postgresql_where=sa.text("deleted_at IS NULL"))
    op.create_index("idx_custom_roles_domain", "custom_roles", ["domain"],
                    postgresql_where=sa.text("is_template = TRUE"))

    op.create_table(
        "role_assignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role_id", UUID(as_uuid=True),
                  sa.ForeignKey("custom_roles.id", ondelete="CASCADE")),
        sa.Column("system_role", sa.Text()),
        sa.Column("resource_type", sa.Text()),
        sa.Column("resource_id", UUID(as_uuid=True)),
        sa.Column("conditions", JSONB(), server_default="'{}'"),
        sa.Column("granted_by", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("granted_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", TIMESTAMPTZ()),
        sa.Column("revoked_at", TIMESTAMPTZ()),
        sa.Column("revoked_by", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("revoke_reason", sa.Text()),
    )
    op.create_index(
        "idx_role_assignments_user", "role_assignments", ["user_id", "tenant_id"],
        postgresql_where=sa.text(
            "revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())"
        ),
    )

    op.create_table(
        "api_key_scopes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("api_key_id", UUID(as_uuid=True),
                  sa.ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("resource_id", UUID(as_uuid=True)),
        sa.Column("granted_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", TIMESTAMPTZ()),
        sa.UniqueConstraint("api_key_id", "scope", "resource_id", name="uq_api_key_scope"),
    )

    op.create_table(
        "ip_allowlist_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cidr", CIDR(), nullable=False),
        sa.Column("label", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="TRUE"),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("created_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
        sa.Column("expires_at", TIMESTAMPTZ()),
    )

    op.create_table(
        "scope_definitions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("scope", sa.Text(), nullable=False, unique=True),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.Text(), nullable=False, server_default="'low'"),
        sa.Column("domain", sa.Text()),
        sa.Column("created_at", TIMESTAMPTZ(), nullable=False, server_default=sa.text("now()")),
    )

    op.alter_column("api_keys", "default_role", server_default="viewer")
    op.add_column("api_keys",
                  sa.Column("rotated_from", UUID(as_uuid=True), sa.ForeignKey("api_keys.id")))
    op.add_column("api_keys", sa.Column("last_used_at", TIMESTAMPTZ()))
    op.add_column("api_keys",
                  sa.Column("use_count", sa.BigInteger(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("api_keys", "use_count")
    op.drop_column("api_keys", "last_used_at")
    op.drop_column("api_keys", "rotated_from")
    op.drop_table("scope_definitions")
    op.drop_table("ip_allowlist_entries")
    op.drop_table("api_key_scopes")
    op.drop_table("role_assignments")
    op.drop_table("custom_roles")
```

### 3.3 API Endpoints

#### Role Management

**POST /api/auth/roles**

- Auth: scope `tenancy:write`
- Request:
```json
{
  "name": "case_manager",
  "display_name": "Case Manager",
  "description": "Manages client cases and associated goals",
  "parent_role_id": "uuid-optional",
  "permissions": ["goals:read", "goals:write", "goals:execute", "agents:read"],
  "conditions": {
    "matter_access": "assigned_only"
  },
  "domain": "legal"
}
```
- Response 201:
```json
{
  "id": "uuid",
  "tenant_id": "uuid",
  "name": "case_manager",
  "display_name": "Case Manager",
  "permissions": ["goals:read", "goals:write", "goals:execute", "agents:read"],
  "resolved_permissions": ["goals:read", "goals:write", "goals:execute", "agents:read", "knowledge:read"],
  "conditions": {"matter_access": "assigned_only"},
  "domain": "legal",
  "assignment_count": 0,
  "created_at": "2026-06-28T00:00:00Z"
}
```
- Errors: `400 ROLE_NAME_INVALID`, `400 CIRCULAR_INHERITANCE`, `409 ROLE_EXISTS`, `422 UNKNOWN_SCOPE`

**GET /api/auth/roles** — Query: `domain`, `include_templates`, `include_system`, `search`

**GET /api/auth/roles/{role_id}**

**PATCH /api/auth/roles/{role_id}** — Partial update; invalidates cache for all assignments

**DELETE /api/auth/roles/{role_id}** — Errors: `409 ROLE_HAS_ASSIGNMENTS` (pass `force=true` or `migrate_to=uuid`)

**POST /api/auth/roles/from-template**

- Body: `{ "template_id": "uuid", "overrides": { "conditions": {...} } }`
- Instantiates a domain template for this tenant
- Response 201: new role object

#### Role Assignments

**POST /api/auth/assignments**

- Auth: scope `tenancy:write`
- Request:
```json
{
  "user_id": "uuid",
  "role_id": "uuid",
  "resource_type": "agent",
  "resource_id": "uuid",
  "expires_at": "2027-01-01T00:00:00Z"
}
```
- Response 201: assignment object with user display name
- Errors: `404 USER_NOT_FOUND`, `404 ROLE_NOT_FOUND`, `409 ASSIGNMENT_EXISTS`

**GET /api/auth/assignments** — Query: `user_id`, `role_id`, `resource_type`, `include_expired`

**DELETE /api/auth/assignments/{id}** — Soft-revoke; body: `{ "reason": "..." }`

#### API Key Scopes

**GET /api/auth/keys/{key_id}/scopes**
```json
{
  "key_id": "uuid",
  "scopes": [
    { "scope": "goals:read", "resource_id": null, "expires_at": null },
    { "scope": "goals:write", "resource_id": "agent-uuid", "expires_at": "2027-01-01" }
  ],
  "effective_scopes": ["goals:read", "goals:write"],
  "missing_high_risk": ["goals:delete", "governance:approve"]
}
```

**POST /api/auth/keys/{key_id}/scopes** — Body: `{ "scopes": [...], "resource_id": null }` — Error: `422 SCOPE_EXCEEDS_CALLER_PERMISSIONS`

**DELETE /api/auth/keys/{key_id}/scopes/{scope}**

**POST /api/auth/keys/{key_id}/rotate**
```json
{ "ttl_seconds": 3600 }
```
Response:
```json
{
  "new_key": "av_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "old_key_expires_at": "2026-06-28T01:00:00Z",
  "rotated_from": "old-key-uuid"
}
```

#### IP Allowlist

**GET /api/auth/ip-allowlist** — Returns active entries for tenant

**POST /api/auth/ip-allowlist** — Body: `{ "cidr": "10.0.0.0/8", "label": "Corporate VPN" }`
- Validation: valid CIDR notation, non-broadcast
- Errors: `400 INVALID_CIDR`, `409 CIDR_OVERLAP`

**DELETE /api/auth/ip-allowlist/{entry_id}** — Soft-deactivates; invalidates cache

#### Scope Explorer

**GET /api/auth/scopes** — Full catalog with risk levels; cached in app memory

**GET /api/auth/scopes/check** — Query: `scope`, `resource_id`
```json
{ "allowed": true, "reason": "explicit_grant", "source": "api_key_scope" }
```
or:
```json
{ "allowed": false, "reason": "scope_not_granted", "missing_scopes": ["goals:read"] }
```

### 3.4 Business Logic — Python

```python
# agent-verse-backend/app/tenancy/scope_enforcement.py
"""
ScopeEnforcementMiddleware — enforces API key scopes on every request.

Pipeline (in order):
  1. Exempt path? → skip enforcement
  2. No API key? → 401
  3. IP allowlist check (Redis cache) → 403 if blocked
  4. Resolve required scope from route registry
  5. Check Redis cache for {tenant_id}:{key_id} permission set
  6. Cache miss: load from DB, store in Redis, TTL=300s
  7. Scope check → 403 with missing scope info
  8. ABAC condition evaluation (if role has conditions)
  9. Fire-and-forget: update last_used_at counter in Redis
"""
from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Optional
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.db.session import AsyncSession

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Scope registry: (HTTP method, path prefix) → required scope
# ---------------------------------------------------------------------------
SCOPE_REGISTRY: dict[tuple[str, str], str] = {
    ("GET",    "/api/goals"):              "goals:read",
    ("POST",   "/api/goals"):             "goals:write",
    ("DELETE", "/api/goals"):             "goals:delete",
    ("POST",   "/api/goals/run"):         "goals:execute",
    ("POST",   "/api/goals/batch"):       "goals:write",
    ("GET",    "/api/agents"):            "agents:read",
    ("POST",   "/api/agents"):            "agents:write",
    ("DELETE", "/api/agents"):            "agents:delete",
    ("GET",    "/api/knowledge"):         "knowledge:read",
    ("POST",   "/api/knowledge"):         "knowledge:write",
    ("DELETE", "/api/knowledge"):         "knowledge:delete",
    ("GET",    "/api/governance"):        "governance:read",
    ("POST",   "/api/governance"):        "governance:write",
    ("POST",   "/api/governance/approve"):"governance:approve",
    ("GET",    "/api/audit"):             "audit:read",
    ("POST",   "/api/audit/export"):      "audit:export",
    ("GET",    "/api/costs"):             "costs:read",
    ("POST",   "/api/costs"):             "costs:admin",
    ("GET",    "/api/tenancy"):           "tenancy:read",
    ("PATCH",  "/api/tenancy"):           "tenancy:write",
    ("POST",   "/api/tenancy"):           "tenancy:write",
    ("GET",    "/api/mcp"):               "mcp:read",
    ("POST",   "/api/mcp"):               "mcp:write",
    ("DELETE", "/api/mcp"):               "mcp:write",
}

EXEMPT_PATH_PREFIXES = frozenset({
    "/health",
    "/docs",
    "/openapi.json",
    "/api/auth/login",
    "/api/auth/token",
    "/api/auth/refresh",
    "/api/webhooks",
})


class PermissionCache:
    """
    Redis-backed permission set cache.

    Key:  perm:{tenant_id}:{key_id}
    Value: JSON list of granted scope strings
    TTL:  300 seconds (configurable)
    """

    TTL = 300
    PREFIX = "perm:"

    def __init__(self, redis: aioredis.Redis) -> None:
        self._r = redis

    def _key(self, tenant_id: str, key_id: str) -> str:
        return f"{self.PREFIX}{tenant_id}:{key_id}"

    async def get(self, tenant_id: str, key_id: str) -> Optional[set[str]]:
        raw = await self._r.get(self._key(tenant_id, key_id))
        if raw is None:
            return None
        return set(json.loads(raw))

    async def set(self, tenant_id: str, key_id: str, scopes: set[str]) -> None:
        await self._r.setex(
            self._key(tenant_id, key_id),
            self.TTL,
            json.dumps(sorted(scopes)),
        )

    async def invalidate(self, tenant_id: str, key_id: str) -> None:
        await self._r.delete(self._key(tenant_id, key_id))

    async def invalidate_tenant(self, tenant_id: str) -> None:
        """Bust all cached permissions for a tenant after role update."""
        cursor = 0
        pattern = f"{self.PREFIX}{tenant_id}:*"
        while True:
            cursor, keys = await self._r.scan(cursor, match=pattern, count=200)
            if keys:
                await self._r.delete(*keys)
            if cursor == 0:
                break


class IPAllowlistCache:
    """
    Redis hash per tenant: key=cidr string, value='1'
    TTL: 60 seconds
    """

    TTL = 60
    PREFIX = "ip_wl:"

    def __init__(self, redis: aioredis.Redis) -> None:
        self._r = redis

    def _key(self, tenant_id: str) -> str:
        return f"{self.PREFIX}{tenant_id}"

    async def get_cidrs(
        self, tenant_id: str, db: "AsyncSession"
    ) -> list[str]:
        cached = await self._r.get(self._key(tenant_id))
        if cached is not None:
            return json.loads(cached)

        from sqlalchemy import select, cast, Text
        from app.db.models.auth import IPAllowlistEntry

        result = await db.execute(
            select(cast(IPAllowlistEntry.cidr, Text))
            .where(
                IPAllowlistEntry.tenant_id == UUID(tenant_id),
                IPAllowlistEntry.is_active.is_(True),
            )
        )
        cidrs = [row[0] for row in result.fetchall()]
        await self._r.setex(self._key(tenant_id), self.TTL, json.dumps(cidrs))
        return cidrs

    async def invalidate(self, tenant_id: str) -> None:
        await self._r.delete(self._key(tenant_id))


class ABACEvaluator:
    """
    Evaluates attribute-based conditions attached to role assignments.

    Supported condition keys:
      department_match (bool): user.department == resource.department
      ownership (str): "creator" | "any" — resource.created_by == user.user_id
      time_window (dict): {start: "HH:MM", end: "HH:MM", tz: "TZ"}
      clearance_lte (str): user.clearance_level >= resource.sensitivity_level
    """

    async def evaluate(
        self,
        conditions: dict[str, Any],
        user_ctx: dict[str, Any],
        resource_ctx: dict[str, Any],
    ) -> bool:
        if not conditions:
            return True

        checks: list[bool] = []

        if conditions.get("department_match"):
            checks.append(user_ctx.get("department") == resource_ctx.get("department"))

        if "ownership" in conditions:
            if conditions["ownership"] == "creator":
                checks.append(
                    str(resource_ctx.get("created_by")) == str(user_ctx.get("user_id"))
                )

        if "time_window" in conditions:
            import zoneinfo
            from datetime import datetime
            tw = conditions["time_window"]
            tz = zoneinfo.ZoneInfo(tw.get("tz", "UTC"))
            now = datetime.now(tz)
            current = now.strftime("%H:%M")
            checks.append(tw.get("start", "00:00") <= current <= tw.get("end", "23:59"))

        return all(checks) if checks else True


class ScopeEnforcementMiddleware(BaseHTTPMiddleware):
    """
    Enforces API key scopes + IP allowlist on every non-exempt request.
    """

    def __init__(
        self,
        app: Any,
        redis: aioredis.Redis,
        db_factory: Callable,
    ) -> None:
        super().__init__(app)
        self.perm = PermissionCache(redis)
        self.ip = IPAllowlistCache(redis)
        self.abac = ABACEvaluator()
        self._db = db_factory

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # 1. Exempt paths
        if any(path.startswith(p) for p in EXEMPT_PATH_PREFIXES):
            return await call_next(request)

        # 2. Require authentication
        tenant = getattr(request.state, "tenant", None)
        api_key = getattr(request.state, "api_key", None)
        if not tenant or not api_key:
            return JSONResponse(
                status_code=401,
                content={"error": "AUTHENTICATION_REQUIRED",
                         "message": "Valid API key required"},
            )

        tenant_id = str(tenant.id)
        key_id = str(api_key.id)

        # 3. IP allowlist check
        client_ip = self._client_ip(request)
        async with self._db() as db:
            cidrs = await self.ip.get_cidrs(tenant_id, db)
        if cidrs and not self._ip_allowed(client_ip, cidrs):
            logger.warning("ip_blocked", tenant_id=tenant_id, ip=client_ip, path=path)
            return JSONResponse(
                status_code=403,
                content={
                    "error": "IP_NOT_ALLOWED",
                    "message": f"Source IP {client_ip} is not permitted for this tenant",
                },
            )

        # 4. Required scope?
        required = self._required_scope(request.method, path)
        if required is None:
            return await call_next(request)

        # 5. Resolve permissions (cache-first)
        granted = await self.perm.get(tenant_id, key_id)
        if granted is None:
            async with self._db() as db:
                granted = await self._load_from_db(db, tenant_id, key_id)
            await self.perm.set(tenant_id, key_id, granted)

        # 6. Scope check
        if required not in granted:
            logger.info("scope_denied", tenant_id=tenant_id, key_id=key_id,
                        required=required, path=path)
            return JSONResponse(
                status_code=403,
                content={
                    "error": "INSUFFICIENT_SCOPE",
                    "message": f"Operation requires scope '{required}'",
                    "required_scope": required,
                    "granted_scopes": sorted(granted),
                },
            )

        return await call_next(request)

    @staticmethod
    def _client_ip(request: Request) -> str:
        for header in ("X-Forwarded-For", "X-Real-IP"):
            val = request.headers.get(header)
            if val:
                return val.split(",")[0].strip()
        return request.client.host if request.client else "0.0.0.0"

    @staticmethod
    def _ip_allowed(client_ip: str, cidrs: list[str]) -> bool:
        try:
            addr = ipaddress.ip_address(client_ip)
        except ValueError:
            return False
        for cidr in cidrs:
            try:
                if addr in ipaddress.ip_network(cidr, strict=False):
                    return True
            except ValueError:
                continue
        return False

    @staticmethod
    def _required_scope(method: str, path: str) -> Optional[str]:
        if (method, path) in SCOPE_REGISTRY:
            return SCOPE_REGISTRY[(method, path)]
        for (m, p), scope in SCOPE_REGISTRY.items():
            if method == m and path.startswith(p):
                return scope
        return None

    @staticmethod
    async def _load_from_db(
        db: "AsyncSession", tenant_id: str, key_id: str
    ) -> set[str]:
        from sqlalchemy import select
        from app.db.models.auth import APIKeyScope, CustomRole, RoleAssignment

        # Direct key scopes
        rows = await db.execute(
            select(APIKeyScope.scope).where(
                APIKeyScope.api_key_id == UUID(key_id),
                APIKeyScope.tenant_id == UUID(tenant_id),
            )
        )
        scopes: set[str] = {r[0] for r in rows.fetchall()}

        # Role-based scopes via assignments
        role_rows = await db.execute(
            select(CustomRole.permissions)
            .join(RoleAssignment, RoleAssignment.role_id == CustomRole.id)
            .where(
                RoleAssignment.tenant_id == UUID(tenant_id),
                RoleAssignment.revoked_at.is_(None),
            )
        )
        for (perms,) in role_rows.fetchall():
            if perms:
                scopes.update(perms)

        return scopes


# ---------------------------------------------------------------------------
# Role resolver — full permission set including inheritance chain
# ---------------------------------------------------------------------------

class RoleResolver:
    """Resolve permissions for a role, traversing the parent chain."""

    async def resolve(
        self,
        role_id: UUID,
        db: "AsyncSession",
        _visited: Optional[set[UUID]] = None,
    ) -> set[str]:
        if _visited is None:
            _visited = set()
        if role_id in _visited:
            return set()  # cycle guard
        _visited.add(role_id)

        from sqlalchemy import select
        from app.db.models.auth import CustomRole

        row = await db.execute(
            select(CustomRole).where(
                CustomRole.id == role_id, CustomRole.is_active.is_(True)
            )
        )
        role = row.scalar_one_or_none()
        if not role:
            return set()

        perms: set[str] = set(role.permissions or [])
        if role.parent_role_id:
            parent = await self.resolve(role.parent_role_id, db, _visited)
            perms |= parent
        return perms
```

### 3.5 Domain Role Templates

```python
# agent-verse-backend/app/tenancy/domain_role_templates.py
"""
Pre-built role definitions for regulated domains.
Instantiated via POST /api/auth/roles/from-template.
"""
from typing import Any

DOMAIN_ROLE_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "healthcare": [
        {
            "name": "phi_reader",
            "display_name": "PHI Reader",
            "description": "Read-only access to patient health information goals",
            "permissions": ["goals:read", "knowledge:read"],
            "conditions": {"data_classification_lte": "PHI"},
        },
        {
            "name": "prescribing_physician",
            "display_name": "Prescribing Physician",
            "description": "Full clinical goal access with HITL authority",
            "permissions": [
                "goals:read", "goals:write", "goals:execute",
                "governance:approve", "knowledge:read",
            ],
            "conditions": {"license_verification": "required"},
        },
        {
            "name": "care_coordinator",
            "display_name": "Care Coordinator",
            "description": "Manages care plan goals across assigned patient roster",
            "permissions": ["goals:read", "goals:write", "agents:read"],
            "conditions": {"patient_roster": "assigned_only"},
        },
        {
            "name": "hipaa_compliance_officer",
            "display_name": "HIPAA Compliance Officer",
            "description": "Audit access and policy governance for HIPAA compliance",
            "permissions": [
                "audit:read", "audit:export",
                "governance:read", "governance:write",
            ],
        },
    ],
    "legal": [
        {
            "name": "paralegal",
            "display_name": "Paralegal",
            "description": "Research and document drafting — assigned matters only",
            "permissions": ["goals:read", "goals:write", "knowledge:read", "knowledge:write"],
            "conditions": {"matter_access": "assigned_only"},
        },
        {
            "name": "associate_attorney",
            "display_name": "Associate Attorney",
            "description": "Full matter execution requiring senior review above threshold",
            "permissions": [
                "goals:read", "goals:write", "goals:execute",
                "agents:read", "knowledge:read",
            ],
            "conditions": {"supervisor_approval_over_usd": 50000},
        },
        {
            "name": "senior_partner",
            "display_name": "Senior Partner",
            "description": "Full firm access including billing and governance",
            "permissions": [
                "goals:read", "goals:write", "goals:execute", "goals:delete",
                "agents:read", "agents:write",
                "governance:read", "governance:write", "governance:approve",
                "costs:read", "costs:admin",
                "knowledge:read", "knowledge:write",
            ],
        },
        {
            "name": "client_portal",
            "display_name": "Client Portal (External)",
            "description": "Limited read access for external clients",
            "permissions": ["goals:read"],
            "conditions": {"matter_access": "client_own_matters"},
        },
    ],
    "finance": [
        {
            "name": "analyst",
            "display_name": "Financial Analyst",
            "description": "Read-only financial analysis",
            "permissions": ["goals:read", "knowledge:read", "costs:read"],
        },
        {
            "name": "trader",
            "display_name": "Trader",
            "description": "Execute trading goals within risk limits",
            "permissions": [
                "goals:read", "goals:write", "goals:execute", "knowledge:read",
            ],
            "conditions": {
                "risk_limit": "within_daily_var",
                "time_window": {
                    "start": "09:30", "end": "16:00", "tz": "America/New_York"
                },
            },
        },
        {
            "name": "risk_officer",
            "display_name": "Chief Risk Officer",
            "description": "Governance, policy, and emergency stop authority",
            "permissions": [
                "goals:read", "goals:delete",
                "governance:read", "governance:write", "governance:approve",
                "audit:read", "costs:admin",
            ],
        },
        {
            "name": "sox_compliance_manager",
            "display_name": "SOX Compliance Manager",
            "description": "SOX/FINRA compliance oversight and audit export",
            "permissions": ["audit:read", "audit:export", "governance:read", "costs:read"],
        },
    ],
    "education": [
        {
            "name": "student",
            "display_name": "Student",
            "description": "Access own learning goals only",
            "permissions": ["goals:read", "goals:execute"],
            "conditions": {"ownership": "creator"},
        },
        {
            "name": "instructor",
            "display_name": "Instructor",
            "description": "Manage course goals and student progress",
            "permissions": [
                "goals:read", "goals:write", "goals:execute",
                "agents:read", "knowledge:read", "knowledge:write",
            ],
            "conditions": {"course_access": "assigned_courses"},
        },
        {
            "name": "institution_admin",
            "display_name": "Institution Administrator",
            "description": "Full institutional access",
            "permissions": [
                "goals:read", "goals:write", "goals:delete",
                "agents:read", "agents:write",
                "tenancy:read", "tenancy:write",
                "audit:read", "costs:admin",
            ],
        },
    ],
    "ecommerce": [
        {
            "name": "catalog_manager",
            "display_name": "Catalog Manager",
            "description": "Product catalog automation",
            "permissions": [
                "goals:read", "goals:write", "agents:read",
                "knowledge:read", "knowledge:write",
            ],
        },
        {
            "name": "customer_success",
            "display_name": "Customer Success",
            "description": "Customer support automation — assigned region",
            "permissions": ["goals:read", "goals:write", "goals:execute", "knowledge:read"],
            "conditions": {"department_match": True},
        },
        {
            "name": "operations_lead",
            "display_name": "Operations Lead",
            "description": "Full operational access excluding financial admin",
            "permissions": [
                "goals:read", "goals:write", "goals:execute",
                "agents:read", "agents:write",
                "knowledge:read", "knowledge:write",
                "costs:read",
            ],
        },
    ],
}
```

### 3.6 main.py Wiring Changes

```python
# Additions to agent-verse-backend/app/main.py

from app.tenancy.scope_enforcement import ScopeEnforcementMiddleware
from app.tenancy.auth_router import router as auth_router

def create_app(manage_pools: bool = True) -> FastAPI:
    app = FastAPI(...)

    # ... existing TenantMiddleware (sets request.state.tenant, request.state.api_key) ...

    # ScopeEnforcementMiddleware reads those state values — must come AFTER TenantMiddleware
    app.add_middleware(
        ScopeEnforcementMiddleware,
        redis=app.state.redis,
        db_factory=lambda: app.state.db_session_factory(),
    )

    # New auth router
    app.include_router(auth_router, prefix="/api/auth", tags=["Auth & Authorization"])

    return app


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... existing pool setup ...

    # Seed canonical scope definitions
    from app.tenancy.scope_seeder import seed_scope_definitions
    async with app.state.db_session_factory() as db:
        await seed_scope_definitions(db)

    # Pre-warm permission cache for high-traffic tenants
    from app.tenancy.scope_enforcement import PermissionCache
    from app.tenancy.cache_warmer import warm_permission_cache
    await warm_permission_cache(
        app.state.redis,
        app.state.db_session_factory,
        min_daily_calls=10_000,
    )

    yield
```

---

## 4. Frontend Specification

### 4.1 New Pages & Routes

| Route | Sidebar Entry | Description |
|-------|---------------|-------------|
| `/settings/roles` | Settings → Roles & Permissions | RBAC management with domain templates |
| `/settings/roles/:roleId` | (nested drawer) | Role detail, edit, permission matrix |
| `/settings/api-keys` | Settings → API Keys | Key listing with scope badges |
| `/settings/api-keys/:keyId/scopes` | (nested) | Scope editor for a key |
| `/settings/ip-allowlist` | Settings → IP Allowlist | CIDR entry management |
| `/settings/scope-explorer` | Settings → Scope Explorer | Interactive scope catalog |

### 4.2 TypeScript Interfaces

```typescript
// src/features/auth/types.ts

export interface ScopeDefinition {
  scope: string;
  resource: string;
  action: string;
  description: string;
  riskLevel: 'low' | 'medium' | 'high' | 'critical';
  domain: string | null;
}

export interface CustomRole {
  id: string;
  tenantId: string;
  name: string;
  displayName: string;
  description: string | null;
  parentRoleId: string | null;
  permissions: string[];
  resolvedPermissions: string[];
  conditions: Record<string, unknown>;
  domain: string | null;
  isTemplate: boolean;
  isActive: boolean;
  assignmentCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface RoleAssignment {
  id: string;
  tenantId: string;
  userId: string;
  userEmail: string;
  userDisplayName: string;
  roleId: string | null;
  systemRole: string | null;
  roleName: string;
  roleDisplayName: string;
  resourceType: string | null;
  resourceId: string | null;
  conditions: Record<string, unknown>;
  grantedBy: string;
  grantedAt: string;
  expiresAt: string | null;
  revokedAt: string | null;
}

export interface APIKeyScope {
  scope: string;
  resourceId: string | null;
  expiresAt: string | null;
}

export interface APIKeyWithScopes {
  id: string;
  name: string;
  prefix: string;
  scopes: APIKeyScope[];
  effectiveScopes: string[];
  defaultRole: string;
  lastUsedAt: string | null;
  useCount: number;
  createdAt: string;
}

export interface IPAllowlistEntry {
  id: string;
  cidr: string;
  label: string | null;
  isActive: boolean;
  createdAt: string;
  expiresAt: string | null;
}

export interface PermissionGateProps {
  requiredScope: string;
  resourceId?: string;
  fallback?: React.ReactNode;
  children: React.ReactNode;
}
```

### 4.3 PermissionGate Component

```typescript
// src/features/auth/components/PermissionGate.tsx
import React from 'react';
import { useCurrentScopes } from '../hooks/useCurrentScopes';

export const PermissionGate: React.FC<PermissionGateProps> = ({
  requiredScope,
  resourceId,
  fallback = null,
  children,
}) => {
  const { grantedScopes, isLoading } = useCurrentScopes();

  if (isLoading) {
    return (
      <div
        className="permission-skeleton"
        style={{ animation: 'permSkeleton 1.5s ease-in-out infinite' }}
        aria-busy="true"
        aria-label="Loading permissions"
      />
    );
  }

  const hasScope = grantedScopes.includes(requiredScope);
  if (!hasScope) return <>{fallback}</>;
  return <>{children}</>;
};
```

### 4.4 Animation Specs

```css
/* src/features/auth/auth-animations.css */

@keyframes roleCardEntrance {
  0%   { opacity: 0; transform: translateY(12px) scale(0.97); }
  60%  { transform: translateY(-2px) scale(1.005); }
  100% { opacity: 1; transform: translateY(0) scale(1); }
}

@keyframes scopeGrantPop {
  0%   { opacity: 0; transform: scale(0.7); background-color: var(--color-success-100); }
  50%  { transform: scale(1.1); }
  100% { opacity: 1; transform: scale(1); background-color: var(--color-success-50); }
}

@keyframes scopeRevokeFade {
  0%   { opacity: 1; transform: scale(1); }
  30%  { transform: scale(1.05); background-color: var(--color-danger-100); }
  100% { opacity: 0; transform: scale(0.7); }
}

@keyframes permSkeleton {
  0%, 100% { background-color: var(--color-surface-2); opacity: 0.6; }
  50%       { background-color: var(--color-surface-3); opacity: 1; }
}

@keyframes accessDeniedShake {
  0%, 100% { transform: translateX(0); }
  15%      { transform: translateX(-6px) rotate(-1deg); }
  30%      { transform: translateX(5px) rotate(1deg); }
  45%      { transform: translateX(-5px); }
  60%      { transform: translateX(4px); }
  75%      { transform: translateX(-3px); }
  90%      { transform: translateX(2px); }
}

@keyframes inheritanceExpand {
  from { opacity: 0; max-height: 0; transform: translateX(-8px); }
  to   { opacity: 1; max-height: 600px; transform: translateX(0); }
}

@keyframes keyRotationSparkle {
  0%   { opacity: 0; transform: scale(0) rotate(0deg); }
  50%  { opacity: 1; transform: scale(1.3) rotate(180deg); }
  100% { opacity: 0; transform: scale(0) rotate(360deg); }
}

@keyframes ipBlockedPulse {
  0%   { background-color: transparent; }
  20%  { background-color: var(--color-danger-50); }
  80%  { background-color: var(--color-danger-50); }
  100% { background-color: transparent; }
}

.role-card {
  animation: roleCardEntrance 0.3s ease-out both;
}

.scope-tag--granted {
  animation: scopeGrantPop 0.25s ease-out both;
}

.scope-tag--revoked {
  animation: scopeRevokeFade 0.2s ease-out both;
}

.access-denied {
  animation: accessDeniedShake 0.5s cubic-bezier(0.36, 0.07, 0.19, 0.97) both;
}

.inheritance-tree-node {
  animation: inheritanceExpand 0.25s ease-out both;
}
```

### 4.5 Empty / Error States

```typescript
// No roles defined
export const EmptyRolesState: React.FC<{ onCreate: () => void; onBrowse: () => void }> = ({
  onCreate, onBrowse,
}) => (
  <div className="empty-state" role="status" aria-label="No custom roles">
    <ShieldIcon size={48} className="empty-state__icon" aria-hidden />
    <h3 className="empty-state__title">No custom roles yet</h3>
    <p className="empty-state__description">
      Start with a domain template (healthcare, legal, finance) or build
      a role from scratch. System roles admin, developer, and viewer are always available.
    </p>
    <div className="empty-state__actions">
      <Button variant="primary" onClick={onCreate}>Create Role</Button>
      <Button variant="secondary" onClick={onBrowse}>Browse Domain Templates</Button>
    </div>
  </div>
);

// IP allowlist empty
export const EmptyAllowlistState: React.FC<{ onAdd: () => void }> = ({ onAdd }) => (
  <div className="empty-state" role="status">
    <NetworkIcon size={40} aria-hidden />
    <h3>No IP restrictions configured</h3>
    <p>All IP addresses may access this tenant's API. Add CIDR blocks to restrict access.</p>
    <Button variant="primary" onClick={onAdd}>Add CIDR Block</Button>
  </div>
);

// Key with no scopes
export const NoScopesWarning: React.FC<{ onGrant: () => void }> = ({ onGrant }) => (
  <div className="empty-state empty-state--warning" role="alert">
    <KeyIcon size={40} aria-hidden />
    <h3>No scopes assigned</h3>
    <p>This key cannot access any protected endpoint until scopes are granted.</p>
    <Button variant="primary" onClick={onGrant}>Grant Scopes</Button>
  </div>
);
```

### 4.6 Dark Mode Compliance

```css
/* src/features/auth/auth-theme.css — CSS vars only, no hardcoded colours */

.role-card {
  background: var(--color-surface-1);
  border: 1px solid var(--color-border-default);
  color: var(--color-text-primary);
  box-shadow: var(--shadow-sm);
  border-radius: var(--radius-md);
}

.role-card:hover {
  border-color: var(--color-border-emphasis);
  box-shadow: var(--shadow-md);
}

.scope-badge          { background: var(--color-accent-subtle);  color: var(--color-accent-emphasis);  border: 1px solid var(--color-accent-muted); }
.scope-badge--critical { background: var(--color-danger-subtle); color: var(--color-danger-emphasis); border-color: var(--color-danger-muted); }
.scope-badge--high    { background: var(--color-warning-subtle); color: var(--color-warning-emphasis); border-color: var(--color-warning-muted); }
.scope-badge--medium  { background: var(--color-attention-subtle); color: var(--color-attention-emphasis); border-color: var(--color-attention-muted); }
.scope-badge--low     { background: var(--color-success-subtle);  color: var(--color-success-emphasis);  border-color: var(--color-success-muted); }

.permission-skeleton { border-radius: var(--radius-sm); height: 32px; width: 100%; }
.inheritance-line    { border-left: 2px solid var(--color-border-muted); margin-left: var(--spacing-4); padding-left: var(--spacing-4); }
```

### 4.7 Mobile Responsiveness

```css
.roles-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--spacing-4);
}

@media (max-width: 640px) {
  .roles-grid { grid-template-columns: 1fr; }

  .role-detail-panel {
    position: fixed;
    bottom: 0; left: 0; right: 0;
    height: 80vh;
    border-radius: var(--radius-xl) var(--radius-xl) 0 0;
    z-index: var(--z-modal);
    overflow-y: auto;
  }

  .scope-explorer { max-height: 60vh; overflow-y: auto; }

  .permission-matrix {
    font-size: var(--font-size-xs);
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
}
```

---

## 5. Scale Architecture

**Target:** 1 M tenants, 100 k API calls/second

| Layer | Bottleneck | Solution | Result |
|-------|-----------|----------|--------|
| Permission resolution | Postgres hit per request | Redis cache TTL=300s, LRU 50k entries | <2 ms p99 |
| IP allowlist | CIDR scan per request | Redis JSON per tenant TTL=60s | 0 Postgres reads |
| Scope catalog | DB read per startup | In-memory dict loaded once at boot | 0 ms lookup |
| Role inheritance | Recursive DB queries | Flattened `resolved_permissions` cached on role write | 1 DB read/role-update |
| Cache invalidation | Stale perms after role change | `invalidate_tenant()` via Redis SCAN on role mutation | <1 s propagation |
| Multi-region cache | Divergence between regions | Redis Cluster with cross-region async replication; 15 s max staleness | Global consistency |
| Cache warm-up | Cold start penalty | Pre-warm for tenants >10k daily calls during lifespan startup | 0 cold-start misses |

**Cache warm strategy:**
```python
async def warm_permission_cache(redis, db_factory, min_daily_calls: int = 10_000):
    async with db_factory() as db:
        from sqlalchemy import select, func
        from app.db.models.auth import AuditEvent, APIKey
        high_traffic = await db.execute(
            select(APIKey.tenant_id, APIKey.id)
            .join(AuditEvent, AuditEvent.api_key_id == APIKey.id)
            .group_by(APIKey.tenant_id, APIKey.id)
            .having(func.count() >= min_daily_calls // 24)
            .limit(50_000)
        )
    cache = PermissionCache(redis)
    async with db_factory() as db:
        for tenant_id, key_id in high_traffic.fetchall():
            scopes = await ScopeEnforcementMiddleware._load_from_db(
                db, str(tenant_id), str(key_id)
            )
            await cache.set(str(tenant_id), str(key_id), scopes)
```

---

## 6. Testing Strategy

```python
# agent-verse-backend/tests/tenancy/test_scope_enforcement.py
"""
Full test suite for ScopeEnforcementMiddleware, PermissionCache, IPAllowlistCache, ABACEvaluator.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.tenancy.scope_enforcement import (
    ABACEvaluator,
    IPAllowlistCache,
    PermissionCache,
    ScopeEnforcementMiddleware,
)
from app.tenancy.domain_role_templates import DOMAIN_ROLE_TEMPLATES


@pytest.fixture
def mock_redis():
    store: dict = {}
    r = AsyncMock()

    async def _get(k):
        return store.get(k)

    async def _setex(k, ttl, v):
        store[k] = v if isinstance(v, (bytes, str)) else json.dumps(v)

    async def _delete(*keys):
        for k in keys:
            store.pop(k, None)

    async def _scan(cursor, match="*", count=100):
        import fnmatch
        hits = [k for k in store if fnmatch.fnmatch(k, match)]
        return 0, hits

    r.get = _get
    r.setex = _setex
    r.delete = _delete
    r.scan = _scan
    r._store = store
    return r


# ---- PermissionCache -------------------------------------------------------

class TestPermissionCache:
    @pytest.mark.asyncio
    async def test_miss_returns_none(self, mock_redis):
        cache = PermissionCache(mock_redis)
        assert await cache.get("t1", "k1") is None

    @pytest.mark.asyncio
    async def test_set_and_get_round_trip(self, mock_redis):
        cache = PermissionCache(mock_redis)
        scopes = {"goals:read", "agents:read"}
        await cache.set("t1", "k1", scopes)
        result = await cache.get("t1", "k1")
        assert result == scopes

    @pytest.mark.asyncio
    async def test_invalidate_removes_entry(self, mock_redis):
        cache = PermissionCache(mock_redis)
        await cache.set("t1", "k1", {"goals:read"})
        await cache.invalidate("t1", "k1")
        assert await cache.get("t1", "k1") is None

    @pytest.mark.asyncio
    async def test_invalidate_tenant_clears_all_keys(self, mock_redis):
        cache = PermissionCache(mock_redis)
        for i in range(5):
            await cache.set("t1", f"k{i}", {"goals:read"})
        await cache.invalidate_tenant("t1")
        for i in range(5):
            assert await cache.get("t1", f"k{i}") is None

    @pytest.mark.asyncio
    async def test_different_tenants_isolated(self, mock_redis):
        cache = PermissionCache(mock_redis)
        await cache.set("t1", "k1", {"goals:read"})
        await cache.set("t2", "k1", {"goals:write"})
        assert await cache.get("t1", "k1") == {"goals:read"}
        assert await cache.get("t2", "k1") == {"goals:write"}


# ---- IPAllowlistCache ------------------------------------------------------

class TestIPAllowlistCache:
    @pytest.mark.asyncio
    async def test_no_cidrs_allows_any_ip(self, mock_redis):
        checker = IPAllowlistCache(mock_redis)
        mock_db = AsyncMock()
        key = f"ip_wl:t1"
        mock_redis._store[key] = json.dumps([])
        cidrs = await checker.get_cidrs("t1", mock_db)
        assert ScopeEnforcementMiddleware._ip_allowed("8.8.8.8", cidrs) is True

    @pytest.mark.asyncio
    async def test_ip_in_cidr_allowed(self, mock_redis):
        cidrs = ["10.0.0.0/8", "192.168.1.0/24"]
        assert ScopeEnforcementMiddleware._ip_allowed("10.5.5.5", cidrs) is True
        assert ScopeEnforcementMiddleware._ip_allowed("192.168.1.100", cidrs) is True

    @pytest.mark.asyncio
    async def test_ip_outside_cidr_blocked(self, mock_redis):
        cidrs = ["10.0.0.0/8"]
        assert ScopeEnforcementMiddleware._ip_allowed("8.8.8.8", cidrs) is False

    @pytest.mark.asyncio
    async def test_ipv6_allowed(self, mock_redis):
        cidrs = ["2001:db8::/32"]
        assert ScopeEnforcementMiddleware._ip_allowed("2001:db8::1", cidrs) is True

    @pytest.mark.asyncio
    async def test_invalid_ip_blocked(self, mock_redis):
        cidrs = ["10.0.0.0/8"]
        assert ScopeEnforcementMiddleware._ip_allowed("not_an_ip", cidrs) is False


# ---- ABACEvaluator ---------------------------------------------------------

class TestABACEvaluator:
    @pytest.mark.asyncio
    async def test_empty_conditions_pass(self):
        ev = ABACEvaluator()
        assert await ev.evaluate({}, {}, {}) is True

    @pytest.mark.asyncio
    async def test_department_match_same(self):
        ev = ABACEvaluator()
        result = await ev.evaluate(
            {"department_match": True},
            {"department": "legal"},
            {"department": "legal"},
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_department_match_different(self):
        ev = ABACEvaluator()
        result = await ev.evaluate(
            {"department_match": True},
            {"department": "finance"},
            {"department": "legal"},
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_ownership_creator_match(self):
        ev = ABACEvaluator()
        uid = str(uuid4())
        result = await ev.evaluate(
            {"ownership": "creator"},
            {"user_id": uid},
            {"created_by": uid},
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_ownership_creator_no_match(self):
        ev = ABACEvaluator()
        result = await ev.evaluate(
            {"ownership": "creator"},
            {"user_id": str(uuid4())},
            {"created_by": str(uuid4())},
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_multiple_conditions_all_must_pass(self):
        ev = ABACEvaluator()
        uid = str(uuid4())
        result = await ev.evaluate(
            {"department_match": True, "ownership": "creator"},
            {"department": "legal", "user_id": uid},
            {"department": "legal", "created_by": "other_user"},
        )
        assert result is False  # ownership fails


# ---- Domain Role Templates -------------------------------------------------

class TestDomainRoleTemplates:
    def test_all_five_domains_present(self):
        assert set(DOMAIN_ROLE_TEMPLATES) >= {"healthcare", "legal", "finance", "education", "ecommerce"}

    def test_healthcare_phi_reader_minimal_perms(self):
        phi = next(r for r in DOMAIN_ROLE_TEMPLATES["healthcare"] if r["name"] == "phi_reader")
        assert "goals:delete" not in phi["permissions"]
        assert "governance:approve" not in phi["permissions"]
        assert "tenancy:write" not in phi["permissions"]

    def test_legal_client_portal_read_only(self):
        cp = next(r for r in DOMAIN_ROLE_TEMPLATES["legal"] if r["name"] == "client_portal")
        assert cp["permissions"] == ["goals:read"]

    def test_finance_trader_has_time_window(self):
        trader = next(r for r in DOMAIN_ROLE_TEMPLATES["finance"] if r["name"] == "trader")
        assert "time_window" in trader.get("conditions", {})

    @pytest.mark.parametrize("domain", ["healthcare", "legal", "finance", "education", "ecommerce"])
    def test_all_permissions_are_known_scopes(self, domain):
        known = {
            "goals:read", "goals:write", "goals:delete", "goals:execute",
            "agents:read", "agents:write", "agents:delete",
            "knowledge:read", "knowledge:write", "knowledge:delete",
            "governance:read", "governance:write", "governance:approve",
            "audit:read", "audit:export",
            "costs:read", "costs:admin",
            "tenancy:read", "tenancy:write",
            "mcp:read", "mcp:write",
        }
        for role in DOMAIN_ROLE_TEMPLATES[domain]:
            bad = set(role["permissions"]) - known
            assert not bad, f"Unknown permissions {bad} in {domain}/{role['name']}"
```

---

## 7. Domain Extensibility

### Healthcare

```python
# Add PHI classification enforcement:
# 1. Tag goals with data_classification='PHI' at creation
# 2. phi_reader role condition: {"data_classification_lte": "PHI"}
# 3. Every PHI access emits HIPAA audit event (see Spec 5)
# 4. Minimum-necessary principle: phi_reader cannot bulk-export, only individual records
# Extend scope_definitions with: 'phi:read', 'phi:export' (critical risk_level)
```

### Legal

```python
# Matter-scoped assignments: resource_type='matter', resource_id=matter_uuid
# Add custom ABAC condition handler: matter_access='assigned_only'
# Privilege check: knowledge documents with privileged=True require attorney role
# Conflict check: ABAC condition prevents attorney accessing adverse-party matters
```

### Finance

```python
# Segregation of Duties constraint: enforce at role_assignment creation
# No user may hold both 'trader' and 'risk_officer' simultaneously
# Time-window enforcement built into trader role (NYSE hours only)
# Position limit conditions: {"max_notional_usd": 1_000_000}
# After-hours emergency scope: temporary escalation with mandatory HITL approval
```

### Education

```python
# Course-scoped assignments: resource_type='course', resource_id=course_uuid
# Student privacy: FERPA-compliant — students cannot see peers' goals
# Instructor temporary elevation: grant students goals:execute for exam windows
# Parent/guardian portal: read-only scoped to enrolled child's goals
```

### E-commerce

```python
# Region-scoped catalog manager: resource_type='region', resource_id=region_code
# Vendor portal: external vendor read-only on their own SKU goals
# Seasonal access expansion: time-based ABAC for holiday catalog operations
# Third-party fulfillment: narrow API key with only goals:read + mcp:read
```
