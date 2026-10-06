# AgentVerse Kubernetes Deployment with Helm, Kong, Loki, and Grafana

This guide deploys AgentVerse end-to-end on Minikube using the first-party Helm
chart at `agent-verse-backend/infra/helm/agentverse`.

The chart deploys:

- Kong Gateway in DB-less mode as the front door
- Backend API
- Frontend web app
- Celery worker and beat scheduler
- Postgres + pgvector with persistent volume
- Redis with AOF persistence
- MinIO with persistent volume
- Loki with persistent volume
- Promtail DaemonSet for Kubernetes pod logs
- Grafana with Prometheus + Loki datasources
- Prometheus and OpenTelemetry Collector
- Mailpit for local SMTP testing

## Prerequisites

Install:

- Docker
- Minikube
- kubectl
- Helm 3

Start Minikube:

```bash
minikube -p agentverse start --cpus=4 --memory=7600 --disk-size=60g
kubectl config use-context agentverse
```

Use Minikube's Docker daemon so locally-built images are visible to the cluster:

```bash
eval $(minikube -p agentverse docker-env)
```

## Build Images

From repo root:

```bash
docker build -t agentverse/backend:local ./agent-verse-backend \
  --build-arg INSTALL_PLAYWRIGHT=false
docker build -t agentverse/frontend:local ./agent-verse-frontend \
  --build-arg VITE_API_URL=/api \
  --build-arg VITE_GRAFANA_URL=/grafana
```

For production or RPA/browser-automation validation, build the backend with
`--build-arg INSTALL_PLAYWRIGHT=true` (the default). The smaller Minikube build
above avoids loading several hundred MB of browser binaries into constrained
local Docker-driver profiles.

## Deployment Modes

The chart supports three deployment modes:

1. **Full stack** — app + infra together, using `values.yaml`.
2. **Infrastructure-only** — Postgres, Redis, MinIO, Kong, Loki, Promtail,
   Grafana, Prometheus, OTel, Mailpit, backups using `values-infra.yaml`.
3. **Application-only** — backend, frontend, worker, beat using `values-app.yaml`.

Keep using the all-in-one chart for one-click local deployment. Use the infra/app
split for production where infrastructure changes must be controlled separately
from application rollouts.

## Deploy Full Stack with Helm

```bash
helm upgrade --install agentverse ./agent-verse-backend/infra/helm/agentverse \
  --namespace agentverse \
  --create-namespace \
  --set secrets.databasePassword=agentverse \
  --set secrets.redisPassword=agentverse-redis \
  --set secrets.vaultMasterKey=change-me-vault-master-key-min-32-chars \
  --set secrets.jwtSecret=change-me-jwt \
  --set secrets.goalTokenSecret=change-me-goal-token \
  --set secrets.manifestSigningSecret=change-me-manifest \
  --set secrets.platformAdminKey=change-me-admin-key
```

## Deploy Infrastructure Only

```bash
helm upgrade --install agentverse-infra ./agent-verse-backend/infra/helm/agentverse \
  --namespace agentverse \
  --create-namespace \
  -f ./agent-verse-backend/infra/helm/agentverse/values-infra.yaml \
  --set secrets.databasePassword=agentverse \
  --set secrets.redisPassword=agentverse-redis \
  --set secrets.vaultMasterKey=change-me-vault-master-key-min-32-chars \
  --set secrets.jwtSecret=change-me-jwt
```

Deploy just one infrastructure component, for example Postgres:

```bash
helm upgrade --install agentverse-postgres ./agent-verse-backend/infra/helm/agentverse \
  --namespace agentverse \
  --create-namespace \
  -f ./agent-verse-backend/infra/helm/agentverse/values-infra.yaml \
  --set redis.enabled=false \
  --set minio.enabled=false \
  --set kong.enabled=false \
  --set loki.enabled=false \
  --set promtail.enabled=false \
  --set grafana.enabled=false \
  --set prometheus.enabled=false \
  --set otel.enabled=false \
  --set mailpit.enabled=false
```

Common component toggles:

```bash
# Redis only
--set postgresql.enabled=false --set minio.enabled=false --set kong.enabled=false \
--set loki.enabled=false --set promtail.enabled=false --set grafana.enabled=false \
--set prometheus.enabled=false --set otel.enabled=false --set mailpit.enabled=false --set backup.enabled=false

# Observability only (Loki/Promtail/Grafana/Prometheus/Otel)
--set postgresql.enabled=false --set redis.enabled=false --set minio.enabled=false \
--set kong.enabled=false --set mailpit.enabled=false --set backup.enabled=false

# Kong only
--set postgresql.enabled=false --set redis.enabled=false --set minio.enabled=false \
--set loki.enabled=false --set promtail.enabled=false --set grafana.enabled=false \
--set prometheus.enabled=false --set otel.enabled=false --set mailpit.enabled=false --set backup.enabled=false
```

