# Coordination delivery lag
## Symptoms
Outbox lag is sustained above 30 seconds or replay cursors stop advancing.
## Impact
Accepted work remains durable, but clients receive delayed events.
## Dashboard queries
Inspect `agentverse_coordination_outbox_lag_seconds` and outbox publish errors.
## Diagnosis
Check PostgreSQL outbox age, Redis health, publisher replicas, and dead letters.
## Mitigation
Keep admissions bounded; restore publishers or temporarily use PostgreSQL polling.
## Recovery
Drain the canonical outbox in sequence and restart consumers from their cursor.
## Verification
Confirm lag returns below five seconds with no sequence gaps or duplicates.
## Escalation
Page the platform owner if lag persists for 15 minutes.
## Rollback
Disable live fan-out, retain REST/SSE replay, and route to the previous color.
