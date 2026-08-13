# Tenant policy anomalies
## Symptoms
Authorization, RLS, or policy denials deviate from baseline.
## Impact
Legitimate work may stop; an isolation bypass is a critical incident.
## Dashboard queries
Inspect policy denial rate, auth failures, and RLS audit events.
## Diagnosis
Compare policy version, actor scopes, tenant context, and feature flags.
## Mitigation
Fail closed, revoke affected credentials, and freeze policy propagation.
## Recovery
Deploy the last approved policy and rotate exposed credentials.
## Verification
Run cross-tenant negative tests and verify the audit chain.
## Escalation
Page security immediately for any cross-tenant observation.
## Rollback
Restore the signed policy version without deleting audit evidence.
