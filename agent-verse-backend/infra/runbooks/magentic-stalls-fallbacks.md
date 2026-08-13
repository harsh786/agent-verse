# Magentic stalls and fallbacks
## Symptoms
Ledger stall counters or fallback events rise for Magentic sessions.
## Impact
Plans stop progressing or consume extra model calls.
## Dashboard queries
Inspect ledger failures, fallback rate, rounds, and latency.
## Diagnosis
Review safe ledger facts, blockers, next actor, and checkpoint versions.
## Mitigation
Request bounded human review or switch to the certified fallback.
## Recovery
Resume from the last accepted ledger revision.
## Verification
Confirm revisions advance and completed work is not repeated.
## Escalation
Escalate repeated stalls after the configured maximum.
## Rollback
Disable Magentic selection while preserving ledger evidence.
