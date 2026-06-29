# Audit Log

## Overview

The audit log is an append-only record of every governed action taken within AgentVerse. "Append-only" is a structural guarantee, not a policy: the `AuditLog` class in `app/governance/audit.py` exposes no `update()` or `delete()` method. In production, the backing PostgreSQL table has an immutability trigger that rejects `UPDATE` and `DELETE` statements at the database layer.

The `AuditExplorerPage` at `/audit` provides a query interface with time-range, tool-name, and outcome filters, plus CSV/JSON export for offline analysis.

---

## Dual-Write Architecture

Every `AuditLog.record()` call follows two paths simultaneously:

### Path 1: In-memory (fast)

```python
self._log.setdefault(tenant_ctx.tenant_id, []).append(event)
```

The event is appended to an in-process list keyed by `tenant_id`. This write is synchronous and returns immediately, so the agent loop is never blocked by I/O.

### Path 2: PostgreSQL (durable)

```python
loop.create_task(self._db_record(event, tenant_ctx.tenant_id))
```

An `asyncio.Task` is created fire-and-forget. If the database write fails, the exception is caught, logged as a warning, and never re-raised to the caller. The agent continues executing. If the pod crashes before the task completes, the event may be lost from PostgreSQL — but by that point the agent loop has already proceeded, so blocking on the DB write would not have helped.

In practice, DB write failures are extremely rare and are surfaced via metrics and log alerts. For high-assurance deployments, a synchronous dual-write mode is available via feature flag.

```mermaid
sequenceDiagram
    participant Loop as AgentLoop
    participant AL as AuditLog
    participant Mem as In-Memory List
    participant Task as asyncio.Task
    participant PG as PostgreSQL

    Loop->>AL: record(event, tenant_ctx)
    AL->>Mem: append(event)       # synchronous, instant
    AL->>Task: create_task(_db_record)
    AL-->>Loop: return (immediate)
    Task->>PG: INSERT audit_log ... # async, background
    PG-->>Task: OK
```

---

## `AuditEvent` Schema

```python
@dataclass
class AuditEvent:
    goal_id: str
    tool_name: str
    action_level: ActionLevel    # allow | deny | require_approval
    outcome: str                 # "allowed" | "denied" | "approved" | "rejected" | "error"
    step_id: str = ""
    approver: str | None = None
    note: str = ""
    event_id: str                # UUID hex, auto-generated
    # SOC2-required fields
    ip_address: str | None = None
    user_agent: str | None = None
    api_key_id: str | None = None
    request_id: str | None = None
    connector_id: str | None = None
    auth_type: str | None = None
```

### SOC2-required fields

These six fields satisfy the attribution requirements for a SOC2 Type II audit:

| Field | Purpose |
|---|---|
| `ip_address` | Network provenance — where did the request originate? |
| `user_agent` | Client identification — which SDK/browser version? |
| `api_key_id` | Key-level attribution — which credential was used? |
| `request_id` | Distributed tracing — correlate with OTEL spans |
| `connector_id` | MCP connector identification — which external system was invoked? |
| `auth_type` | Authentication method — `api_key`, `jwt`, `oauth` |

These fields are populated by the middleware layer before the event reaches the audit recorder, so individual tool implementations do not need to carry request metadata.

---

## Querying the Audit Log

### Via the Audit Explorer UI

The `AuditExplorerPage` provides a filter form with:

- **Tool name** — exact or partial match (passed to the backend, which performs a `LIKE` query)
- **Start time / End time** — ISO-8601 datetime pickers
- **Outcome** — client-side substring filter across the outcome column
- **Free-text search** — client-side full-payload JSON string search (runs on already-fetched data)

Results are capped at 200 rows per query. For larger exports, use the CSV/JSON download buttons.

### Via the API

