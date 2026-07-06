# AgentVerse — Local Minikube Deployment Guide

This guide walks you through bringing the **entire AgentVerse stack** up on your
laptop using Minikube, including the backend API, Celery worker, beat scheduler,
frontend, Postgres, Redis, MinIO, Kong gateway, Loki, Grafana, Prometheus, OTel
Collector, and Mailpit.

---

## What Gets Deployed

| Component | Image | Role |
|-----------|-------|------|
| **backend** | `agentverse/backend:local` | FastAPI + LangGraph API server |
| **worker** | `agentverse/backend:local` | Celery worker (all goal queues) |
| **beat** | `agentverse/backend:local` | Celery beat scheduler |
| **frontend** | `agentverse/frontend:local` | React 19 web app |
| **postgres** | `pgvector/pgvector:pg16` | Primary database with vector search |
| **redis** | `redis:7-alpine` | Cache, pub/sub, checkpoints |
| **minio** | `minio/minio` | Artifact / file object storage |
| **kong** | `kong:3.7` | API gateway (NodePort 30080) |
| **loki** | `grafana/loki:2.9.8` | Log aggregation |
| **grafana** | `grafana/grafana:10.4.0` | Dashboards (Prometheus + Loki) |
| **prometheus** | `prom/prometheus:v2.51.0` | Metrics scraping |
| **otel** | `otel/opentelemetry-collector-contrib` | Trace collector |
| **mailpit** | `axllent/mailpit` | Local SMTP testing |

---

## Prerequisites

Install the following tools on your machine:

```bash
# macOS — install everything via Homebrew
brew install minikube kubectl helm docker git gh
brew install uv          # Python package manager used by the backend
brew install node        # Node.js + npm for the frontend
```

Verify versions:

```bash
minikube version         # >= 1.30
kubectl version --client # >= 1.28
helm version             # >= 3.14
docker version           # >= 24
uv --version             # >= 0.4
node --version           # >= 20
```

---

## Step 0 — Fix Docker Desktop File Descriptor Limit (macOS)

> **Do this first.** Without it, kube-proxy inside Minikube will crash with
> `too many open files` and service DNS will not work.

Open **Docker Desktop → Settings → Docker Engine** and add the `default-ulimits`
block to the JSON config:

```json
{
  "default-ulimits": {
    "nofile": {
      "Name": "nofile",
      "Hard": 1048576,
      "Soft": 1048576
    }
  }
}
```

Click **Apply & Restart**. Docker Desktop must restart fully before continuing.

**Alternative: use Colima instead of Docker Desktop**

```bash
brew install colima
colima start --cpu 4 --memory 8 --disk 60 --vm-type vz
```

Colima sets higher ulimits by default and avoids the kube-proxy crash entirely.

---

## Step 1 — Start Minikube

```bash
minikube -p agentverse start \
  --cpus=4 \
  --memory=7600 \
  --disk-size=60g

kubectl config use-context agentverse
```

Verify the cluster is healthy before continuing:

```bash
# kube-proxy must be Running (not CrashLoopBackOff)
kubectl -n kube-system rollout status daemonset/kube-proxy --timeout=120s

# CoreDNS must be Running
kubectl -n kube-system rollout status deployment/coredns --timeout=120s

# DNS must resolve internal services
kubectl -n default run dns-ok \
  --image=busybox:1.36 \
  --restart=Never \
  --rm -i -- \
  nslookup kubernetes.default.svc.cluster.local
```

Expected output:

```
Server:    10.96.0.10
Address 1: 10.96.0.10 kube-dns.kube-system.svc.cluster.local

Name:      kubernetes.default.svc.cluster.local
Address 1: 10.96.0.1 kubernetes.default.svc.cluster.local
```

If kube-proxy is in `CrashLoopBackOff`, go back to Step 0 and increase Docker
Desktop file descriptor limits, then delete and recreate the profile:

```bash
minikube -p agentverse delete
# (fix Docker Desktop ulimits, restart Docker Desktop)
minikube -p agentverse start --cpus=4 --memory=7600 --disk-size=60g
```

---

## Step 2 — Build Docker Images

Build images on the host and load them into Minikube. Do **not** run
`eval $(minikube docker-env)` first — build on the host daemon for reliable
dependency downloads.

```bash
# From repo root
docker build -t agentverse/backend:local ./agent-verse-backend \
  --build-arg INSTALL_PLAYWRIGHT=false

docker build -t agentverse/frontend:local ./agent-verse-frontend \
  --build-arg VITE_API_URL=/api \
  --build-arg VITE_GRAFANA_URL=/grafana
```

