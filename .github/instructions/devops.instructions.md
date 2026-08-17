---
applyTo: ".github/workflows/**,agent-verse-backend/helm/**,agent-verse-backend/Dockerfile,agent-verse-frontend/Dockerfile"
---

# DevOps Instructions — AgentVerse

## Docker Build Standards

### Backend Dockerfile Pattern
```dockerfile
# Always multi-stage — keep runtime image minimal
FROM python:3.12-slim AS builder
WORKDIR /app
RUN pip install uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev    # install deps without dev extras

FROM python:3.12-slim AS runtime
# Non-root user — NEVER run as root
RUN addgroup --system appuser && adduser --system --group appuser
WORKDIR /app
COPY --from=builder /app/.venv ./.venv
COPY app/ ./app/
USER appuser
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
# Always set graceful shutdown timeout
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000",
     "--workers", "4", "--timeout-graceful-shutdown", "30"]
```

## GitHub Actions Conventions

### Job Naming
```yaml
jobs:
  backend-lint:       # ruff + mypy
  backend-test:       # pytest (unit + integration)
  frontend-lint:      # eslint + typecheck
  frontend-test:      # vitest
  contract-test:      # API contract verification
  docker-build:       # build + push to ghcr.io
  deploy-staging:     # kubectl rollout (auto on main)
  deploy-production:  # kubectl rollout (manual approval)
  rollback:           # manual trigger only
```

### Required CI Checks (Block Merge If Any Fail)
```yaml
- ruff check + mypy (backend)
- pytest --cov (80% minimum coverage)
- eslint + tsc --noEmit (frontend)
- vitest run (frontend unit)
- bundlesize check (<200KB initial)
- pip-audit + npm audit (security)
- grype (container vulnerability scan)
```

## Kubernetes / Helm Standards

### Resource Limits (Always Set)
```yaml
resources:
  requests:
    cpu: "250m"
    memory: "512Mi"
  limits:
    cpu: "1000m"
    memory: "1Gi"
```

### HPA Configuration
```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
spec:
  minReplicas: 2      # never < 2 (availability)
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80
```

### Health Checks (Always Configure)
```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8000
  initialDelaySeconds: 10
  periodSeconds: 10
  timeoutSeconds: 3
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /ready
    port: 8000
  initialDelaySeconds: 5
  periodSeconds: 5
  timeoutSeconds: 3
  failureThreshold: 2
```

### Pod Disruption Budget (Always Set)
```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
spec:
  minAvailable: 1    # Never all pods down during rolling update
```

## Secret Management

```yaml
# NEVER hardcode secrets in YAML
# NEVER use ConfigMap for secrets
# ALWAYS use ExternalSecret (already in helm/templates/externalsecret.yaml):
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
spec:
  secretStoreRef:
    name: vault-backend   # or AWS Secrets Manager
  target:
    name: agentverse-secrets
  data:
    - secretKey: DATABASE_URL
      remoteRef:
        key: agentverse/production
        property: database_url
```

## Deployment Strategy

### Rolling Update (Default)
```yaml
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxSurge: 1         # one extra pod during update
    maxUnavailable: 0   # zero downtime
```

### Blue-Green Switch
```bash
# Switch traffic from blue to green:
kubectl patch service backend -p '{"spec":{"selector":{"slot":"green"}}}'
# Verify error rate before committing:
kubectl rollout status deployment/backend-green --timeout=120s
```

## Observability in k8s

```yaml
# Always export OTel to the collector:
env:
  - name: OTEL_EXPORTER_OTLP_ENDPOINT
    value: "http://otel-collector:4317"
  - name: OTEL_SERVICE_NAME
    value: "agentverse-backend"
  - name: OTEL_RESOURCE_ATTRIBUTES
    value: "deployment.environment=$(ENVIRONMENT)"
```

## Run Commands Reference

```bash
# Local dev:
docker-compose -f infra/docker-compose.yml up -d postgres redis

# Apply migrations:
cd agent-verse-backend && uv run alembic upgrade head

# Deploy to staging:
helm upgrade agentverse ./helm/agentverse \
  --namespace staging \
  --set image.tag=$IMAGE_TAG \
  --wait --timeout 5m

# Rollback:
helm rollback agentverse --namespace staging
```
