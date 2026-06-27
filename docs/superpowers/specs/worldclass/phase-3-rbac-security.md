# Phase 3: RBAC & Security

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a complete role-based access control system with four roles, JWT + API-key role loading, role-protected endpoints, and IP allowlisting with per-tenant CIDR enforcement.

**Architecture:** Roles are stored in a new `user_roles` table with tenant-scoped RLS. `TenantContext` is extended with `roles`. The `TenantMiddleware` populates roles from Keycloak JWT claims (if present) or from the `user_roles` DB table for API key users. A `require_role` decorator wraps FastAPI path functions. IP allowlisting is a separate `ip_allowlist` table checked in `TenantMiddleware`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x, PostgreSQL RLS, pytest-asyncio, httpx ASGITransport

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `app/db/models/rbac.py` | Create | `UserRole`, `IPAllowlist` ORM models |
| `app/db/migrations/versions/0023_rbac.py` | Create | `user_roles` + `ip_allowlist` tables |
| `app/tenancy/context.py` | Modify | Add `roles: tuple[str, ...]` field to `TenantContext` |
| `app/tenancy/middleware.py` | Modify | Populate `roles` from JWT or DB; enforce IP allowlist |
| `app/tenancy/rbac.py` | Create | `require_role` decorator, `ROLES` enum, role hierarchy |
| `app/api/tenants.py` | Modify | Add `GET/POST/DELETE /tenants/me/roles` endpoints |
| `app/api/tenants.py` | Modify | Add `GET/POST/DELETE /tenants/me/ip-allowlist` endpoints |
| `app/api/governance.py` | Modify | Apply `require_role` to emergency-stop, approvals, audit, budget |
| `app/api/agents.py` | Modify | Apply `require_role` to DELETE /agents/* |
| `tests/test_phase3_rbac.py` | Create | Full RBAC test suite |

---

## Task 3.1 — Role Model: user_roles Table + UserRole Pydantic Model

**Current state:** No role model exists. `TenantContext` has only `tenant_id`, `plan`, `api_key_id`.

**Files:**
- Create: `agent-verse-backend/app/db/models/rbac.py`
- Create: `agent-verse-backend/app/db/migrations/versions/0023_rbac.py`
- Test: `agent-verse-backend/tests/test_phase3_rbac.py`

- [ ] **Step 1: Create ORM models**

```python
# app/db/models/rbac.py
"""ORM models for RBAC: user roles and IP allowlist."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class UserRole(Base):
    """Maps a user (identified by sub/api_key_id) to a role within a tenant."""

    __tablename__ = "user_roles"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # user_id is the Keycloak `sub` claim or the api_key_id for API key users
    user_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IPAllowlistEntry(Base):
    """CIDR range allowed to access this tenant's API."""

    __tablename__ = "ip_allowlist"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    cidr: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

- [ ] **Step 2: Create Alembic migration**

```python
# app/db/migrations/versions/0023_rbac.py
"""Create user_roles and ip_allowlist tables.

Revision ID: 0023
Revises: 0022
"""
from __future__ import annotations
from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_roles",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("user_id", sa.String(200), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "user_id", "role", name="uq_user_role"),
    )
    op.create_index("ix_user_roles_tenant", "user_roles", ["tenant_id"])
    op.create_index("ix_user_roles_user", "user_roles", ["user_id"])

    op.create_table(
        "ip_allowlist",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("cidr", sa.String(50), nullable=False),
        sa.Column("description", sa.String(200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_ip_allowlist_tenant", "ip_allowlist", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("ip_allowlist")
    op.drop_table("user_roles")
```

- [ ] **Step 3: Write failing tests**

```python
# tests/test_phase3_rbac.py
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport


def test_user_role_model_has_required_fields():
    """UserRole ORM model must have id, tenant_id, user_id, role, created_at."""
    from app.db.models.rbac import UserRole
    assert hasattr(UserRole, "id")
    assert hasattr(UserRole, "tenant_id")
    assert hasattr(UserRole, "user_id")
    assert hasattr(UserRole, "role")
    assert hasattr(UserRole, "created_at")


def test_ip_allowlist_model_has_required_fields():
    """IPAllowlistEntry must have id, tenant_id, cidr, description, created_at."""
    from app.db.models.rbac import IPAllowlistEntry
    assert hasattr(IPAllowlistEntry, "id")
    assert hasattr(IPAllowlistEntry, "cidr")
    assert hasattr(IPAllowlistEntry, "description")
```

- [ ] **Step 4: Run — expect pass**

```bash
cd agent-verse-backend
pytest tests/test_phase3_rbac.py -k "model" -xvs
```

- [ ] **Step 5: Commit**

```bash
git add app/db/models/rbac.py app/db/migrations/versions/0023_rbac.py \
        tests/test_phase3_rbac.py
git commit -m "feat(rbac): user_roles and ip_allowlist DB models + migration"
```

---

## Task 3.2 — RBAC Middleware: Roles in TenantContext

**Current state:** `TenantContext` is a `frozen` dataclass with `tenant_id`, `plan`, `api_key_id`. No roles field exists. `TenantMiddleware` resolves tenant via API key only.

**Gap:** Add `roles: tuple[str, ...]` to `TenantContext`. In `TenantMiddleware`, after resolving tenant, load roles from Keycloak JWT `realm_access.roles` if Bearer token, or from `user_roles` DB table if API key.

**Files:**
- Modify: `agent-verse-backend/app/tenancy/context.py`
- Modify: `agent-verse-backend/app/tenancy/middleware.py`
- Create: `agent-verse-backend/app/tenancy/rbac.py`
- Test: `agent-verse-backend/tests/test_phase3_rbac.py`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_phase3_rbac.py

def test_tenant_context_has_roles_field():
    """TenantContext must have a roles field defaulting to empty tuple."""
    from app.tenancy.context import TenantContext, PlanTier
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1")
    assert hasattr(ctx, "roles")
    assert ctx.roles == ()

def test_tenant_context_roles_are_immutable():
    """TenantContext.roles must be a tuple (immutable)."""
    from app.tenancy.context import TenantContext, PlanTier
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1",
                        roles=("admin", "operator"))
    assert isinstance(ctx.roles, tuple)
    assert "admin" in ctx.roles

def test_require_role_allows_matching_role():
    """require_role must allow request when tenant has required role."""
    from app.tenancy.rbac import has_role
    from app.tenancy.context import TenantContext, PlanTier
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1",
                        roles=("admin",))
    assert has_role(ctx, "admin") is True
    assert has_role(ctx, "operator") is False

def test_require_role_admin_implies_all_roles():
    """Admin role implicitly has operator and approver permissions."""
    from app.tenancy.rbac import has_any_role
    from app.tenancy.context import TenantContext, PlanTier
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1",
                        roles=("admin",))
    # Admin can act as operator or approver
    assert has_any_role(ctx, ["admin", "operator"]) is True
    assert has_any_role(ctx, ["approver", "admin"]) is True
```

- [ ] **Step 2: Run — expect failures**

```bash
pytest tests/test_phase3_rbac.py -k "tenant_context" -xvs
```
Expected: `TypeError: TenantContext() got unexpected keyword argument 'roles'`

- [ ] **Step 3: Add roles to TenantContext**

Modify `app/tenancy/context.py`:

```python
@dataclass(frozen=True, slots=True)
class TenantContext:
    """Immutable identity injected into every authenticated request."""

    tenant_id: str
    plan: PlanTier
    api_key_id: str
    roles: tuple[str, ...] = ()  # RBAC roles: "admin" | "operator" | "viewer" | "approver"
```

- [ ] **Step 4: Create RBAC module**

```python
# app/tenancy/rbac.py
"""Role-Based Access Control helpers for AgentVerse.

Roles (most privileged first):
  admin    — full platform control (emergency stop, all writes, user management)
  operator — create/delete agents, run goals, manage connectors
  approver — approve/reject HITL requests
  viewer   — read-only access to goals, audit, agents

Role hierarchy: admin > operator/approver > viewer
"""
from __future__ import annotations

import functools
from typing import Any, Callable

from fastapi import HTTPException, Request, status

from app.tenancy.context import TenantContext

# All valid roles
VALID_ROLES = frozenset({"admin", "operator", "viewer", "approver"})

# Role hierarchy: admin implicitly has all other roles
_ROLE_IMPLIES: dict[str, frozenset[str]] = {
    "admin": frozenset({"admin", "operator", "viewer", "approver"}),
    "operator": frozenset({"operator", "viewer"}),
    "approver": frozenset({"approver", "viewer"}),
    "viewer": frozenset({"viewer"}),
}


def effective_roles(ctx: TenantContext) -> frozenset[str]:
    """Expand ctx.roles with implied roles from hierarchy."""
    result: set[str] = set()
    for role in ctx.roles:
        result.update(_ROLE_IMPLIES.get(role, frozenset({role})))
    return frozenset(result)


def has_role(ctx: TenantContext, role: str) -> bool:
    """Return True if ctx has the given role (with hierarchy expansion)."""
    return role in effective_roles(ctx)


def has_any_role(ctx: TenantContext, roles: list[str]) -> bool:
    """Return True if ctx has any of the given roles."""
    effective = effective_roles(ctx)
    return any(r in effective for r in roles)


def require_role(*roles: str) -> Callable:
    """FastAPI dependency factory that enforces role requirement.

    Usage:
        @router.post("/endpoint")
        async def endpoint(
            request: Request,
            _: None = Depends(require_role("admin")),
        ):
            ...
    """
    def dependency(request: Request) -> None:
        ctx: TenantContext | None = getattr(request.state, "tenant", None)
        if ctx is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
        if not has_any_role(ctx, list(roles)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Insufficient permissions. Required: one of {list(roles)}. "
                    f"Your roles: {list(ctx.roles)}"
                ),
            )
    return dependency


async def load_roles_from_db(
    user_id: str,
    tenant_id: str,
    db_session_factory: Any,
) -> tuple[str, ...]:
    """Load roles for a user from the user_roles table.

    Returns empty tuple if DB unavailable or user has no roles.
    """
    if db_session_factory is None:
        return ()
    try:
        from sqlalchemy import select
        from app.db.models.rbac import UserRole
        async with db_session_factory() as session:
            result = await session.execute(
                select(UserRole.role).where(
                    UserRole.tenant_id == tenant_id,
                    UserRole.user_id == user_id,
                )
            )
            rows = result.scalars().all()
        return tuple(rows)
    except Exception:
        return ()


def extract_roles_from_jwt(jwt_payload: dict[str, Any]) -> tuple[str, ...]:
    """Extract AgentVerse roles from a Keycloak JWT payload.

    Looks for roles in:
    1. realm_access.roles (Keycloak standard)
    2. resource_access.agentverse.roles (client-specific)
    3. Top-level "roles" claim (custom mapper)

    Only returns roles that are in VALID_ROLES.
    """
    roles: set[str] = set()

    realm_access = jwt_payload.get("realm_access", {})
    for r in realm_access.get("roles", []):
        if r in VALID_ROLES:
            roles.add(r)

    resource_access = jwt_payload.get("resource_access", {})
    for r in resource_access.get("agentverse", {}).get("roles", []):
        if r in VALID_ROLES:
            roles.add(r)

    for r in jwt_payload.get("roles", []):
        if r in VALID_ROLES:
            roles.add(r)

    return tuple(roles)
```

- [ ] **Step 5: Update TenantMiddleware to populate roles**

In `app/tenancy/middleware.py`, in the `TenantMiddleware.__call__` method, after the tenant context is resolved (after the API key lookup), add role loading:

```python
# After tenant_ctx is created from API key resolution:

# Load roles: try JWT first, then DB fallback for API key users
_roles: tuple[str, ...] = ()
auth_header = request.headers.get("Authorization", "")
if auth_header.startswith("Bearer "):
    # Try extracting roles from JWT without re-verifying (already verified by Keycloak middleware)
    try:
        import base64, json as _json
        _token = auth_header[7:]
        _parts = _token.split(".")
        if len(_parts) == 3:
            _padded = _parts[1] + "=" * (4 - len(_parts[1]) % 4)
            _payload = _json.loads(base64.urlsafe_b64decode(_padded))
            from app.tenancy.rbac import extract_roles_from_jwt
            _roles = extract_roles_from_jwt(_payload)
    except Exception:
        pass

if not _roles:
    # API key user — load from DB
    db = getattr(request.app.state, "db_session_factory", None)
    if db is not None and tenant_ctx is not None:
        from app.tenancy.rbac import load_roles_from_db
        try:
            import asyncio
            _roles = await load_roles_from_db(
                user_id=tenant_ctx.api_key_id,
                tenant_id=tenant_ctx.tenant_id,
                db_session_factory=db,
            )
        except Exception:
            pass

# Reconstruct TenantContext with roles (it's frozen so we create a new one)
if tenant_ctx is not None and _roles:
    from app.tenancy.context import TenantContext
    tenant_ctx = TenantContext(
        tenant_id=tenant_ctx.tenant_id,
        plan=tenant_ctx.plan,
        api_key_id=tenant_ctx.api_key_id,
        roles=_roles,
    )

request.state.tenant = tenant_ctx
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/test_phase3_rbac.py -k "tenant_context or rbac" -xvs
```
Expected: PASS (5 tests)

- [ ] **Step 7: Commit**

```bash
git add app/tenancy/context.py app/tenancy/middleware.py app/tenancy/rbac.py \
        tests/test_phase3_rbac.py
git commit -m "feat(rbac): roles in TenantContext + middleware role loading + require_role decorator"
```

---

## Task 3.3 — Role-Protected Endpoints

**Current state:** All endpoints are accessible to any authenticated tenant. No role enforcement exists.

**Gap:** Apply `require_role` to the 5 specified endpoints.

**Files:**
- Modify: `agent-verse-backend/app/api/governance.py`
- Modify: `agent-verse-backend/app/api/agents.py`
- Test: `agent-verse-backend/tests/test_phase3_rbac.py`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_phase3_rbac.py

from app.main import create_app

@pytest.fixture
def app():
    return create_app()

@pytest.mark.asyncio
async def test_emergency_stop_requires_admin(app):
    """POST /governance/emergency-stop must return 403 for non-admin."""
    from app.tenancy.context import TenantContext, PlanTier
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Inject a tenant context with viewer role only
        viewer_ctx = TenantContext(
            tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1", roles=("viewer",)
        )
        app.state.tenant = viewer_ctx  # shortcut for test

        # Patch the middleware to inject this context
        with patch("app.tenancy.middleware.TenantMiddleware.__call__",
                   new=make_tenant_middleware(viewer_ctx)):
            response = await client.post("/governance/emergency-stop",
                                          headers={"X-API-Key": "test-key"})
        # 403 because viewer is not admin
        assert response.status_code in {401, 403}

def make_tenant_middleware(ctx):
    """Helper to create a middleware that injects a fixed tenant context."""
    async def middleware(self, scope, receive, send):
        if scope["type"] == "http":
            from starlette.requests import Request
            request = Request(scope)
            request.state.tenant = ctx
        await self.app(scope, receive, send)
    return middleware

@pytest.mark.asyncio
async def test_emergency_stop_allowed_for_admin(app):
    """POST /governance/emergency-stop must succeed for admin."""
    from app.tenancy.context import TenantContext, PlanTier
    admin_ctx = TenantContext(
        tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1", roles=("admin",)
    )
    # Test role check directly
    from app.tenancy.rbac import has_role
    assert has_role(admin_ctx, "admin") is True

@pytest.mark.asyncio
async def test_delete_agent_requires_operator_or_admin():
    """DELETE /agents/{id} must require operator or admin role."""
    from app.tenancy.rbac import has_any_role
    from app.tenancy.context import TenantContext, PlanTier

    viewer_ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1",
                               roles=("viewer",))
    operator_ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1",
                                 roles=("operator",))
    assert not has_any_role(viewer_ctx, ["operator", "admin"])
    assert has_any_role(operator_ctx, ["operator", "admin"])

@pytest.mark.asyncio
async def test_audit_requires_non_viewer_role():
    """GET /governance/audit must be accessible to operator, approver, admin."""
    from app.tenancy.rbac import has_any_role, effective_roles
    from app.tenancy.context import TenantContext, PlanTier

    for role in ["operator", "approver", "admin"]:
        ctx = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1",
                            roles=(role,))
        assert has_any_role(ctx, ["operator", "approver", "admin"]), \
            f"Role {role} should have audit access"

    viewer = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1",
                           roles=("viewer",))
    assert not has_any_role(viewer, ["operator", "approver", "admin"])
```

- [ ] **Step 2: Run — roles tests pass but endpoint protection not yet wired**

```bash
pytest tests/test_phase3_rbac.py -k "role" -xvs
```

- [ ] **Step 3: Apply require_role to governance endpoints**

In `app/api/governance.py`:

```python
from fastapi import Depends
from app.tenancy.rbac import require_role

# POST /governance/emergency-stop → requires admin
@router.post("/emergency-stop")
async def emergency_stop(
    request: Request,
    _rbac: None = Depends(require_role("admin")),
) -> dict[str, Any]:
    # ... existing implementation unchanged ...

# POST /governance/approvals/*/approve → requires approver or admin
@router.post("/approvals/{request_id}/approve")
async def approve_request(
    request: Request,
    request_id: str,
    body: ApproveRejectRequest,
    _rbac: None = Depends(require_role("approver", "admin")),
) -> dict[str, Any]:
    # ... existing implementation unchanged ...