> Use `INSTALL_PLAYWRIGHT=true` only when testing RPA/browser automation features.
> The default `false` keeps the image under 1 GB for faster Minikube loads.

Load images into the Minikube node:

```bash
minikube -p agentverse image load agentverse/backend:local
minikube -p agentverse image load agentverse/frontend:local
```

---

## Step 3 — Deploy Everything (One Command)

This single Helm command deploys the entire stack — infra + app + observability
— into the `agentverse` namespace.

```bash
helm upgrade --install agentverse \
  ./agent-verse-backend/infra/helm/agentverse \
  --namespace agentverse \
  --create-namespace \
  --set localStaticPVs.enabled=true \
  --wait \
  --timeout 20m
```

`localStaticPVs.enabled=true` creates static `hostPath` PersistentVolumes inside
the Minikube node. This avoids relying on the dynamic storage provisioner, which
can be flaky on Docker-driver profiles.

Watch pods come up:

```bash
kubectl -n agentverse get pods -w
```

All pods should reach `Running` or `Completed` within 5–10 minutes on a 4-CPU
laptop.

---

## Step 3 (alternative) — Deploy Infra and App Separately

Use this two-step approach when you want to iterate on the app without touching
the database or gateway.

### 3a — Deploy infrastructure only

```bash
helm upgrade --install agentverse-infra \
  ./agent-verse-backend/infra/helm/agentverse \
  --namespace agentverse \
  --create-namespace \
  -f ./agent-verse-backend/infra/helm/agentverse/values-infra.yaml \
  --set localStaticPVs.enabled=true \
  --wait \
  --timeout 15m
```

This starts: Postgres, Redis, MinIO, Kong, Loki, Grafana, Prometheus, OTel,
Mailpit, and the nightly backup CronJob.

Wait for infra pods to be ready:

```bash
kubectl -n agentverse rollout status deployment/agentverse-infra-agentverse-grafana
kubectl -n agentverse rollout status statefulset/agentverse-infra-agentverse-postgres 2>/dev/null || \
  kubectl -n agentverse get pods -l app.kubernetes.io/component=postgres
```

### 3b — Deploy application only

```bash
# Read ClusterIPs because service DNS requires kube-proxy (which may be unstable locally)
POSTGRES_IP=$(kubectl -n agentverse get svc agentverse-infra-agentverse-postgres \
  -o jsonpath='{.spec.clusterIP}')
REDIS_IP=$(kubectl -n agentverse get svc agentverse-infra-agentverse-redis \
  -o jsonpath='{.spec.clusterIP}')
MINIO_IP=$(kubectl -n agentverse get svc agentverse-infra-agentverse-minio \
  -o jsonpath='{.spec.clusterIP}')

helm upgrade --install agentverse-app \
  ./agent-verse-backend/infra/helm/agentverse \
  --namespace agentverse \
  -f ./agent-verse-backend/infra/helm/agentverse/values-app.yaml \
  --set externalServices.postgresHost="$POSTGRES_IP" \
  --set externalServices.redisHost="$REDIS_IP" \
  --set externalServices.minioEndpoint="http://$MINIO_IP:9000" \
  --wait \
  --timeout 15m
```

This starts: backend API, Celery worker (all goal queues), Celery beat, and the
frontend web app.

---

## Step 4 — Run Database Migrations

Migrations run automatically inside the backend container at startup (the
`lifespan` hook runs `alembic upgrade head`). However, you can also run them
manually if needed:

```bash
BACKEND_POD=$(kubectl -n agentverse get pod \
  -l app.kubernetes.io/component=backend \
  -o jsonpath='{.items[0].metadata.name}')

kubectl -n agentverse exec "$BACKEND_POD" -- \
  uv run alembic upgrade head
```

---

## Step 5 — Verify All Pods Are Running

```bash
kubectl -n agentverse get pods -o wide
```

Expected steady-state (all Running):

```
NAME                                          READY   STATUS    RESTARTS
agentverse-backend-xxxxxxxxx-xxxxx            1/1     Running   0
agentverse-worker-xxxxxxxxx-xxxxx             1/1     Running   0
agentverse-beat-xxxxxxxxx-xxxxx               1/1     Running   0
agentverse-frontend-xxxxxxxxx-xxxxx           1/1     Running   0
agentverse-postgres-0                         1/1     Running   0
agentverse-redis-0                            1/1     Running   0
agentverse-minio-xxxxxxxxx-xxxxx              1/1     Running   0
agentverse-kong-xxxxxxxxx-xxxxx               1/1     Running   0
agentverse-loki-xxxxxxxxx-xxxxx               1/1     Running   0
agentverse-grafana-xxxxxxxxx-xxxxx            1/1     Running   0
agentverse-prometheus-xxxxxxxxx-xxxxx         1/1     Running   0
agentverse-otel-xxxxxxxxx-xxxxx               1/1     Running   0
agentverse-mailpit-xxxxxxxxx-xxxxx            1/1     Running   0
agentverse-loki-smoke-xxxxxxxxx-xxxxx         1/1     Running   0
```

