# Kubernetes redeploy checklist (QA findings that are cluster settings, not code)

Written 2026-10-06 after a QA run on a cluster that was still on an older build.
Sections 1-3 need a change on the cluster only; sections 4-5 need this build plus the cluster settings shown.

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

## 4. S3 / MinIO sync fails: "SSRF guard [s3]: metadata service hostname '169.254.169.254' blocked" (BUG A)

Symptom: a Source's health check (API) is green, but its sync in the Celery worker fails
with `s3: HTTPClientError: ... SSRF guard [s3]: metadata service hostname
'169.254.169.254' blocked`.

Root cause: the worker could not decrypt the Source's stored credentials (they are saved
`enc:v1:` with the API's `VAULT_MASTER_KEY`; a worker on another key — see section 1 —
cannot open them). The store blanked the value, the S3 connector then built its boto3
client with `aws_access_key_id=None`, and botocore fell back to its default credential
chain, which ends at the EC2 instance metadata service (the SSRF guard rightly blocked
it; without the guard the tenant's requests would have been signed with the node's IAM
role).

Fixed in code (this build and later):
- tenant S3 / MinIO / Kinesis clients come from an isolated botocore session with no
  credential providers: the Source's own keys, or an explicitly anonymous (UNSIGNED)
  client — never env vars, `~/.aws`, the container endpoint or IMDS;
- a worker that cannot decrypt a Source's credentials fails the sync with
  "the source's stored credentials (credentials) could not be decrypted on this server
  ... Give every pod (API and all workers) the same VAULT_MASTER_KEY, or re-enter the
  credentials" instead of connecting anonymously;
- the chart sets `AWS_EC2_METADATA_DISABLED=true` on every app workload (defence in depth).

Cluster steps:
1. Redeploy every workload from a build that contains the fix (same image tag everywhere):
   ```bash
   helm upgrade <RELEASE> agent-verse-backend/infra/helm/agentverse -n <NAMESPACE> --reuse-values \
     --set backend.image.tag=<IMAGE_TAG> --set worker.image.tag=<IMAGE_TAG> \
     --set subgoalWorker.image.tag=<IMAGE_TAG> --set scheduleWorker.image.tag=<IMAGE_TAG> \
     --set maintenanceWorker.image.tag=<IMAGE_TAG> --set beat.image.tag=<IMAGE_TAG>
   ```
2. Make sure every pod holds the SAME vault key (section 1). Compare hashes, never print
   the key:
   ```bash
   for p in $(kubectl -n <NAMESPACE> get pods -o name -l 'app.kubernetes.io/instance=<RELEASE>,app.kubernetes.io/component in (backend,worker,subgoal-worker,schedule-worker,maintenance-worker,beat)'); do
     echo "$p $(kubectl -n <NAMESPACE> exec "$p" -- sh -c 'printf %s "$VAULT_MASTER_KEY" | sha256sum | cut -c1-16')"
   done
   ```
   All lines must show the same hash. If a Source was saved while the API ran on a
   different key than it does now, re-enter its access key / secret in the UI.
3. Check the defence-in-depth variable reached the pods (it prints only `true`):
   ```bash
   for p in $(kubectl -n <NAMESPACE> get pods -o name -l 'app.kubernetes.io/instance=<RELEASE>,app.kubernetes.io/component in (backend,worker,subgoal-worker,schedule-worker,maintenance-worker,beat)'); do
     echo "$p $(kubectl -n <NAMESPACE> exec "$p" -- printenv AWS_EC2_METADATA_DISABLED)"
   done
   ```
4. Run a manual sync of the S3 / MinIO Source; the job must complete, or fail with an S3
   error code (e.g. `AccessDenied`) — never with `169.254.169.254`.

## 5. Every ingestion path answers 503 "Embedding provider is unavailable: embedding provider not configured" (BUG B)

The backend has no embedder: no embedding env var on the pods and no embedding model in
the Model Registry. Pick ONE of the two fixes; both reach the API and every worker.

### Option A: chart values (needs cluster access)