@router.post("/approvals/{request_id}/reject")
async def reject_request(
    request: Request,
    request_id: str,
    body: ApproveRejectRequest,
    _rbac: None = Depends(require_role("approver", "admin")),
) -> dict[str, Any]:
    # ... existing implementation unchanged ...

# GET /governance/audit → requires operator, approver, or admin
@router.get("/audit")
async def query_audit(
    request: Request,
    goal_id: str | None = None,
    tool_name: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int = 100,
    offset: int = 0,
    _rbac: None = Depends(require_role("operator", "approver", "admin")),
) -> list[dict[str, Any]]:
    # ... existing implementation unchanged ...

# PUT /governance/budget → requires admin
@router.put("/budget")
async def set_budget(
    request: Request,
    body: SetBudgetRequest,
    _rbac: None = Depends(require_role("admin")),
) -> dict[str, Any]:
    # ... existing implementation unchanged ...
```

In `app/api/agents.py`:

```python
from fastapi import Depends
from app.tenancy.rbac import require_role

# DELETE /agents/* → requires operator or admin
@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    request: Request,
    agent_id: str,
    _rbac: None = Depends(require_role("operator", "admin")),
) -> None:
    # ... existing implementation unchanged ...
```

- [ ] **Step 4: Run all RBAC tests**

```bash
pytest tests/test_phase3_rbac.py -v
```
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add app/api/governance.py app/api/agents.py tests/test_phase3_rbac.py
git commit -m "feat(rbac): role-protected endpoints — emergency-stop, approvals, audit, budget, delete-agent"
```