Check PVCs are all Bound:

```bash
kubectl -n agentverse get pvc
```

---

## Step 6 — Access the Platform

Kong is the single front door, exposed on NodePort `30080`.

```bash
export AGENTVERSE_URL="http://$(minikube -p agentverse ip):30080"
echo "$AGENTVERSE_URL"
```

Open in browser:

```bash
open "$AGENTVERSE_URL"
```

### URL Routes Through Kong

| Path | Service |
|------|---------|
| `$AGENTVERSE_URL/` | React frontend |
| `$AGENTVERSE_URL/api/*` | Backend REST API |
| `$AGENTVERSE_URL/health` | Backend health check |
| `$AGENTVERSE_URL/grafana/*` | Grafana dashboards |

### Quick Health Checks

```bash
# Platform gateway health
curl -f "$AGENTVERSE_URL/health"

# API health
curl -f "$AGENTVERSE_URL/api/health"

# List providers (no auth needed)
curl -f "$AGENTVERSE_URL/api/providers/catalog"
```

### Access Grafana

```bash
open "$AGENTVERSE_URL/grafana"
# user: admin
# password: agentverse
```

In Grafana Explore, query all pod logs:

```logql
{namespace="agentverse"}
```

### Port-Forward Alternatives (if Kong NodePort is unavailable)

```bash
# Backend API direct
kubectl -n agentverse port-forward svc/agentverse-agentverse-backend 8000:8000

# Frontend direct
kubectl -n agentverse port-forward svc/agentverse-agentverse-frontend 3000:80

# Grafana direct
kubectl -n agentverse port-forward svc/agentverse-agentverse-grafana 3001:3000

# MinIO console
kubectl -n agentverse port-forward svc/agentverse-agentverse-minio 9001:9001

# Mailpit (email testing)
kubectl -n agentverse port-forward svc/agentverse-agentverse-mailpit 8025:8025
```

---

## Step 7 — Create a Tenant and Run a Goal

Once the backend is running, create your first tenant:

```bash
# Create a tenant
curl -X POST "$AGENTVERSE_URL/api/tenants" \
  -H "Content-Type: application/json" \
  -d '{"name": "local-dev", "plan": "professional"}'
```

The response includes an `api_key`. Export it:

```bash
export AV_API_KEY="<api_key from response>"
```

Submit a goal:

```bash
curl -X POST "$AGENTVERSE_URL/api/goals" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $AV_API_KEY" \
  -d '{"goal": "Summarise the top 3 features of AgentVerse"}'
```

Stream goal execution events (Server-Sent Events):

```bash
GOAL_ID="<goal_id from response>"
curl -N "$AGENTVERSE_URL/api/goals/$GOAL_ID/stream" \
  -H "X-API-Key: $AV_API_KEY"
```

---

## Quick-Reference Command Sheet

```bash
# 1. Start Minikube (one time)
minikube -p agentverse start --cpus=4 --memory=7600 --disk-size=60g

# 2. Build + load images
docker build -t agentverse/backend:local ./agent-verse-backend --build-arg INSTALL_PLAYWRIGHT=false
docker build -t agentverse/frontend:local ./agent-verse-frontend --build-arg VITE_API_URL=/api --build-arg VITE_GRAFANA_URL=/grafana
minikube -p agentverse image load agentverse/backend:local
minikube -p agentverse image load agentverse/frontend:local

# 3. Deploy full stack
helm upgrade --install agentverse ./agent-verse-backend/infra/helm/agentverse \
  -n agentverse --create-namespace --set localStaticPVs.enabled=true --wait --timeout 20m

# 4. Watch pods
kubectl -n agentverse get pods -w

# 5. Open platform
open "http://$(minikube -p agentverse ip):30080"

# 6. Check health
curl -f "http://$(minikube -p agentverse ip):30080/health"

# --- Day-2 operations ---

# Redeploy only the app (after code change)
docker build -t agentverse/backend:local ./agent-verse-backend --build-arg INSTALL_PLAYWRIGHT=false
minikube -p agentverse image load agentverse/backend:local
kubectl -n agentverse rollout restart deployment/agentverse-agentverse-backend
kubectl -n agentverse rollout restart deployment/agentverse-agentverse-worker
kubectl -n agentverse rollout restart deployment/agentverse-agentverse-beat

# View backend logs
kubectl -n agentverse logs -l app.kubernetes.io/component=backend -f

# View worker logs
kubectl -n agentverse logs -l app.kubernetes.io/component=worker -f

# Scale worker replicas
kubectl -n agentverse scale deployment/agentverse-agentverse-worker --replicas=2

# Run a one-off migration
BACKEND_POD=$(kubectl -n agentverse get pod -l app.kubernetes.io/component=backend -o jsonpath='{.items[0].metadata.name}')
kubectl -n agentverse exec "$BACKEND_POD" -- uv run alembic upgrade head

# Tear everything down (keeps Minikube running)
helm uninstall agentverse -n agentverse

# Tear down and delete cluster
helm uninstall agentverse -n agentverse
minikube -p agentverse stop
minikube -p agentverse delete
```

