# AgentVerse Operations Runbook

## Strategy Runtime v2

For a shadow mismatch, compare the recorded legacy and v2 strategy, topology, readiness,
expected cost, and latency fields. Shadow mode never dispatches v2.

For a readiness failure, inspect stable dependency reason codes and restore the dependency;
production admission fails closed. Evidence expiry or adapter-version changes demote the derived
state until matching evidence is recorded again.

Canary expansion is tenant allowlist based. Expand only after restart, policy, cost, latency,
load, and canary evidence remains current. To stop new admissions, set
`STRATEGY_RUNTIME_V2_KILL_SWITCH=true`; already admitted executions may finish or cancel.

Rollback by removing tenant IDs from `STRATEGY_RUNTIME_V2_TENANT_ALLOWLIST`. If schema rollback
is necessary and no later migration depends on it, run:

```bash
uv run alembic downgrade 0095_raft_lifecycle
```

Never change the adapter version of an accepted execution during rollback.
