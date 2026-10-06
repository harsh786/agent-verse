# Kubernetes redeploy checklist (QA findings that are cluster settings, not code)

Written 2026-10-06 after a QA run on a cluster that was still on an older build.
Everything below needs a change on the cluster; the code already behaves correctly.

## 1. One vault key on every pod (QA #1: "vault key mismatch", goals fail)

Symptom: `TenantProviderError: Tenant LLM API key could not be decrypted: vault key
mismatch ... fingerprint 36c61aa94232454c`. `36c6…` is the fingerprint of the built-in
public default key, so those pods have **no** `VAULT_MASTER_KEY`.

Current chart behaviour: every app workload (API, worker, workflow-worker,
subgoal-worker, schedule-worker, beat) gets `VAULT_MASTER_KEY` from the one secret, and
outside development a pod on the default key refuses to start. Pods on `36c6…` are
therefore running an older build or an overridden `ENVIRONMENT`.

1. Redeploy **all** workloads from the current chart with a single
   `secrets.vaultMasterKey` (the real key — the one behind fingerprint `3a9d…`).
2. Keep `secrets.vaultPreviousMasterKeys=dev-insecure-master-key` until every tenant key
   saved under the default key has been re-saved, so they can still be opened.
3. Verify every pod holds the same key (compare hashes, never print the key):
   `kubectl exec <pod> -- sh -c 'echo -n "$VAULT_MASTER_KEY" | sha256sum'`
4. Re-save a tenant LLM key and run a small goal.

## 2. Model Registry admin access in production (QA #2)

A tenant admin can change the global model registry only when its tenant is listed in
`PLATFORM_ADMIN_TENANT_IDS` (chart value `platformAdminTenantIds`; `*` = any tenant's
admins). With it unset, production refuses tenant admins and accepts only the platform
admin key (`X-Admin-Key`). The same rule now governs the `/admin` page.

## 3. Signed A2A tasks (QA #18)

The API refuses unsigned agent-to-agent tasks (503) only when `ENVIRONMENT=production`.
Set `secrets.a2aSharedSecret` (chart) so signed tasks are accepted, and run the cluster
with `ENVIRONMENT=production`. Production also refuses to start without a platform LLM
provider — check that before flipping the setting (or set `LLM_REQUIRE_PLATFORM_KEY=false`
for a BYOK-only deployment).
