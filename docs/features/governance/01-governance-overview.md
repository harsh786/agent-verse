# Governance Overview

## Why AI Agents Need Governance

Autonomous agents are qualitatively different from traditional software. A conventional API endpoint takes one action per call. An agent given "deploy the new release and clean up the old database tables" may issue dozens of tool calls, some irreversible, before a human has any awareness that work is in progress. Without governance infrastructure, the blast radius of a misunderstood goal or a compromised API key is unbounded.

AgentVerse governance addresses four failure modes:

| Failure mode | Governance control |
|---|---|
| Agent calls a destructive tool it shouldn't | PolicyEngine DENY |
| High-risk step needs a human decision | HITL Gateway REQUIRE_APPROVAL |
| LLM costs run out of control | CostController budget cap |
| Incident needs forensic traceability | Append-only AuditLog |

All four controls are surfaced on a single `GovernancePage` (`/governance`) with tabs for **Policies**, **Approvals**, **Audit**, and **Budget**, plus an always-visible **Emergency Stop** banner.

---

## The Four Governance Subsystems

### 1. PolicyEngine — what tools may run

`app/governance/policies.py` defines a `PolicyEngine` that evaluates every tool call before execution. Each `Policy` carries:

- `denied_tools` — list of glob patterns that produce `DENY`
- `approval_tools` — list of glob patterns that produce `REQUIRE_APPROVAL`
- `allowed_hours_utc` — optional `(start_hour, end_hour)` tuple (e.g. `(9, 17)`)
- `allowed_weekdays` — optional list of ISO weekday integers (0 = Monday, 6 = Sunday)
- `timezone` — IANA timezone name (default `"UTC"`) used for time-window evaluation

Three possible results are defined in `PolicyResult`:

```
ALLOW            → tool call proceeds immediately
DENY             → tool call is blocked; goal step fails with governance error
REQUIRE_APPROVAL → tool call is suspended until a human approves or rejects
```

### 2. HITLGateway — human-in-the-loop approvals

`app/governance/hitl.py` implements an async gateway that suspends agent execution via `asyncio.Event.wait()`. The default timeout is **300 seconds (5 minutes)**, after which the request transitions to `TIMED_OUT` and the step is treated as rejected.

### 3. AuditLog — append-only trail

`app/governance/audit.py` maintains an in-memory log (fast path) with dual-write to PostgreSQL (durable path) via a fire-and-forget `asyncio.create_task()`. There is no `update` or `delete` method — immutability is structural.

### 4. CostController — spend budgets

`app/governance/cost.py` enforces two independent budget limits per call to `check_and_record()`:

- `per_goal_usd` (default `$10.00`) — cumulative LLM cost for a single goal run
- `per_tenant_daily_usd` (default `$500.00`) — rolling daily total across all goals for a tenant

In production, totals are stored in Redis with a daily TTL that resets at UTC midnight. Cross-replica accuracy is guaranteed by per-tenant `asyncio.Lock` guards on write paths.

---

## Emergency Stop

The **Emergency Stop** is a prominent red button pinned to the top of the Governance page. It is always visible regardless of which tab is active.

Activating Emergency Stop via `POST /governance/emergency-stop` performs three actions atomically:

1. **Cancels all running goals** for the tenant — in-flight LangGraph steps are aborted
2. **Rejects all pending HITL approval requests** — any agent waiting on `asyncio.Event.wait()` is unblocked with `REJECTED` status
3. **Publishes a stop signal to all replicas** via Redis pub/sub so that multi-replica deployments halt consistently, not just the replica that received the HTTP request

After activation, the banner changes to a red warning that shows the count of cancelled goals and rejected approvals. A `DELETE /governance/emergency-stop` clears the stop flag and resumes normal operation.

```
Emergency Stop active: 3 goals cancelled · 7 approvals rejected
                        [Clear Emergency Stop]
```

---

## Budget Tab

The Budget tab exposes the `BudgetConfig` for the tenant via `GET /governance/budget` and `PUT /governance/budget`.

