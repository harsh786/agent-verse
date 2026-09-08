# Community 227

> 27 nodes · cohesion 0.09

## Key Concepts

- **TenantUserService (invite/accept/role mgmt)** (13 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- **TenantUser** (7 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- **TenantInvite** (6 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- **TenantRole enum (tenant_admin/org_admin/org_member/org_viewer/approver/billing_admin)** (5 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- **.invite_user()** (4 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- **.accept_invite()** (3 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- **.__init__()** (3 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- **.has_permission()** (2 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- **.change_role()** (2 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- **.get_user()** (2 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- **.get_user_by_email()** (2 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- **.list_pending_invites()** (2 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- **.list_users()** (2 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- **Any** (1 connections)
- **AsyncSession** (1 connections)
- **StrEnum** (1 connections)
- **QA1 — Tenant user management service. Backed by in-memory store (production: DB…** (1 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- **Create an invite and send a magic link email. POST /v1/tenants/users/invite** (1 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- **Accept an invitation and create the user account.** (1 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- **Per spec QA1 — full tenant user record.** (1 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- **Check if user has a permission (global or for specific org).** (1 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- **Pending invitation — expires after 7 days.** (1 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- **.is_expired()** (1 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- **.magic_link()** (1 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- **.remove_user()** (1 connections) — `agent-verse-backend/app/tenancy/tenant_users.py`
- *... and 2 more nodes in this community*

## Relationships

- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (4 shared connections)
- [Community 384](Community_384.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/tenancy/tenant_users.py`

## Audit Trail

- EXTRACTED: 35 (97%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 1 (3%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*