---

## Troubleshooting

### kube-proxy is in CrashLoopBackOff

```bash
kubectl -n kube-system logs -l k8s-app=kube-proxy --tail=20
# error: failed complete: too many open files
```

**Fix:** Increase Docker Desktop file descriptor limits (Step 0) and restart
Docker Desktop. Then delete and recreate the Minikube profile.

### CoreDNS cannot reach `10.96.0.1`

This is a side-effect of kube-proxy being down. Fix kube-proxy first. CoreDNS
will become ready automatically once kube-proxy is healthy.

### Backend pod is in CrashLoopBackOff

```bash
kubectl -n agentverse logs -l app.kubernetes.io/component=backend --previous
```

Common causes:

| Log message | Fix |
|---|---|
| `could not connect to server: Connection refused (postgres)` | Postgres pod not ready yet; wait and retry |
| `VAULT_MASTER_KEY must be at least 32 chars` | Pass `--set secrets.vaultMasterKey=<32-char-string>` |
| `alembic.exc.CommandError: Can't locate revision` | Run manual migration (Step 4) |
| `MANAGE_POOLS must be true` | Already set in `values.yaml`; verify the env block |

### Worker not processing goals

```bash
kubectl -n agentverse logs -l app.kubernetes.io/component=worker -f
```

Check the Redis connection is working:

```bash
REDIS_POD=$(kubectl -n agentverse get pod -l app.kubernetes.io/component=redis -o jsonpath='{.items[0].metadata.name}')
kubectl -n agentverse exec "$REDIS_POD" -- redis-cli -a agentverse-redis ping
# Expected: PONG
```

### PVCs stuck in Pending

```bash
kubectl -n agentverse describe pvc
```

If storage provisioner is crashing, use static PVs:

```bash
helm upgrade agentverse ./agent-verse-backend/infra/helm/agentverse \
  -n agentverse --set localStaticPVs.enabled=true --reuse-values
```

### Port 30080 not reachable

```bash
minikube -p agentverse service -n agentverse agentverse-agentverse-kong --url
```

Use the URL printed by that command instead of constructing it manually.

### Images not found (ErrImageNeverPull / ImagePullBackOff)

The Helm chart sets `imagePullPolicy: IfNotPresent`. Make sure you loaded the
images with `minikube image load` after every rebuild.

```bash
minikube -p agentverse image ls | grep agentverse
```

---

## Local vs Docker Compose

If Minikube is too heavy for your machine, the full stack also runs via Docker
Compose:

```bash
cd agent-verse-backend
docker-compose -f infra/docker-compose.yml up -d \
  postgres redis pgbouncer \
  backend worker beat \
  frontend \
  kong loki promtail prometheus grafana \
  mailpit

# Access
open http://localhost:8080        # Kong gateway
open http://localhost:3001        # Grafana
open http://localhost:8025        # Mailpit
```

---

## Known Limitations on This Machine

| Issue | Status |
|---|---|
| `kube-proxy` crashes with `too many open files` on Docker-driver Minikube | Fixed by increasing Docker Desktop ulimits (Step 0) |
| Dynamic storage provisioner may be flaky | Use `localStaticPVs.enabled=true` (default in all local workflows) |
| Full backend image with Playwright is ~2.5 GB | Build with `INSTALL_PLAYWRIGHT=false` for Minikube smoke testing |
| QEMU Minikube driver not installed | Install with `brew install qemu` as an alternative to Docker driver |
