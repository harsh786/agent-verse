# Community 253

> 25 nodes · cohesion 0.09

## Key Concepts

- **org/rbac.py** (10 connections) — `agent-verse-backend/app/org/rbac.py`
- **OrgRBACGuard** (5 connections) — `agent-verse-backend/app/org/rbac.py`
- **AgentAnomalyDetector** (4 connections) — `agent-verse-backend/app/org/rbac.py`
- **sign_cross_dept_request()** (4 connections) — `agent-verse-backend/app/org/rbac.py`
- **verify_cross_dept_request()** (4 connections) — `agent-verse-backend/app/org/rbac.py`
- **OrgRole** (3 connections) — `agent-verse-backend/app/org/rbac.py`
- **.is_at_least()** (3 connections) — `agent-verse-backend/app/org/rbac.py`
- **.record_tool_call()** (2 connections) — `agent-verse-backend/app/org/rbac.py`
- **.check_department_access()** (2 connections) — `agent-verse-backend/app/org/rbac.py`
- **.require()** (2 connections) — `agent-verse-backend/app/org/rbac.py`
- **.require_min_role()** (2 connections) — `agent-verse-backend/app/org/rbac.py`
- **.can()** (2 connections) — `agent-verse-backend/app/org/rbac.py`
- **Any** (2 connections)
- **require_org_role()** (2 connections) — `agent-verse-backend/app/org/rbac.py`
- **.__init__()** (1 connections) — `agent-verse-backend/app/org/rbac.py`
- **PART 18 — Org-scoped RBAC Middleware. Org roles (per spec): org_admin — full…** (1 connections) — `agent-verse-backend/app/org/rbac.py`
- **Verify actor can access a specific department.** (1 connections) — `agent-verse-backend/app/org/rbac.py`
- **Every inter-dept message is signed (PART 18: Cross-dept request signing).…** (1 connections) — `agent-verse-backend/app/org/rbac.py`
- **Verify inter-dept request signature (max 5 minutes old).** (1 connections) — `agent-verse-backend/app/org/rbac.py`
- **Detect tool calls outside role profile → alert.** (1 connections) — `agent-verse-backend/app/org/rbac.py`
- **Record a tool call. Returns True if it's within profile, False if anomalous.** (1 connections) — `agent-verse-backend/app/org/rbac.py`
- **Check if role has at least the privileges of minimum_role.** (1 connections) — `agent-verse-backend/app/org/rbac.py`
- **FastAPI dependency that enforces org-level RBAC. Usage:…** (1 connections) — `agent-verse-backend/app/org/rbac.py`
- **# TODO: integrate with auth system — read role from request.state** (1 connections) — `agent-verse-backend/app/org/rbac.py`
- **Programmatic RBAC guard for use inside service methods. Usage: guard =…** (1 connections) — `agent-verse-backend/app/org/rbac.py`

## Relationships

- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/org/rbac.py`

## Audit Trail

- EXTRACTED: 30 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*