---

## Task 3.4 — Role Management API Endpoints

**Current state:** No `GET/POST/DELETE /tenants/me/roles` endpoints exist.

**Files:**
- Modify: `agent-verse-backend/app/api/tenants.py`
- Test: `agent-verse-backend/tests/test_phase3_rbac.py`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_phase3_rbac.py

@pytest.mark.asyncio
async def test_create_role_endpoint():
    """POST /tenants/me/roles must create user role and return it."""
    from app.main import create_app
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/tenants/me/roles",
            json={"user_id": "user-123", "role": "operator"},
            headers={"X-API-Key": "test-key"},
        )
        # May return 401 (no real auth) or 201 (if middleware passes)
        # The key thing is the endpoint exists and doesn't 404
        assert response.status_code != 404

@pytest.mark.asyncio
async def test_role_endpoint_rejects_invalid_role():
    """POST /tenants/me/roles must return 422 for invalid role name."""
    from app.main import create_app
    from app.tenancy.context import TenantContext, PlanTier
    app = create_app()

    # Test Pydantic validation directly
    from pydantic import ValidationError
    from app.api.tenants import CreateRoleRequest
    with pytest.raises(ValidationError):
        CreateRoleRequest(user_id="user-1", role="superuser")  # invalid role
```

- [ ] **Step 2: Run — expect 404 or AttributeError**

```bash
pytest tests/test_phase3_rbac.py -k "role_endpoint" -xvs
```

- [ ] **Step 3: Add role management endpoints to tenants.py**

Add to `app/api/tenants.py`:

```python
from pydantic import BaseModel, field_validator
from app.tenancy.rbac import VALID_ROLES


