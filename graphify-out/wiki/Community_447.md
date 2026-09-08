# Community 447

> 14 nodes · cohesion 0.19

## Key Concepts

- **PolicyEngine (denied/approval tool patterns, time windows)** (23 connections) — `agent-verse-backend/app/governance/policies.py`
- **Policy** (11 connections) — `agent-verse-backend/app/governance/policies.py`
- **.evaluate()** (6 connections) — `agent-verse-backend/app/governance/policies.py`
- **.reload_from_db()** (6 connections) — `agent-verse-backend/app/governance/policies.py`
- **.subscribe_to_changes()** (5 connections) — `agent-verse-backend/app/governance/policies.py`
- **._is_within_time_window()** (4 connections) — `agent-verse-backend/app/governance/policies.py`
- **.web_allowed_domains()** (3 connections) — `agent-verse-backend/app/governance/policies.py`
- **.add_policy()** (2 connections) — `agent-verse-backend/app/governance/policies.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/governance/policies.py`
- **Returns True if current time (in policy.timezone) is within policy's allowed…** (1 connections) — `agent-verse-backend/app/governance/policies.py`
- **Evaluate tool access. parent_policy_ids allows sub-agents to inherit parent…** (1 connections) — `agent-verse-backend/app/governance/policies.py`
- **Reload policies from DB. If tenant_id given, reload only that tenant's…** (1 connections) — `agent-verse-backend/app/governance/policies.py`
- **Long-running coroutine: subscribe to policy_changes channel and reload on…** (1 connections) — `agent-verse-backend/app/governance/policies.py`
- **Evaluates tool calls against a set of policies. Policies are intentionally…** (1 connections) — `agent-verse-backend/app/governance/policies.py`

## Relationships

- [Community 72](Community_72.md) (7 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (6 shared connections)
- [Scope & Role Seeding](Scope_&_Role_Seeding.md) (6 shared connections)
- [Scaling & Autoscale Metrics](Scaling_&_Autoscale_Metrics.md) (2 shared connections)
- [Community 102](Community_102.md) (2 shared connections)
- [Org Department Memory](Org_Department_Memory.md) (2 shared connections)
- [Persistence & Retry Services](Persistence_&_Retry_Services.md) (2 shared connections)
- [Community 488](Community_488.md) (2 shared connections)
- [Reliability & Audit](Reliability_&_Audit.md) (1 shared connections)
- [Community 297](Community_297.md) (1 shared connections)
- [Artifacts API & Coordination](Artifacts_API_&_Coordination.md) (1 shared connections)
- [Federated RAG Search](Federated_RAG_Search.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/governance/policies.py`

## Audit Trail

- EXTRACTED: 41 (82%)
- INFERRED: 9 (18%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*