## Deploy Application Only

Deploy this after infrastructure:

```bash
helm upgrade --install agentverse-app ./agent-verse-backend/infra/helm/agentverse \
  --namespace agentverse \
  -f ./agent-verse-backend/infra/helm/agentverse/values-app.yaml \
  --set backend.image.repository=agentverse/backend \
  --set backend.image.tag=local \
  --set worker.image.repository=agentverse/backend \
  --set worker.image.tag=local \
  --set beat.image.repository=agentverse/backend \
  --set beat.image.tag=local \
  --set frontend.image.repository=agentverse/frontend \
  --set frontend.image.tag=local
```

Wait for workloads:

```bash
kubectl -n agentverse get pods -w
```

## Access the Platform

Kong is exposed as NodePort `30080` by default.

```bash
minikube -p agentverse service -n agentverse agentverse-agentverse-kong --url
```

Or use:

```bash
export AGENTVERSE_URL="http://$(minikube -p agentverse ip):30080"
open "$AGENTVERSE_URL"
```

Routes through Kong:

- Frontend: `/`
- Backend API: `/api/*`
- Backend health: `/health`
- Grafana: `/grafana/*`

Example checks:

```bash
curl -f "$AGENTVERSE_URL/health"
curl -f "$AGENTVERSE_URL/api/health"
```

## Persistent Volumes

The chart creates PVCs for stateful components:

- `agentverse-agentverse-postgres`
- `agentverse-agentverse-redis`
- `agentverse-agentverse-minio`
- `agentverse-agentverse-loki`
- `agentverse-agentverse-grafana`
- `agentverse-agentverse-prometheus`

Inspect:

```bash
kubectl -n agentverse get pvc
```

## Logs Pipeline: Promtail → Loki → Grafana

Promtail runs as a DaemonSet and scrapes pod logs from `/var/log/pods`. It pushes
Loki datasource.

Port-forward Grafana if Kong route is unavailable:

```bash
kubectl -n agentverse port-forward svc/agentverse-agentverse-grafana 3001:3000
open http://localhost:3001
```

Default credentials are from `values.yaml`:

- user: `admin`
- password: `agentverse`

In Grafana Explore:

```logql
{namespace="agentverse"}
```

## Smoke / E2E Validation

Run local smoke checks:

```bash
helm template agentverse ./agent-verse-backend/infra/helm/agentverse > /tmp/agentverse.yaml
kubectl apply --dry-run=client -f /tmp/agentverse.yaml
```

After deploy:

```bash
curl -f "$AGENTVERSE_URL/health"
curl -f "$AGENTVERSE_URL/api/health"
kubectl -n agentverse get pods
kubectl -n agentverse get pvc
```

Optional load smoke:

```bash
k6 run ./agent-verse-backend/infra/loadtest/goal_submission.js \
  -e BASE_URL="$AGENTVERSE_URL/api" \
  -e API_KEY=<tenant-api-key>
```

## Docker Compose Gateway/Logging Stack

For local Docker Compose, the backend compose now includes:

- Kong at `http://localhost:8080`
- Loki at `http://localhost:3100`
- Promtail shipping container logs to Loki
- Grafana with Prometheus and Loki datasources at `http://localhost:3001`

Run:

```bash
cd agent-verse-backend
docker-compose -f infra/docker-compose.yml up -d postgres redis pgbouncer backend worker beat frontend kong loki promtail prometheus grafana
```

Access:

- Main gateway: `http://localhost:8080`
- Direct backend: `http://localhost:8000`
- Direct frontend: `http://localhost:5173`
- Grafana: `http://localhost:3001`

## Production Notes

For production:

- Replace all `secrets.*` values with Kubernetes ExternalSecrets or sealed secrets.
- Use a managed Postgres or a highly-available Postgres operator for production.
- Use object storage for Loki chunks if logs must persist beyond a single node.
- Use Kong Enterprise or Kong Ingress Controller/Gateway API if you need dynamic
  route reconciliation from Kubernetes resources.
- Run migrations as a one-shot Job or a controlled release step before scaling the
  backend above one replica.

### Allowing your own internal sources (e.g. MinIO on a private IP)

Ingestion connectors refuse private, loopback, link-local and cloud-metadata
addresses. If a knowledge source lives on your own network (say a MinIO at
`192.168.63.104`), the operator opens exactly those hosts with two settings:

| Setting | Helm value (both charts) | Default |
|---------|--------------------------|---------|
| `INGESTION_ALLOW_INTERNAL_SOURCES` | `ingestion.allowInternalSources` | `false` |
| `INGESTION_INTERNAL_SOURCE_ALLOWLIST` | `ingestion.internalSourceAllowlist` | `""` |