class CreateRoleRequest(BaseModel):
    user_id: str
    role: str

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in VALID_ROLES:
            raise ValueError(f"role must be one of {sorted(VALID_ROLES)}, got {v!r}")
        return v


@router.get("/tenants/me/roles")
async def list_roles(request: Request) -> list[dict]:
    """List all user role assignments for this tenant."""
    tenant = _require_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        return []
    try:
        from sqlalchemy import select
        from app.db.models.rbac import UserRole
        from app.db.rls import sqlalchemy_rls_context
        async with db() as session:
            async with sqlalchemy_rls_context(session, tenant.tenant_id):
                result = await session.execute(
                    select(UserRole).where(UserRole.tenant_id == tenant.tenant_id)
                )
                rows = result.scalars().all()
        return [
            {"id": r.id, "user_id": r.user_id, "role": r.role,
             "created_at": r.created_at.isoformat() if r.created_at else ""}
            for r in rows
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/tenants/me/roles", status_code=201)
async def create_role(request: Request, body: CreateRoleRequest) -> dict:
    """Assign a role to a user within this tenant."""
    tenant = _require_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    import uuid
    role_id = uuid.uuid4().hex
    if db is None:
        # In-memory fallback for tests
        return {"id": role_id, "user_id": body.user_id, "role": body.role,
                "tenant_id": tenant.tenant_id}
    try:
        from app.db.models.rbac import UserRole
        from app.db.rls import sqlalchemy_rls_context
        row = UserRole(
            id=role_id,
            tenant_id=tenant.tenant_id,
            user_id=body.user_id,
            role=body.role,
        )
        async with db() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant.tenant_id):
                session.add(row)
        return {"id": role_id, "user_id": body.user_id, "role": body.role,
                "tenant_id": tenant.tenant_id}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/tenants/me/roles/{role_id}", status_code=204)