| Field | Default | Meaning |
|---|---|---|
| `per_goal_usd` | `$10.00` | Max LLM spend for a single goal execution |
| `per_tenant_daily_usd` | `$500.00` | Max total LLM spend per tenant per UTC day |

When either limit is exceeded, `CostController.check_and_record()` returns `False` and the agent loop halts with a `BudgetExceeded` error. This surfaces in the goal status as `failed: budget_exceeded` and fires a notification if a channel is configured.

---

## Policy Simulation

Before activating a new policy, operators can simulate it against a hypothetical tool call without touching live execution. The simulation endpoint evaluates the current policy set and returns the verdict without side effects — no audit record is written, no agent is paused.

```
POST /governance/policies/simulate
{
  "tool_name": "postgres.execute_query",
  "goal_id":   "goal_abc123"
}

Response:
{
  "result": "require_approval",
  "matched_policy": "prod-db-approvals",
  "pattern": "postgres.*"
}
```

---

## Request Flow: PolicyEngine Evaluation

The following sequence shows the path from a planned agent step to tool execution, with PolicyEngine acting as the gate.

```mermaid
sequenceDiagram
    participant Loop as AgentLoop
    participant PE as PolicyEngine
    participant HITL as HITLGateway
    participant Tool as ToolExecutor

    Loop->>PE: evaluate(tool_name, tenant_ctx)
    alt No policy matches
        PE-->>Loop: ALLOW
        Loop->>Tool: execute()
        Tool-->>Loop: result
    else Tool in denied_tools
        PE-->>Loop: DENY
        Loop-->>Loop: mark step failed
    else Tool in approval_tools
        PE-->>Loop: REQUIRE_APPROVAL
        Loop->>HITL: request_approval(goal_id, action, risk_level)
        HITL-->>Loop: ApprovalRequest (pending)
        Loop->>HITL: wait_for_approval(request_id, timeout=300s)
        alt Human approves
            HITL-->>Loop: APPROVED
            Loop->>Tool: execute()
            Tool-->>Loop: result
        else Human rejects or timeout
            HITL-->>Loop: REJECTED / TIMED_OUT
            Loop-->>Loop: mark step failed
        end
    end
```

---

## Cross-cutting Concerns

### Tenant isolation

All governance operations are scoped to `tenant_ctx.tenant_id`. The PostgreSQL `governance_policies`, `approval_requests`, and `audit_log` tables have Row-Level Security policies enforced via the `app.tenant_id` GUC set inside every transaction. In-memory data structures are keyed on `(tenant_id, ...)` tuples.

### Regulated domain fail-closed

For tenants operating in regulated industries (`healthcare`, `hipaa`, `legal`, `finance`, `sox`, `fintech`, `pci`), `evaluate_with_domain_failsafe()` returns `REQUIRE_APPROVAL` when no explicit policy matches. This "fail-closed" posture means the default is to require a human decision, not to allow.

### Policy subscriber — cross-replica consistency

When a policy is created or deleted on any replica, `PolicyEngine.publish_change()` writes a JSON message to the Redis `policy_changes` channel. A background `asyncio.Task` created by `start_policy_subscriber()` at lifespan startup listens on this channel and calls `reload_from_db()` on every other replica, ensuring all instances converge to the same policy set within milliseconds.

---

## API Quick Reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/governance/policies` | List all policies for the tenant |
| `POST` | `/governance/policies` | Create a new policy |
| `DELETE` | `/governance/policies/:id` | Delete a policy |
| `GET` | `/governance/approvals` | List approval requests |
| `POST` | `/governance/approvals/:id/approve` | Approve a request |
| `POST` | `/governance/approvals/:id/reject` | Reject a request |
| `GET` | `/governance/audit` | Query audit log |
| `GET` | `/governance/budget` | Get budget config |
| `PUT` | `/governance/budget` | Update budget config |
| `POST` | `/governance/emergency-stop` | Activate emergency stop |
| `DELETE` | `/governance/emergency-stop` | Clear emergency stop |

See individual feature pages for full request/response schemas.
