# Agent pattern runaway limits
## Symptoms
Tokens, fan-out, duration, or cost accelerate toward hard ceilings.
## Impact
Tenant budgets and worker capacity may be exhausted.
## Dashboard queries
Inspect token and cost rates by bounded strategy and family.
## Diagnosis
Check selected profile, limits, recursion, retries, and fallback loops.
## Mitigation
Activate the family kill switch and stop new admissions.
## Recovery
Cancel descendants, release leases, and reconcile reserved budget.
## Verification
Confirm active work and spend return to the approved envelope.
## Escalation
Notify governance for any limit bypass or approval anomaly.
## Rollback
Pin the previous adapter version and preserve accepted checkpoints.