`INGESTION_INTERNAL_SOURCE_ALLOWLIST` takes hostnames and single IPs (a host covers every port). For testing
only, it also takes private ranges in CIDR form (`192.168.0.0/16`); metadata / link-local / `0.0.0.0/8` stay blocked even
when listed, and `ENVIRONMENT=production` refuses CIDR entries — allowlist service hostnames in production.

```bash
helm upgrade --install agentverse infra/helm/agentverse \
  --set ingestion.allowInternalSources=true \
  --set-string ingestion.internalSourceAllowlist='192.168.63.104\,minio.internal'
```

- Both are needed: the allowlist does nothing while the flag is off.
- Entries are comma-separated hostnames or IP literals. Only the listed hosts open;
  every other private, metadata or neighbouring address stays blocked.
- They are operator-only. A tenant's `connection_config` can never set or widen them.
- They are rendered into the shared app config map, so the API and every worker and
  beat enforce the same policy (ingestion runs on the workers too).
- Raw manifests: edit `INGESTION_*` in `agent-verse-backend/infra/k8s/configmap.yaml`
  (the `agentverse-config` ConfigMap). Docker Compose: set them in the backend `.env`
  (see `.env.example`); the production compose file passes them to every app service.

### OCR parallelism (scanned documents)

OCR runs pages of a document side by side and several documents at once, on ONE
bounded pool per process (API and every worker). The defaults size it from the
container's CPU limit (cgroup quota, not the node's core count) and split it
between a prefork worker's children, so most deployments need no tuning. Knobs
(helm `ocr.*`, raw k8s `agentverse-config`, prod compose shared env):

| Setting | Default | Meaning |
|---|---|---|
| `OCR_MAX_CONCURRENCY` | `0` (= CPUs) | OCR threads and page bitmaps in flight per process |
| `OCR_PAGE_CONCURRENCY` | `0` (= max - 1, min 2) | pages of one document at once; below the global cap so a huge scan never starves another document |
| `OCR_VISION_CONCURRENCY` | `4` | LLM-vision fallback calls in flight per process |
| `OCR_RENDER_DPI` | `300` | scanned-page rasterisation (one page at a time) |
| `OMP_THREAD_LIMIT` | `1` | tesseract's own OpenMP threads (pages already run in parallel) |

Memory: each page in flight holds one rasterised page (about 9 MB at 300 dpi for
A4 grayscale, plus its preprocessed copy), so peak OCR memory per process is about
`OCR_MAX_CONCURRENCY x 20 MB`. Give the CPU limit to the pods that OCR, and
raise `OCR_MAX_CONCURRENCY` only together with it.

## GitHub Actions Pipelines

The repository contains separate pipelines for each stage:

| Workflow | Purpose |
|---|---|
| `.github/workflows/ci-full.yml` | Runs full backend and frontend CI, plus Helm render checks |
| `.github/workflows/build-images.yml` | Builds backend/frontend images and pushes to GHCR |
| `.github/workflows/helm-validate.yml` | Lints/renders/packages all chart variants |
| `.github/workflows/deploy-infra.yml` | Manually deploys infra-only or one infra component |
| `.github/workflows/deploy-app.yml` | Manually deploys backend/frontend/worker/beat using existing infra |
| `.github/workflows/deploy-full.yml` | Manually deploys the entire stack |
| `.github/workflows/rollback.yml` | Rolls a Helm release back to a previous revision |

Required GitHub environment secrets:

```text
KUBE_CONFIG              # base64-encoded kubeconfig
DATABASE_PASSWORD
REDIS_PASSWORD
VAULT_MASTER_KEY
JWT_SECRET
```

Optional production secrets:

```text
ANTHROPIC_API_KEY
OPENAI_API_KEY
GOOGLE_API_KEY
VOYAGE_API_KEY
STRIPE_SECRET_KEY
```

### Deploy infra from GitHub Actions

Run workflow: **Deploy Infrastructure**.

Inputs:

- `environment`: `staging` or `production`
- `component`: `all`, `postgres`, `redis`, `minio`, `kong`, or `observability`

### Deploy app from GitHub Actions

Run workflow: **Deploy Application**.

Inputs:

- `environment`: `staging` or `production`
- `image_tag`: image tag to deploy, default `${GITHUB_SHA}`
- `deploy_frontend`: true/false
- `deploy_backend`: true/false

### Rollback

Run workflow: **Helm Rollback** with:

- `release`: `agentverse`, `agentverse-app`, or `agentverse-infra`
- `revision`: revision from `helm history <release> -n agentverse`
