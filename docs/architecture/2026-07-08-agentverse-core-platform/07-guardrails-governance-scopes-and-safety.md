# Guardrails, Governance, Scopes, and Safety

**Date:** 2026-07-08

## Purpose

This document explains all safety, guardrail, governance, tenant isolation, RBAC, scope, audit, cost, and human approval systems in AgentVerse.

## Safety Layers

```text
API authentication
  -> tenant context
  -> API key scopes
  -> RBAC roles
  -> DB row-level security
  -> permission matrix
  -> policy engine
  -> tool risk classification
  -> guardrail checker
  -> HITL approval
  -> output PII scan
  -> audit log
```

## Tenant Isolation

`TenantContext` carries:

```python
tenant_id
plan
api_key_id
roles
```

Every DB operation uses tenant-aware RLS context.

## Row-Level Security

Postgres RLS enforces tenant isolation using `app.tenant_id` session variable.

All tenant data is scoped by `tenant_id`:

```text
goals
agents
knowledge collections
memory
audit logs
connectors
policies
cost ledger
```

## API Key Scopes

API keys can have scopes such as:

```text
goals:create
goals:read
agents:write
knowledge:admin
governance:admin
connectors:write
```

These limit what a key can do even within the same tenant.

## RBAC Roles

Roles are stored in `TenantContext.roles`. Higher roles can imply lower roles depending on role hierarchy.

Typical roles:

```text
viewer
operator
admin
owner
compliance_admin
```

## Agent Collection Scopes

Agents can be bound to specific knowledge collections:

```python
allowed_collection_ids = ["finance-policy", "pci-docs"]
```

`smart_context_fetch()` respects this and skips collections outside the agent's scope.

## Permission Matrix

Implemented in `app/governance/permissions.py`.

Action levels:

```text
ALLOW       -> execute silently
ALLOW_LOG   -> execute and audit
APPROVAL    -> request human approval
DENY        -> block immediately
```

Default for unconfigured tools is `ALLOW_LOG`.

## Policy Engine

Implemented in `app/governance/policies.py`.

Policy fields:

```python
Policy(
    denied_tools=[...],
    approval_tools=[...],
    allowed_hours_utc=(9, 18),
    allowed_weekdays=[0,1,2,3,4],
    timezone="Asia/Kolkata",
    scope="global",
)
```

Results:

```text
ALLOW
DENY
REQUIRE_APPROVAL
```

Regulated domains fail closed:

```text
healthcare
hippa
legal
finance
sox
fintech
pci
```

## Tool Risk Classification

Implemented in `app/agent/tool_risk.py`.

Risk levels:

```text
read
write_low
write_high
unknown
```

Examples:

```text
jira_search_issues -> read
jira_add_comment -> write_low
jira_create_issue -> write_high
jira_delete_issue -> destructive
stripe_* -> write_high minimum
```

## HITL Gateway

Implemented in `app/governance/hitl.py`.

Human approval states:

```text
pending
approved
rejected
```

Dual mode:

```text
in-process asyncio.Event for tests/local
Redis BLPOP for cross-replica production approval
```

Workflow:

```text
high-risk tool
  -> request_approval
  -> waiting_approval event
  -> user approves in UI
  -> publish_resolution
  -> worker resumes
```

## Guardrail Checker

Implemented in `app/intelligence/guardrails.py`.

Checks:

1. prompt injection
2. base64-encoded injection
3. ROT13 injection
4. Unicode homoglyph injection
5. dangerous command patterns
6. PII leakage
7. tool hallucination
8. output leakage

Dangerous patterns include:

```text
rm -rf
truncate table
delete from
mkfs
format C:
```

## Tool Name Validation

Implemented in `app/agent/tool_calls.py`.

Prevents tool hallucination:

```text
allowed_tools = {"jira_search_issues", "jira_get_issue"}
tool_call = "getTeamworkGraphContext"
-> rejected as TOOL NOT AVAILABLE
```

Suffix matching supports:

```text
Jira Connector.jira_search_issues -> jira_search_issues
```

## Argument Validation

Tool arguments are validated against JSON schema before MCP dispatch:

```text
missing required field -> ARGUMENT VALIDATION FAILED
unknown field -> ARGUMENT VALIDATION FAILED
```

## Grounding Check

Implemented in `app/agent/grounding.py`.

Extracts claims:

```text
Jira IDs
PR numbers
URLs
dates
numbers
emails
quoted strings
```

Then verifies they appear in tool outputs.

Structured tool outputs are skipped because the output itself is the evidence.

## Exfiltration Guard

`app/agent/exfil_guard.py` detects attempts to send secrets/data to external places.

Useful for:

- preventing prompt-injected data leaks
- blocking webhook exfiltration
- protecting tenant secrets

## Audit Trail

Implemented in `app/governance/audit.py`.

AuditEvent fields:

```python
goal_id
outcome
step_id
approver
note
ip_address
user_agent
api_key_id
request_id
connector_id
```

Audit logs are append-only and persisted to Postgres when DB is configured.

## Cost Controller

Implemented in `app/governance/cost.py`.

Default budget:

```text
per_goal_usd = 10.0
per_tenant_daily_usd = 500.0
```

Checks happen before or after cost-incurring operations.

Redis counters provide distributed cost enforcement across replicas.

## Combined Safety Workflow

```text
Executor proposes tool call
  -> validate tool name
  -> validate arguments
  -> permission matrix
  -> policy engine
  -> tool risk classifier
  -> HITL if required
  -> execute tool
  -> scan output for PII
  -> grounding check
  -> audit log
```

## Real Use Case: Finance Domain

Goal:

```text
Refund last 5 failed payments.
```

Safety path:

```text
Stripe connector -> high-risk connector
refund action -> write_high
finance domain -> regulated domain
PolicyEngine -> REQUIRE_APPROVAL
HITLGateway -> waits for compliance officer approval
AuditLog -> records approver and outcome
```