NVIDIA hosted embeddings (`nvidia/nemotron-3-embed-1b`, 2048-d — equal to the default
`EMBEDDING_DIM` / pgvector column width):
```bash
helm upgrade <RELEASE> agent-verse-backend/infra/helm/agentverse -n <NAMESPACE> --reuse-values \
  --set-string secrets.nvidiaApiKey='<NVIDIA_API_KEY>' \
  --set-string embedding.nvidiaEmbedModel=nvidia/nemotron-3-embed-1b \
  --set-string embedding.nvidiaEmbedDim=2048 \
  --set-string embedding.dim=2048
```
- `secrets.nvidiaApiKey` goes into the chart Secret (`NVIDIA_API_KEY`, a `secretKeyRef` on
  every app workload); the embedding settings go into the shared ConfigMap
  (`NVIDIA_EMBED_MODEL`, `NVIDIA_EMBED_DIM`, `EMBEDDING_DIM`). Empty values are not rendered.
- With `secrets.create=false` (an externally managed Secret), add the key there instead:
  `kubectl -n <NAMESPACE> patch secret <RELEASE>-agentverse-secrets --type merge -p '{"stringData":{"NVIDIA_API_KEY":"<NVIDIA_API_KEY>"}}'`
  then `kubectl -n <NAMESPACE> rollout restart deploy -l 'app.kubernetes.io/instance=<RELEASE>,app.kubernetes.io/component in (backend,worker,subgoal-worker,schedule-worker,maintenance-worker,beat)'`.
- Any other OpenAI-compatible `/v1/embeddings` server instead:
  `--set-string embedding.baseUrl=<EMBEDDING_BASE_URL> --set-string embedding.model=<EMBEDDING_MODEL> --set-string embedding.dim=<DIM> --set-string secrets.embeddingApiKey='<EMBEDDING_API_KEY>'`.
- Note: an NVIDIA key also makes NVIDIA available to the chat / planning router
  (`app/core/config.py`, NVIDIA NIM section) — expected for this deployment.
- Legacy chart (`agent-verse-backend/helm/agentverse`, ExternalSecrets): store the key at
  `agentverse/nvidia-api-key` in the secret store (`externalSecrets.secrets.nvidiaApiKey`)
  and set `--set-string embedding.nvidiaEmbedModel=nvidia/nemotron-3-embed-1b --set-string embedding.nvidiaEmbedDim=2048`.

### Option B: Model Registry (no cluster access)

As a platform admin (section 2), open the Models page, add an embedding model and save:
provider `nvidia` (or `openai_compatible`), model `nvidia/nemotron-3-embed-1b`, base URL
`https://integrate.api.nvidia.com/v1`, API key `<NVIDIA_API_KEY>` (stored vault-encrypted),
then use "Test connection" (it records the 2048-d width, which must equal `EMBEDDING_DIM`).
With no embedding env var set, that model becomes the embedder: workers pick it up on
their next task, and an API that started without an embedder binds it within ~15 s of the
registry change (no restart).

### Verify (either option)

1. The settings reached every pod (hash the key, never print it):
   ```bash
   for p in $(kubectl -n <NAMESPACE> get pods -o name -l 'app.kubernetes.io/instance=<RELEASE>,app.kubernetes.io/component in (backend,worker,subgoal-worker,schedule-worker,maintenance-worker,beat)'); do
     echo "$p $(kubectl -n <NAMESPACE> exec "$p" -- sh -c 'printf %s "$NVIDIA_API_KEY" | sha256sum | cut -c1-16; echo " model=$NVIDIA_EMBED_MODEL dim=$NVIDIA_EMBED_DIM/$EMBEDDING_DIM"')"
   done
   ```
   (Option A: same hash and `model=nvidia/nemotron-3-embed-1b dim=2048/2048` on every pod.
   The hash of an empty key is `e3b0c44298fc1c14`, i.e. not configured.)
2. The API reports an embedder (unauthenticated, no secrets in the output):
   ```bash
   kubectl -n <NAMESPACE> exec deploy/<RELEASE>-agentverse-backend -- python -c \
     "import json,urllib.request;h=json.load(urllib.request.urlopen('http://localhost:8000/health'));print(h['capabilities']['embedder']);print(urllib.request.urlopen('http://localhost:8000/health/ready').status)"
   ```
   Expect `status: available`, the model `nvidia/nemotron-3-embed-1b`, dimension 2048, and 200.
3. A small ingest succeeds (no 503): upload a short text document to a collection in the
   UI, or `POST /knowledge/ingest` with a one-line text, and check it is searchable.
