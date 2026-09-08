# Community 282

> 23 nodes · cohesion 0.15

## Key Concepts

- **SCIMHandler** (11 connections) — `agent-verse-backend/app/auth/scim_handler.py`
- **Any** (8 connections)
- **scim_handler.py** (7 connections) — `agent-verse-backend/app/auth/scim_handler.py`
- **_db_row_to_scim_user()** (7 connections) — `agent-verse-backend/app/auth/scim_handler.py`
- **_scim_error()** (7 connections) — `agent-verse-backend/app/auth/scim_handler.py`
- **require_scim_auth()** (6 connections) — `agent-verse-backend/app/auth/scim_handler.py`
- **.create_user()** (6 connections) — `agent-verse-backend/app/auth/scim_handler.py`
- **.get_user()** (5 connections) — `agent-verse-backend/app/auth/scim_handler.py`
- **.update_user()** (5 connections) — `agent-verse-backend/app/auth/scim_handler.py`
- **.list_users()** (4 connections) — `agent-verse-backend/app/auth/scim_handler.py`
- **.delete_user()** (3 connections) — `agent-verse-backend/app/auth/scim_handler.py`
- **._map_groups_to_role()** (3 connections) — `agent-verse-backend/app/auth/scim_handler.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/auth/scim_handler.py`
- **Request** (1 connections)
- **SCIM 2.0 user/group provisioning handler (RFC 7644). Handles automated user…** (1 connections) — `agent-verse-backend/app/auth/scim_handler.py`
- **SCIM 2.0 user/group provisioning. Constructed per-request with tenant_id…** (1 connections) — `agent-verse-backend/app/auth/scim_handler.py`
- **List tenant users in SCIM ListResponse format.** (1 connections) — `agent-verse-backend/app/auth/scim_handler.py`
- **Get a single user by SCIM external ID or internal DB id.** (1 connections) — `agent-verse-backend/app/auth/scim_handler.py`
- **Create a user from SCIM payload. Maps group memberships to roles via…** (1 connections) — `agent-verse-backend/app/auth/scim_handler.py`
- **Update a user (PUT = full replacement, PATCH = Operations list).** (1 connections) — `agent-verse-backend/app/auth/scim_handler.py`
- **Authenticate SCIM requests via pre-provisioned bearer token. Amendment 8.2:…** (1 connections) — `agent-verse-backend/app/auth/scim_handler.py`
- **Deprovision (soft-delete) a user.** (1 connections) — `agent-verse-backend/app/auth/scim_handler.py`
- **Convert a DB row (tuple or Row) to SCIM User resource.** (1 connections) — `agent-verse-backend/app/auth/scim_handler.py`

## Relationships

- [Enterprise API Surface](Enterprise_API_Surface.md) (4 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/auth/scim_handler.py`

## Audit Trail

- EXTRACTED: 45 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*