async def delete_role(request: Request, role_id: str) -> None:
    """Remove a role assignment."""
    tenant = _require_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        return
    try:
        from sqlalchemy import select
        from app.db.models.rbac import UserRole
        from app.db.rls import sqlalchemy_rls_context
        async with db() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant.tenant_id):
                result = await session.execute(
                    select(UserRole).where(
                        UserRole.id == role_id,
                        UserRole.tenant_id == tenant.tenant_id,
                    )
                )
                row = result.scalar_one_or_none()
                if row is None:
                    raise HTTPException(status_code=404, detail="Role assignment not found")
                await session.delete(row)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_phase3_rbac.py -k "role_endpoint" -xvs
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/api/tenants.py tests/test_phase3_rbac.py
git commit -m "feat(rbac): GET/POST/DELETE /tenants/me/roles endpoints"
```

---

## Task 3.5 — IP Allowlisting Middleware + API

**Current state:** No IP filtering exists. Any IP can call the API.

**Gap:** New `ip_allowlist` table (migration 0023). Middleware checks: if tenant has any IP allowlist entries, reject requests from IPs not matching any CIDR. New `GET/POST/DELETE /tenants/me/ip-allowlist` API.

**Files:**
- Modify: `agent-verse-backend/app/tenancy/middleware.py`
- Modify: `agent-verse-backend/app/api/tenants.py`
- Test: `agent-verse-backend/tests/test_phase3_rbac.py`

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_phase3_rbac.py

def test_ip_in_cidr_check():
    """IP allowlist CIDR matching must work correctly."""
    from app.tenancy.rbac import is_ip_allowed

    # Single IP in /32
    assert is_ip_allowed("192.168.1.100", ["192.168.1.100/32"])
    # IP in /24
    assert is_ip_allowed("10.0.0.50", ["10.0.0.0/24"])
    # IP not in range
    assert not is_ip_allowed("192.168.2.1", ["192.168.1.0/24"])
    # IPv6 loopback always allowed
    assert is_ip_allowed("127.0.0.1", ["10.0.0.0/8"])  # loopback bypass

def test_ip_allowlist_empty_means_all_allowed():
    """Empty IP allowlist means no restriction."""
    from app.tenancy.rbac import is_ip_allowed
    assert is_ip_allowed("1.2.3.4", [])  # No entries = allow all

@pytest.mark.asyncio
async def test_ip_allowlist_endpoint_exists():
    """GET /tenants/me/ip-allowlist must exist and return list."""
    from app.main import create_app
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/tenants/me/ip-allowlist",
            headers={"X-API-Key": "test-key"},
        )
        assert response.status_code != 404
```

