# Coordination lease reclaims
## Symptoms
Claim failures, expiry, or fencing rejections rise above baseline.
## Impact
Work may be delayed while stale owners are fenced and tasks are reclaimed.
## Dashboard queries
Inspect claim failure rate and `agentverse_coordination_active_leases`.
## Diagnosis
Compare worker heartbeats, lease TTL, clock skew, and fencing tokens.
## Mitigation
Stop unhealthy workers and allow only the highest fencing token to write.
## Recovery
Reclaim expired leases through the canonical coordinator.
## Verification
Confirm one active owner per claim and no duplicate authority transition.
## Escalation
Page reliability when reclaim loops exceed two lease periods.
## Rollback
Disable the affected pattern and drain accepted work on stable workers.