```
GET /governance/audit
X-API-Key: <key>
Query parameters:
  goal_id      (optional) filter by specific goal
  tool_name    (optional) filter by tool name
  start_time   (optional) ISO-8601 lower bound
  end_time     (optional) ISO-8601 upper bound
  limit        (default 50, max 200)
  offset       (default 0) for pagination

Response 200:
[
  {
    "event_id": "abc123",
    "goal_id": "goal_xyz",
    "tool_name": "github.create_pr",
    "action_level": "allow",
    "outcome": "allowed",
    "step_id": "step_001",
    "approver": null,
    "note": "",
    "ip_address": "10.0.1.5",
    "api_key_id": "key_def456",
    "request_id": "req_ghi789"
  }
]
```

---

## CSV / JSON Export

The `AuditExplorerPage` implements client-side export — no additional API call is needed:

```typescript
const CSV_COLUMNS: (keyof AuditEvent)[] = [
  "event_id", "goal_id", "tool_name", "action_level", "outcome", "approver", "note"
];

function toCsv(rows: AuditEvent[]): string {
  const header = CSV_COLUMNS.join(",");
  const body = rows.map(r =>
    CSV_COLUMNS.map(c => `"${String(r[c] ?? "").replace(/"/g, '""')}"`)
               .join(",")
  ).join("\n");
  return `${header}\n${body}`;
}
```

JSON export includes the full event payload including SOC2 fields. CSV export uses a fixed column set for easier import into SIEM tools and spreadsheets.

For larger exports (beyond 200 rows), use the compliance export endpoint which bundles the full audit history:

```
POST /compliance/export/soc2
```

---

## Audit Integrity Verification

The `action_level` field encodes a hash chain anchor. Each row in the `audit_log` PostgreSQL table includes an `integrity_hash` column computed as:

```
sha256(event_id || goal_id || tool_name || outcome || previous_hash)
```

where `previous_hash` is the `integrity_hash` of the most recent prior row for the same tenant. This forms a Merkle-style chain: tampering with any row breaks the hash of all subsequent rows.

To verify integrity:

```
POST /governance/audit/verify
X-API-Key: <admin_key>

Response 200:
{
  "chain_valid": true,
  "events_verified": 15024,
  "earliest_event": "2026-01-01T00:00:00Z",
  "latest_event": "2026-06-29T10:00:00Z"
}

Response 200 (tamper detected):
{
  "chain_valid": false,
  "first_broken_event_id": "abc123",
  "events_after_break": 142
}
```

---

## `ActionLevel` Values

`ActionLevel` is defined in `app/governance/permissions.py`:

| Value | Meaning |
|---|---|
| `allow` | Tool was permitted and executed |
| `deny` | Tool was blocked by policy |
| `require_approval` | Tool was paused for human review |

The `outcome` field carries the final result after any approval process completes:

| Outcome | Meaning |
|---|---|
| `allowed` | Executed successfully |
| `denied` | Blocked by `PolicyEngine` |
| `approved` | Paused, then approved by a human |
| `rejected` | Paused, then rejected by a human |
| `timed_out` | Paused, approval timeout expired |
| `error` | Execution failed with an exception |

---

## In-Memory Query Interface

For tests and local development without PostgreSQL, `AuditLog.query()` runs against the in-memory store:

```python
def query(
    self,
    *,
    tenant_ctx: TenantContext,
    goal_id: str | None = None,
    tool_name: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AuditEvent]:
```

Filters are applied in Python. This makes unit tests for audit assertions trivial — no database mock required.

---

## Retention and Immutability

The PostgreSQL table has a row-level trigger:

```sql
CREATE OR REPLACE FUNCTION audit_log_immutability()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'UPDATE' OR TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'audit_log is append-only — modifications are not permitted';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_immutability_trigger
BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION audit_log_immutability();
```

Records are never deleted from the live audit table. Retention is handled by archiving rows older than the configured window to cold storage (S3/GCS), not by deleting them. The archived rows remain available for compliance queries via the archive retrieval endpoint.

The only exception is GDPR right-to-erasure, which deletes the `audit_log` table rows for the subject tenant as part of the 27-table cascade — see [06-compliance.md](./06-compliance.md). This is a legally mandated exception to the immutability rule and is recorded in the `deleted_tenants` table as an audit-of-the-audit.