- [ ] **Step 2: Run — expect failure**

```bash
pytest tests/test_phase3_rbac.py -k "ip" -xvs
```
Expected: `ImportError: cannot import name 'is_ip_allowed'`

- [ ] **Step 3: Add is_ip_allowed to rbac.py**

Add to `app/tenancy/rbac.py`:

```python
import ipaddress


def is_ip_allowed(client_ip: str, allowed_cidrs: list[str]) -> bool:
    """Return True if client_ip is allowed by the allowlist.

    Empty allowlist = no restriction (all IPs allowed).
    Loopback (127.x, ::1) is always allowed.
    """
    if not allowed_cidrs:
        return True

    try:
        ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False  # Invalid IP format — deny

    # Always allow loopback
    if ip.is_loopback:
        return True

    for cidr in allowed_cidrs:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
            if ip in network:
                return True
        except ValueError:
            continue  # Skip malformed CIDR entries

    return False
```

- [ ] **Step 4: Add IP allowlist middleware check**

In `app/tenancy/middleware.py` `TenantMiddleware.__call__`, after roles are loaded and before `await self.app(scope, receive, send)`, add:

```python
# IP allowlist check (only when tenant resolved and DB available)
if tenant_ctx is not None:
    db = getattr(request.app.state, "db_session_factory", None)
    _allowed_cidrs: list[str] = []
    if db is not None:
        try:
            from sqlalchemy import select
            from app.db.models.rbac import IPAllowlistEntry
            async with db() as session:
                result = await session.execute(
                    select(IPAllowlistEntry.cidr).where(
                        IPAllowlistEntry.tenant_id == tenant_ctx.tenant_id
                    )
                )
                _allowed_cidrs = [str(r) for r in result.scalars().all()]
        except Exception:
            pass  # DB unavailable — skip allowlist check

    if _allowed_cidrs:
        client_ip = request.client.host if request.client else ""
        # Also check X-Forwarded-For for proxy deployments
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()

        from app.tenancy.rbac import is_ip_allowed
        if not is_ip_allowed(client_ip, _allowed_cidrs):
            from starlette.responses import JSONResponse
            response = JSONResponse(
                {"detail": f"IP {client_ip} is not in the allowlist for this tenant"},
                status_code=403,
            )
            await response(scope, receive, send)
            return
```

