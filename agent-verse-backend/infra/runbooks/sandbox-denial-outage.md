# Sandbox denial outage
## Symptoms
Code executions are denied or unavailable above baseline.
## Impact
PoT and CodeAct cannot execute governed programs.
## Dashboard queries
Inspect sandbox denial rate, latency, and runner readiness.
## Diagnosis
Check policy bundle, image digest, egress rules, quotas, and runner health.
## Mitigation
Fail closed and route eligible goals to non-code reasoning.
## Recovery
Restore a certified runner and rerun its escape/resource suite.
## Verification
Confirm only authorized images execute and artifacts remain sanitized.
## Escalation
Page security immediately for any suspected sandbox escape.
## Rollback
Disable code strategies and pin the last certified runner digest.
