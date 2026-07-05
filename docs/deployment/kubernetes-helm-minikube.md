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
minikube start --cpus=6 --memory=12288 --disk-size=60g
```

Use Minikube's Docker daemon so locally-built images are visible to the cluster:

```bash
eval $(minikube docker-env)
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

## Deploy with Helm

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

Wait for workloads:

```bash
kubectl -n agentverse get pods -w
```

## Access the Platform

Kong is exposed as NodePort `30080` by default.

```bash
minikube service -n agentverse agentverse-agentverse-kong --url
```

Or use:

```bash
export AGENTVERSE_URL="http://$(minikube ip):30080"
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