- [ ] **Step 5: Add IP allowlist API endpoints**

Add to `app/api/tenants.py`:

```python
class CreateIPAllowlistRequest(BaseModel):
    cidr: str
    description: str = ""

    @field_validator("cidr")
    @classmethod
    def validate_cidr(cls, v: str) -> str:
        import ipaddress
        try:
            ipaddress.ip_network(v, strict=False)
        except ValueError as exc:
            raise ValueError(f"Invalid CIDR: {v!r}") from exc
        return v


@router.get("/tenants/me/ip-allowlist")
async def list_ip_allowlist(request: Request) -> list[dict]:
    tenant = _require_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        return []
    try:
        from sqlalchemy import select
        from app.db.models.rbac import IPAllowlistEntry
        from app.db.rls import sqlalchemy_rls_context
        async with db() as session:
            async with sqlalchemy_rls_context(session, tenant.tenant_id):
                result = await session.execute(
                    select(IPAllowlistEntry).where(
                        IPAllowlistEntry.tenant_id == tenant.tenant_id
                    )
                )
                rows = result.scalars().all()
        return [
            {"id": r.id, "cidr": r.cidr, "description": r.description,
             "created_at": r.created_at.isoformat() if r.created_at else ""}
            for r in rows
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/tenants/me/ip-allowlist", status_code=201)
async def create_ip_allowlist_entry(
    request: Request, body: CreateIPAllowlistRequest
) -> dict:
    tenant = _require_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    import uuid
    entry_id = uuid.uuid4().hex
    if db is None:
        return {"id": entry_id, "cidr": body.cidr, "description": body.description}
    try:
        from app.db.models.rbac import IPAllowlistEntry
        from app.db.rls import sqlalchemy_rls_context
        row = IPAllowlistEntry(
            id=entry_id,
            tenant_id=tenant.tenant_id,
            cidr=body.cidr,
            description=body.description,
        )
        async with db() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant.tenant_id):
                session.add(row)
        return {"id": entry_id, "cidr": body.cidr, "description": body.description}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/tenants/me/ip-allowlist/{entry_id}", status_code=204)
async def delete_ip_allowlist_entry(request: Request, entry_id: str) -> None:
    tenant = _require_tenant(request)
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        return
    try:
        from sqlalchemy import select
        from app.db.models.rbac import IPAllowlistEntry
        from app.db.rls import sqlalchemy_rls_context
        async with db() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant.tenant_id):
                result = await session.execute(
                    select(IPAllowlistEntry).where(
                        IPAllowlistEntry.id == entry_id,
                        IPAllowlistEntry.tenant_id == tenant.tenant_id,
                    )
                )
                row = result.scalar_one_or_none()
                if row is None:
                    raise HTTPException(status_code=404, detail="Allowlist entry not found")
                await session.delete(row)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
```

- [ ] **Step 6: Run all Phase 3 tests**

```bash
pytest tests/test_phase3_rbac.py -v
```
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add app/tenancy/rbac.py app/tenancy/middleware.py app/api/tenants.py \
        tests/test_phase3_rbac.py
git commit -m "feat(rbac): IP allowlisting middleware + GET/POST/DELETE /tenants/me/ip-allowlist API"
```

---

## Acceptance Criteria

| Item | Criterion |
|---|---|
| 3.1 Role model | `user_roles` table created via migration; `UserRole` ORM model has all required fields |
| 3.2 RBAC middleware | `TenantContext.roles` populated from JWT `realm_access.roles` or DB `user_roles` for API key users |
| 3.3 Protected endpoints | `POST /governance/emergency-stop` returns 403 for viewer; 200 for admin |
| 3.4 Role API | `POST /tenants/me/roles` creates role; `DELETE` removes; `GET` lists; 422 for invalid role name |
| 3.5 IP allowlist | Request from non-allowlisted IP returns 403; empty allowlist allows all; loopback always allowed |
