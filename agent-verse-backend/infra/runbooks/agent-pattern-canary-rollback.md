# Agent pattern canary rollback
## Symptoms
The canary gate returns hold or rollback, or quality/cost/latency breaches after switching.
## Impact
New pattern traffic is paused while accepted work remains durable and replayable.
## Dashboard queries
Compare strategy quality, p95/p99, cost, denials, outbox lag, and lease reclaims by color.
## Diagnosis
Validate evidence digests, adapter pins, sample size, observation window, and security events.
## Mitigation
Set the family kill switch and stop new admissions to the canary color.
## Recovery
Route service traffic to the previous warm color and replay accepted checkpoints.
## Verification
Confirm no sequence gaps, duplicate effects, evidence deletion, or schema downgrade.
## Escalation
The named release owner and security owner approve re-entry after any critical breach.
## Rollback
Run `NAMESPACE=<namespace> infra/k8s/switch-traffic.sh <previous-color>` with signed evidence.
