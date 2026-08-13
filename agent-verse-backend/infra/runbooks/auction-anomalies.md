# Auction anomalies
## Symptoms
No-bid, invalid signature, fairness, or settlement failures increase.
## Impact
Tasks remain unallocated or may require deterministic fallback.
## Dashboard queries
Inspect bid and allocation failures plus active winner leases.
## Diagnosis
Verify deadline, sealed-bid key version, signatures, scores, and identity clusters.
## Mitigation
Stop unseal, quarantine suspicious bidders, and use the fallback allocator.
## Recovery
Rebid with a new round and fencing epoch after authorization.
## Verification
Confirm one winner, reproducible scoring, and settled lease ownership.
## Escalation
Page security for collusion or signature anomalies.
## Rollback
Disable auction allocation and retain opaque bid evidence.
