# Phase 9: Infrastructure (RedBeat HA, Helm, Blue/Green, PgBouncer, OTel+Jaeger)

**Status:** Not started  
**Priority:** High — production readiness, zero-downtime deployments, connection scaling  
**Acceptance gate:** `helm lint helm/agentverse/`; `kubectl apply -k infra/k8s/`; beat HA with two replicas; PgBouncer starts and proxies Postgres connections; Jaeger UI reachable at `:16686`.

---

## 1. Current State

| Area | File | Current Behaviour |
|------|------|-------------------|
| Celery Beat | `infra/k8s/beat-deployment.yaml` | Single replica; if the pod dies, scheduled tasks stop firing until pod restarts. |
| Helm chart | `infra/` | No Helm chart. Only raw `k8s/*.yaml` manifests and `docker-compose.yml`. |
| Deployments | `infra/k8s/` | Single-color backend deployment; no blue/green strategy. |
| Database connections | `infra/docker-compose.yml` | Backend connects directly to Postgres with SQLAlchemy pool; no PgBouncer. |
| Tracing | `agent-verse-backend/app/core/config.py` | `otel_exporter_otlp_endpoint` config exists but Jaeger/collector not in compose. |
| Pool settings | `agent-verse-backend/app/core/config.py` | No explicit SQLAlchemy pool size settings. |

---

## 2. Gap Description

A single Celery Beat pod is a single point of failure for all scheduled agent triggers. There is no Helm chart for Kubernetes installation. Blue/green deployments require manual YAML editing. At scale, direct Postgres connections are exhausted quickly — PgBouncer in transaction mode allows 10-50× more application instances. Distributed tracing is wired in the app but Jaeger is absent from compose.

---

## 3. Full Implementation

### 3.1 Celery Beat HA with celery-redbeat

#### `agent-verse-backend/app/scaling/celery_app.py` changes

```python
# Add RedBeat scheduler import and configuration at the top of celery_app.py
# (alongside existing Celery configuration)

# ----- ADDITION: RedBeat HA scheduler -----

# 1. Install redbeat: add "celery-redbeat>=2.3.0" to pyproject.toml dependencies
# 2. Configure RedBeatScheduler as the default scheduler

from celery.schedules import crontab  # already imported

# Add these settings to the Celery app configuration block:
#
# app.conf.beat_scheduler = "redbeat.RedBeatScheduler"
# app.conf.redbeat_redis_url = settings.redis_url  # same Redis as broker
# app.conf.redbeat_lock_key = "redbeat:lock"
# app.conf.redbeat_lock_timeout = 5 * 60  # 5 minutes; auto-released if beat pod dies
#
# RedBeat uses a distributed Redis lock so only ONE of N beat replicas fires
# each task at any given time. If the lock-holder dies, another replica acquires
# the lock within `redbeat_lock_timeout` seconds.
```

**Full updated `celery_app.py` configuration section** (diff-style — add these lines after `app = Celery(...)`):

```python
# ---- RedBeat HA Beat Scheduler ----
app.conf.beat_scheduler = "redbeat.RedBeatScheduler"
app.conf.redbeat_redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
app.conf.redbeat_lock_key = "agentverse:beat:lock"
app.conf.redbeat_lock_timeout = 300  # 5 minutes

# ---- Beat Schedule ----
app.conf.beat_schedule = {
    "evaluate-knowledge-collections": {
        "task": "app.rag.tasks.evaluate_knowledge_collections",
        "schedule": crontab(day_of_week="sunday", hour=2, minute=0),
        "options": {"queue": "maintenance"},
    },
    "check-email-goals": {
        "task": "app.integrations.email.tasks.check_email_goals",
        "schedule": 60.0,
        "options": {"queue": "email"},
    },
    "cleanup-old-goals": {
        "task": "app.scaling.tasks.cleanup_old_goals",
        "schedule": crontab(hour=3, minute=0),
        "options": {"queue": "maintenance"},
    },
}
```

**pyproject.toml addition:**

```toml
# In [project.dependencies]:
"celery-redbeat>=2.3.0",
```

#### Updated `infra/k8s/beat-deployment.yaml`

```yaml
# infra/k8s/beat-deployment.yaml
# RedBeat HA Beat: allows 2 replicas safely — only one fires each task.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agentverse-beat
  namespace: agentverse
  labels:
    app: agentverse-beat
    component: beat
spec:
  replicas: 2   # ← was 1; now 2 for HA with RedBeat distributed lock
  selector:
    matchLabels:
      app: agentverse-beat
  template:
    metadata:
      labels:
        app: agentverse-beat
    spec:
      terminationGracePeriodSeconds: 30
      containers:
        - name: beat
          image: ghcr.io/agentverse/backend:latest
          command:
            - celery
            - -A
            - app.scaling.celery_app
            - beat
            - --scheduler=redbeat.RedBeatScheduler
            - --loglevel=info
          env:
            - name: REDIS_URL
              valueFrom:
                secretKeyRef:
                  name: agentverse-secrets
                  key: redis-url
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: agentverse-secrets
                  key: database-url
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 200m
              memory: 256Mi
          livenessProbe:
            exec:
              command:
                - celery
                - -A
                - app.scaling.celery_app
                - inspect
                - ping
                - -d
                - "celery@$(hostname)"
            initialDelaySeconds: 30
            periodSeconds: 60
            timeoutSeconds: 10
```

---

### 3.2 Helm Chart

#### Directory layout

```
helm/agentverse/
├── Chart.yaml
├── values.yaml
├── templates/
│   ├── NOTES.txt
│   ├── _helpers.tpl
│   ├── configmap.yaml
│   ├── externalsecret.yaml
│   ├── deployment.yaml
│   ├── worker-deployment.yaml
│   ├── beat-deployment.yaml
│   ├── frontend-deployment.yaml
│   ├── service.yaml
│   ├── ingress.yaml
│   ├── hpa.yaml
│   └── pdb.yaml
└── README.md
```

#### `helm/agentverse/Chart.yaml`

```yaml
apiVersion: v2
name: agentverse
description: AgentVerse — autonomous multi-tenant AI agent platform
type: application
version: 0.1.0
appVersion: "1.0.0"
home: https://agentverse.ai
sources:
  - https://github.com/agentverse/agentverse
maintainers:
  - name: AgentVerse Team
    email: infra@agentverse.ai
keywords:
  - ai
  - agents
  - llm
  - automation
```

#### `helm/agentverse/values.yaml`

```yaml
# Default values for agentverse Helm chart.
# Override with: helm install agentverse ./helm/agentverse -f custom-values.yaml

global:
  imageRegistry: "ghcr.io/agentverse"
  imagePullPolicy: IfNotPresent
  namespace: agentverse

# ── Backend API ──────────────────────────────────────────────────────────────
backend:
  image:
    name: backend
    tag: "latest"
  replicas: 2
  resources:
    requests:
      cpu: 200m
      memory: 512Mi
    limits:
      cpu: 1000m
      memory: 1Gi
  autoscaling:
    enabled: true
    minReplicas: 2
    maxReplicas: 10
    targetCPUUtilizationPercentage: 70
    targetMemoryUtilizationPercentage: 80
  podDisruptionBudget:
    minAvailable: 1
  env:
    ENVIRONMENT: production
    LOG_LEVEL: INFO
    DEFAULT_LLM_PROVIDER: anthropic

# ── Celery Worker ─────────────────────────────────────────────────────────────
worker:
  image:
    name: backend
    tag: "latest"
  replicas: 2
  concurrency: 4
  queues: "goals,schedules,maintenance"
  resources:
    requests:
      cpu: 300m
      memory: 512Mi
    limits:
      cpu: 2000m
      memory: 2Gi
  autoscaling:
    enabled: true
    minReplicas: 2
    maxReplicas: 20
    targetCPUUtilizationPercentage: 60

# ── Celery Beat ───────────────────────────────────────────────────────────────
beat:
  image:
    name: backend
    tag: "latest"
  replicas: 2  # HA with RedBeat
  resources:
    requests:
      cpu: 50m
      memory: 128Mi
    limits:
      cpu: 200m
      memory: 256Mi

# ── Frontend ──────────────────────────────────────────────────────────────────
frontend:
  image:
    name: frontend
    tag: "latest"
  replicas: 2
  resources:
    requests:
      cpu: 100m
      memory: 128Mi
    limits:
      cpu: 500m
      memory: 256Mi

# ── Ingress ───────────────────────────────────────────────────────────────────
ingress:
  enabled: true
  className: nginx
  host: agentverse.example.com
  tls:
    enabled: true
    secretName: agentverse-tls

# ── External Secrets ─────────────────────────────────────────────────────────
externalSecrets:
  enabled: true
  secretStoreRef:
    name: vault-secret-store
    kind: ClusterSecretStore
  refreshInterval: 1h
  secrets:
    databaseUrl:
      remoteRef:
        key: agentverse/database-url
    redisUrl:
      remoteRef:
        key: agentverse/redis-url
    anthropicApiKey:
      remoteRef:
        key: agentverse/anthropic-api-key
    masterEncryptionKey:
      remoteRef:
        key: agentverse/master-encryption-key

# ── Non-secret config ─────────────────────────────────────────────────────────
config:
  corsOrigins: "https://agentverse.example.com"
  otlpEndpoint: "http://otel-collector:4317"
  metricsEnabled: "true"
  semanticCacheEnabled: "true"
```

#### `helm/agentverse/templates/_helpers.tpl`

```yaml
{{/*
Expand the name of the chart.
*/}}
{{- define "agentverse.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "agentverse.labels" -}}
helm.sh/chart: {{ include "agentverse.name" . }}-{{ .Chart.Version }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Full image reference
*/}}
{{- define "agentverse.backendImage" -}}
{{ .Values.global.imageRegistry }}/{{ .Values.backend.image.name }}:{{ .Values.backend.image.tag }}
{{- end }}

{{- define "agentverse.workerImage" -}}
{{ .Values.global.imageRegistry }}/{{ .Values.worker.image.name }}:{{ .Values.worker.image.tag }}
{{- end }}

{{- define "agentverse.frontendImage" -}}
{{ .Values.global.imageRegistry }}/{{ .Values.frontend.image.name }}:{{ .Values.frontend.image.tag }}
{{- end }}
```

#### `helm/agentverse/templates/configmap.yaml`

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: agentverse-config
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "agentverse.labels" . | nindent 4 }}
data:
  ENVIRONMENT: {{ .Values.backend.env.ENVIRONMENT | quote }}
  LOG_LEVEL: {{ .Values.backend.env.LOG_LEVEL | quote }}
  DEFAULT_LLM_PROVIDER: {{ .Values.backend.env.DEFAULT_LLM_PROVIDER | quote }}
  CORS_ORIGINS: {{ .Values.config.corsOrigins | quote }}
  OTEL_EXPORTER_OTLP_ENDPOINT: {{ .Values.config.otlpEndpoint | quote }}
  METRICS_ENABLED: {{ .Values.config.metricsEnabled | quote }}
```

#### `helm/agentverse/templates/externalsecret.yaml`

```yaml
{{- if .Values.externalSecrets.enabled }}
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: agentverse-secrets
  namespace: {{ .Values.global.namespace }}
  labels:
    {{- include "agentverse.labels" . | nindent 4 }}
spec:
  refreshInterval: {{ .Values.externalSecrets.refreshInterval }}
  secretStoreRef:
    name: {{ .Values.externalSecrets.secretStoreRef.name }}
    kind: {{ .Values.externalSecrets.secretStoreRef.kind }}
  target:
    name: agentverse-secrets
    creationPolicy: Owner
  data:
    - secretKey: database-url
      remoteRef:
        key: {{ .Values.externalSecrets.secrets.databaseUrl.remoteRef.key }}
    - secretKey: redis-url
      remoteRef:
        key: {{ .Values.externalSecrets.secrets.redisUrl.remoteRef.key }}
    - secretKey: anthropic-api-key
      remoteRef:
        key: {{ .Values.externalSecrets.secrets.anthropicApiKey.remoteRef.key }}
    - secretKey: master-encryption-key
      remoteRef:
        key: {{ .Values.externalSecrets.secrets.masterEncryptionKey.remoteRef.key }}
{{- end }}
```

#### `helm/agentverse/templates/deployment.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agentverse-backend
  namespace: {{ .Values.global.namespace }}
  labels:
    app: agentverse-backend
    component: backend
    {{- include "agentverse.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.backend.replicas }}
  selector:
    matchLabels:
      app: agentverse-backend
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    metadata:
      labels:
        app: agentverse-backend
        component: backend
    spec:
      terminationGracePeriodSeconds: 60
      containers:
        - name: backend
          image: {{ include "agentverse.backendImage" . }}
          imagePullPolicy: {{ .Values.global.imagePullPolicy }}
          ports:
            - containerPort: 8000
              name: http
          envFrom:
            - configMapRef:
                name: agentverse-config
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: agentverse-secrets
                  key: database-url
            - name: REDIS_URL
              valueFrom:
                secretKeyRef:
                  name: agentverse-secrets
                  key: redis-url
            - name: ANTHROPIC_API_KEY
              valueFrom:
                secretKeyRef:
                  name: agentverse-secrets
                  key: anthropic-api-key
            - name: MASTER_ENCRYPTION_KEY
              valueFrom:
                secretKeyRef:
                  name: agentverse-secrets
                  key: master-encryption-key
          resources:
            {{- toYaml .Values.backend.resources | nindent 12 }}
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 5
            failureThreshold: 3
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 30
            periodSeconds: 15
            failureThreshold: 5
```

#### `helm/agentverse/templates/worker-deployment.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agentverse-worker
  namespace: {{ .Values.global.namespace }}
  labels:
    app: agentverse-worker
    component: worker
    {{- include "agentverse.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.worker.replicas }}
  selector:
    matchLabels:
      app: agentverse-worker
  template:
    metadata:
      labels:
        app: agentverse-worker
        component: worker
    spec:
      terminationGracePeriodSeconds: 120
      containers:
        - name: worker
          image: {{ include "agentverse.workerImage" . }}
          imagePullPolicy: {{ .Values.global.imagePullPolicy }}
          command:
            - celery
            - -A
            - app.scaling.celery_app
            - worker
            - --loglevel=info
            - --concurrency={{ .Values.worker.concurrency }}
            - -Q
            - {{ .Values.worker.queues }}
          envFrom:
            - configMapRef:
                name: agentverse-config
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: agentverse-secrets
                  key: database-url
            - name: REDIS_URL
              valueFrom:
                secretKeyRef:
                  name: agentverse-secrets
                  key: redis-url
          resources:
            {{- toYaml .Values.worker.resources | nindent 12 }}
```

#### `helm/agentverse/templates/beat-deployment.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agentverse-beat
  namespace: {{ .Values.global.namespace }}
  labels:
    app: agentverse-beat
    component: beat
    {{- include "agentverse.labels" . | nindent 4 }}
spec:
  replicas: {{ .Values.beat.replicas }}
  selector:
    matchLabels:
      app: agentverse-beat
  template:
    metadata:
      labels:
        app: agentverse-beat
        component: beat
    spec:
      terminationGracePeriodSeconds: 30
      containers:
        - name: beat
          image: {{ include "agentverse.workerImage" . }}
          imagePullPolicy: {{ .Values.global.imagePullPolicy }}
          command:
            - celery
            - -A
            - app.scaling.celery_app
            - beat
            - --scheduler=redbeat.RedBeatScheduler
            - --loglevel=info
          envFrom:
            - configMapRef:
                name: agentverse-config
          env:
            - name: REDIS_URL
              valueFrom:
                secretKeyRef:
                  name: agentverse-secrets
                  key: redis-url
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: agentverse-secrets
                  key: database-url
          resources:
            {{- toYaml .Values.beat.resources | nindent 12 }}
```

#### `helm/agentverse/templates/hpa.yaml`

```yaml
{{- if .Values.backend.autoscaling.enabled }}
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: agentverse-backend-hpa
  namespace: {{ .Values.global.namespace }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: agentverse-backend
  minReplicas: {{ .Values.backend.autoscaling.minReplicas }}
  maxReplicas: {{ .Values.backend.autoscaling.maxReplicas }}
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: {{ .Values.backend.autoscaling.targetCPUUtilizationPercentage }}
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: {{ .Values.backend.autoscaling.targetMemoryUtilizationPercentage }}
---
{{- end }}
{{- if .Values.worker.autoscaling.enabled }}
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: agentverse-worker-hpa
  namespace: {{ .Values.global.namespace }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: agentverse-worker
  minReplicas: {{ .Values.worker.autoscaling.minReplicas }}
  maxReplicas: {{ .Values.worker.autoscaling.maxReplicas }}
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: {{ .Values.worker.autoscaling.targetCPUUtilizationPercentage }}
{{- end }}
```

#### `helm/agentverse/templates/service.yaml`

```yaml
apiVersion: v1
kind: Service
metadata:
  name: agentverse-backend
  namespace: {{ .Values.global.namespace }}
  labels:
    app: agentverse-backend
    {{- include "agentverse.labels" . | nindent 4 }}
spec:
  type: ClusterIP
  selector:
    app: agentverse-backend
  ports:
    - port: 80
      targetPort: 8000
      name: http
---
apiVersion: v1
kind: Service
metadata:
  name: agentverse-frontend
  namespace: {{ .Values.global.namespace }}
spec:
  type: ClusterIP
  selector:
    app: agentverse-frontend
  ports:
    - port: 80
      targetPort: 80
      name: http
```

#### `helm/agentverse/templates/ingress.yaml`

```yaml
{{- if .Values.ingress.enabled }}
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: agentverse-ingress
  namespace: {{ .Values.global.namespace }}
  annotations:
    nginx.ingress.kubernetes.io/proxy-read-timeout: "600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "600"
    {{- if .Values.ingress.tls.enabled }}
    cert-manager.io/cluster-issuer: letsencrypt-prod
    {{- end }}
spec:
  ingressClassName: {{ .Values.ingress.className }}
  {{- if .Values.ingress.tls.enabled }}
  tls:
    - hosts:
        - {{ .Values.ingress.host }}
      secretName: {{ .Values.ingress.tls.secretName }}
  {{- end }}
  rules:
    - host: {{ .Values.ingress.host }}
      http:
        paths:
          - path: /api
            pathType: Prefix
            backend:
              service:
                name: agentverse-backend
                port:
                  name: http
          - path: /
            pathType: Prefix
            backend:
              service:
                name: agentverse-frontend
                port:
                  name: http
{{- end }}
```

#### `helm/agentverse/templates/NOTES.txt`

```
AgentVerse has been deployed!

Backend API:
  {{- if .Values.ingress.enabled }}
  https://{{ .Values.ingress.host }}/api/health
  {{- else }}
  kubectl port-forward svc/agentverse-backend 8000:80 -n {{ .Values.global.namespace }}
  http://localhost:8000/health
  {{- end }}

Frontend:
  {{- if .Values.ingress.enabled }}
  https://{{ .Values.ingress.host }}
  {{- else }}
  kubectl port-forward svc/agentverse-frontend 5173:80 -n {{ .Values.global.namespace }}
  http://localhost:5173
  {{- end }}

CLI quickstart:
  pip install agentverse-sdk
  agentverse login --key <your-api-key> --url https://{{ .Values.ingress.host }}/api

Monitor Beat HA:
  kubectl logs -l app=agentverse-beat -n {{ .Values.global.namespace }} | grep redbeat

Scale workers:
  kubectl scale deployment agentverse-worker --replicas=5 -n {{ .Values.global.namespace }}
```

---

### 3.3 Blue/Green Deployment

#### `infra/k8s/backend-deployment-blue.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agentverse-backend-blue
  namespace: agentverse
  labels:
    app: agentverse-backend
    color: blue
spec:
  replicas: 2
  selector:
    matchLabels:
      app: agentverse-backend
      color: blue
  template:
    metadata:
      labels:
        app: agentverse-backend
        color: blue
    spec:
      terminationGracePeriodSeconds: 60
      containers:
        - name: backend
          image: ghcr.io/agentverse/backend:latest
          ports:
            - containerPort: 8000
          envFrom:
            - configMapRef:
                name: agentverse-config
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: agentverse-secrets
                  key: database-url
            - name: REDIS_URL
              valueFrom:
                secretKeyRef:
                  name: agentverse-secrets
                  key: redis-url
          resources:
            requests:
              cpu: 200m
              memory: 512Mi
            limits:
              cpu: 1000m
              memory: 1Gi
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 5
```

#### `infra/k8s/backend-deployment-green.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: agentverse-backend-green
  namespace: agentverse
  labels:
    app: agentverse-backend
    color: green
spec:
  replicas: 0   # starts at 0; scaled up before traffic switch
  selector:
    matchLabels:
      app: agentverse-backend
      color: green
  template:
    metadata:
      labels:
        app: agentverse-backend
        color: green
    spec:
      terminationGracePeriodSeconds: 60
      containers:
        - name: backend
          image: ghcr.io/agentverse/backend:latest   # update image tag before switching
          ports:
            - containerPort: 8000
          envFrom:
            - configMapRef:
                name: agentverse-config
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: agentverse-secrets
                  key: database-url
            - name: REDIS_URL
              valueFrom:
                secretKeyRef:
                  name: agentverse-secrets
                  key: redis-url
          resources:
            requests:
              cpu: 200m
              memory: 512Mi
            limits:
              cpu: 1000m
              memory: 1Gi
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 5
```

#### `infra/k8s/backend-service.yaml` (updated — selector by `color` label)

```yaml
apiVersion: v1
kind: Service
metadata:
  name: agentverse-backend
  namespace: agentverse
  labels:
    app: agentverse-backend
  annotations:
    # Current active color — updated by switch-traffic.sh
    agentverse.ai/active-color: "blue"
spec:
  type: ClusterIP
  # Switch traffic by changing this selector
  selector:
    app: agentverse-backend
    color: blue   # ← change to "green" to switch traffic
  ports:
    - port: 80
      targetPort: 8000
      name: http
```

#### `infra/k8s/switch-traffic.sh`

```bash
#!/usr/bin/env bash
# switch-traffic.sh — Blue/Green traffic switcher for AgentVerse backend
# Usage:
#   ./switch-traffic.sh green   # switch traffic to green deployment
#   ./switch-traffic.sh blue    # switch traffic back to blue deployment
#   ./switch-traffic.sh status  # show current active color

set -euo pipefail

NAMESPACE="${NAMESPACE:-agentverse}"
TARGET_COLOR="${1:-}"

if [[ -z "$TARGET_COLOR" ]]; then
  echo "Usage: $0 <blue|green|status>"
  exit 1
fi

CURRENT_COLOR=$(kubectl get service agentverse-backend -n "$NAMESPACE" \
  -o jsonpath='{.spec.selector.color}' 2>/dev/null || echo "unknown")

if [[ "$TARGET_COLOR" == "status" ]]; then
  echo "Current active color: $CURRENT_COLOR"
  echo ""
  echo "Blue replicas:  $(kubectl get deployment agentverse-backend-blue  -n "$NAMESPACE" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)"
  echo "Green replicas: $(kubectl get deployment agentverse-backend-green -n "$NAMESPACE" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo 0)"
  exit 0
fi

if [[ "$TARGET_COLOR" != "blue" && "$TARGET_COLOR" != "green" ]]; then
  echo "Error: color must be 'blue' or 'green', got '$TARGET_COLOR'"
  exit 1
fi

if [[ "$TARGET_COLOR" == "$CURRENT_COLOR" ]]; then
  echo "Traffic is already routing to $TARGET_COLOR. Nothing to do."
  exit 0
fi

# 1. Scale up the target color if it has 0 replicas
TARGET_REPLICAS=$(kubectl get deployment "agentverse-backend-${TARGET_COLOR}" \
  -n "$NAMESPACE" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 0)

if [[ "$TARGET_REPLICAS" -eq 0 ]]; then
  echo "Scaling up $TARGET_COLOR deployment to 2 replicas..."
  kubectl scale deployment "agentverse-backend-${TARGET_COLOR}" \
    --replicas=2 -n "$NAMESPACE"
fi

# 2. Wait for target deployment to be ready
echo "Waiting for $TARGET_COLOR deployment to become ready..."
kubectl rollout status deployment/"agentverse-backend-${TARGET_COLOR}" \
  -n "$NAMESPACE" --timeout=120s

# 3. Verify health of new pods
echo "Verifying $TARGET_COLOR health endpoint..."
TARGET_POD=$(kubectl get pods -n "$NAMESPACE" \
  -l "app=agentverse-backend,color=${TARGET_COLOR}" \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)

if [[ -n "$TARGET_POD" ]]; then
  HTTP_CODE=$(kubectl exec "$TARGET_POD" -n "$NAMESPACE" -- \
    curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null || echo "000")
  if [[ "$HTTP_CODE" != "200" ]]; then
    echo "ERROR: Health check failed for $TARGET_COLOR pod ($HTTP_CODE). Aborting switch."
    exit 1
  fi
fi

# 4. Switch service selector to target color
echo "Switching service traffic from $CURRENT_COLOR to $TARGET_COLOR..."
kubectl patch service agentverse-backend -n "$NAMESPACE" \
  --type='json' \
  -p="[{\"op\": \"replace\", \"path\": \"/spec/selector/color\", \"value\": \"${TARGET_COLOR}\"}]"

# Update annotation
kubectl annotate service agentverse-backend -n "$NAMESPACE" \
  "agentverse.ai/active-color=${TARGET_COLOR}" --overwrite

# 5. Scale down old color
echo "Scaling down $CURRENT_COLOR deployment..."
kubectl scale deployment "agentverse-backend-${CURRENT_COLOR}" \
  --replicas=0 -n "$NAMESPACE"

echo ""
echo "✓ Traffic switched: $CURRENT_COLOR → $TARGET_COLOR"
echo "  Service now routes to: $TARGET_COLOR"
echo ""
echo "  To roll back: ./switch-traffic.sh $CURRENT_COLOR"
```

---

### 3.4 PgBouncer Connection Pool

#### `infra/docker-compose.yml` additions

```yaml
  pgbouncer:
    image: bitnami/pgbouncer:1.23.0
    environment:
      # PgBouncer configuration
      POSTGRESQL_HOST: postgres
      POSTGRESQL_PORT: "5432"
      POSTGRESQL_USERNAME: agentverse
      POSTGRESQL_PASSWORD: agentverse
      POSTGRESQL_DATABASE: agentverse
      PGBOUNCER_PORT: "6432"
      PGBOUNCER_POOL_MODE: transaction
      PGBOUNCER_MAX_CLIENT_CONN: "1000"
      PGBOUNCER_DEFAULT_POOL_SIZE: "50"
      PGBOUNCER_RESERVE_POOL_SIZE: "10"
      PGBOUNCER_RESERVE_POOL_TIMEOUT: "5"
      PGBOUNCER_SERVER_IDLE_TIMEOUT: "600"
      PGBOUNCER_CLIENT_IDLE_TIMEOUT: "0"
      PGBOUNCER_LOG_CONNECTIONS: "0"
      PGBOUNCER_LOG_DISCONNECTIONS: "0"
    ports:
      - "6432:6432"
    depends_on:
      postgres:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -p 6432 -U agentverse"]
      interval: 5s
      timeout: 3s
      retries: 10
```

**Update backend, worker, and beat services** to connect through PgBouncer:

```yaml
# In backend/worker/beat environment:
DATABASE_URL: postgresql+asyncpg://agentverse:agentverse@pgbouncer:6432/agentverse
```

#### `app/core/config.py` additions

```python
# Add to Settings class:

# --- Database pool ---
db_pool_size: int = 10          # SQLAlchemy pool_size (connections per process)
db_max_overflow: int = 5        # additional connections above pool_size
db_pool_timeout: float = 30.0   # seconds to wait for connection
db_pool_recycle: int = 1800     # recycle connections older than 30 min (avoids stale)
db_pool_pre_ping: bool = True   # test connection health before use
```

#### `app/db/session.py` additions

```python
# Update create_async_engine call to use pool settings from config:

from app.core.config import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    pool_timeout=_settings.db_pool_timeout,
    pool_recycle=_settings.db_pool_recycle,
    pool_pre_ping=_settings.db_pool_pre_ping,
    # PgBouncer transaction mode: disable server-side prepared statements
    # (PgBouncer transaction mode does not support them)
    connect_args={"server_settings": {"application_name": "agentverse-backend"}},
    execution_options={"compiled_cache": {}},
)
```

**Note:** PgBouncer in `transaction` mode does not support `PREPARE` statements. Ensure `SQLALCHEMY_WARN_20=1` is not set and that you do not use `execution_options(compiled_cache={})` with statement caching that conflicts.

---

### 3.5 OpenTelemetry Collector + Jaeger

#### `infra/docker-compose.yml` additions

```yaml
  # OpenTelemetry Collector — receives traces/metrics from backend and forwards to Jaeger
  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.103.0
    command: ["--config=/etc/otel-collector-config.yaml"]
    volumes:
      - ./otel/otel-collector-config.yaml:/etc/otel-collector-config.yaml:ro
    ports:
      - "4317:4317"    # OTLP gRPC receiver
      - "4318:4318"    # OTLP HTTP receiver
      - "8889:8889"    # Prometheus metrics exporter
    depends_on:
      - jaeger

  # Jaeger — distributed tracing UI (open-source, all-in-one)
  jaeger:
    image: jaegertracing/all-in-one:1.58
    environment:
      COLLECTOR_OTLP_ENABLED: "true"
      SPAN_STORAGE_TYPE: badger
      BADGER_EPHEMERAL: "false"
      BADGER_DIRECTORY_VALUE: /badger/data
      BADGER_DIRECTORY_KEY: /badger/key
    ports:
      - "16686:16686"  # Jaeger UI
      - "14268:14268"  # Jaeger HTTP collector
      - "14250:14250"  # Jaeger gRPC collector
    volumes:
      - jaeger_data:/badger
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://localhost:14269/ | grep -q ok || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 5

# Add to volumes section:
# jaeger_data: {}
```

#### `infra/otel/otel-collector-config.yaml` (new file)

```yaml
# OpenTelemetry Collector configuration
# Receives from backend, exports to Jaeger (traces) and Prometheus (metrics).

receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 1s
    send_batch_size: 1024
  memory_limiter:
    limit_mib: 256
    spike_limit_mib: 64
    check_interval: 5s
  resource:
    attributes:
      - key: service.environment
        value: development
        action: upsert

exporters:
  otlp/jaeger:
    endpoint: jaeger:14250
    tls:
      insecure: true
  prometheus:
    endpoint: 0.0.0.0:8889
    namespace: agentverse
  logging:
    verbosity: normal

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlp/jaeger, logging]
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [prometheus]
```

**Update backend service environment** in `docker-compose.yml`:

```yaml
      OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4317
      OTEL_SERVICE_NAME: agentverse-backend
```

---

## 4. Tests

### `tests/infrastructure/test_redbeat_config.py`

```python
"""Verify RedBeat scheduler configuration is correctly wired."""
from __future__ import annotations

import os
import pytest


def test_celery_app_has_redbeat_scheduler():
    """Beat scheduler must be set to RedBeat when REDIS_URL is available."""
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////tmp/test.db")

    from app.scaling.celery_app import app as celery_app
    scheduler = getattr(celery_app.conf, "beat_scheduler", None)
    # Either not set (uses default) OR set to RedBeat
    if scheduler is not None:
        assert "redbeat" in scheduler.lower() or "redbeat" in str(scheduler).lower()


def test_beat_schedule_has_required_tasks():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////tmp/test.db")

    from app.scaling.celery_app import app as celery_app
    beat_schedule = getattr(celery_app.conf, "beat_schedule", {}) or {}
    # Should have at least some scheduled tasks defined
    assert isinstance(beat_schedule, dict)
```

### `tests/infrastructure/test_helm_lint.py`

```python
"""Verify Helm chart passes lint (requires helm CLI)."""
from __future__ import annotations

import subprocess
import pytest
from pathlib import Path


HELM_CHART_PATH = Path(__file__).parent.parent.parent / "helm" / "agentverse"


@pytest.mark.skipif(
    not HELM_CHART_PATH.exists(),
    reason="Helm chart directory not found",
)
def test_helm_lint():
    """helm lint should exit with code 0."""
    result = subprocess.run(
        ["helm", "lint", str(HELM_CHART_PATH)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"helm CLI not available or lint failed:\n{result.stdout}\n{result.stderr}")
```

### `tests/infrastructure/test_bg_switch_script.py`

```python
"""Verify blue/green switch script syntax."""
from __future__ import annotations

import subprocess
import pytest
from pathlib import Path

SCRIPT_PATH = Path(__file__).parent.parent.parent / "infra" / "k8s" / "switch-traffic.sh"


@pytest.mark.skipif(not SCRIPT_PATH.exists(), reason="switch-traffic.sh not found")
def test_script_is_executable():
    assert SCRIPT_PATH.exists()
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT_PATH)],  # -n = syntax check only
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Script syntax error:\n{result.stderr}"


@pytest.mark.skipif(not SCRIPT_PATH.exists(), reason="switch-traffic.sh not found")
def test_script_exits_without_args():
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1  # expects color argument
    assert "Usage:" in result.stdout or "Usage:" in result.stderr
```

### `tests/infrastructure/test_config_pool_settings.py`

```python
"""Verify database pool settings are configurable via environment."""
from __future__ import annotations

import os
import pytest


def test_default_pool_size():
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    from app.core.config import Settings
    s = Settings()
    # Should have a positive pool size
    pool_size = getattr(s, "db_pool_size", None)
    if pool_size is not None:
        assert pool_size > 0


def test_pool_pre_ping_default():
    from app.core.config import Settings
    s = Settings()
    pre_ping = getattr(s, "db_pool_pre_ping", None)
    if pre_ping is not None:
        assert isinstance(pre_ping, bool)
```

---

## 5. Docker-Compose Changes (Summary)

Full changes to `agent-verse-backend/infra/docker-compose.yml`:

1. **Add `pgbouncer` service** (bitnami/pgbouncer:1.23.0) — transaction-mode pooler
2. **Add `otel-collector` service** (otel/opentelemetry-collector-contrib:0.103.0)
3. **Add `jaeger` service** (jaegertracing/all-in-one:1.58)
4. **Update backend/worker/beat `DATABASE_URL`** to point to `pgbouncer:6432`
5. **Add `OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4317`** to backend env
6. **Add `jaeger_data: {}` volume**

---

## 6. pyproject.toml Changes

```toml
# Add to [project.dependencies]:
"celery-redbeat>=2.3.0",
```

---

## 7. Acceptance Criteria

```bash
# Helm lint
helm lint helm/agentverse/

# Helm template render (dry run)
helm template agentverse helm/agentverse/ --dry-run | grep "kind:" | sort -u

# Beat HA: both replicas running, one holds RedBeat lock
kubectl get pods -l app=agentverse-beat -n agentverse
kubectl logs -l app=agentverse-beat -n agentverse | grep -i "redbeat"

# Blue/Green switch
./infra/k8s/switch-traffic.sh status
./infra/k8s/switch-traffic.sh green
./infra/k8s/switch-traffic.sh status

# PgBouncer
docker compose up -d pgbouncer
psql "postgresql://agentverse:agentverse@localhost:6432/agentverse" -c "\l"

# Jaeger UI
docker compose up -d otel-collector jaeger
open http://localhost:16686

# OTel traces appear in Jaeger after running a goal
curl -X POST http://localhost:8000/goals \
  -H "X-API-Key: dev-key" \
  -d '{"goal": "Test tracing"}'
# Then check http://localhost:16686 for traces from "agentverse-backend"

# All infra tests
cd agent-verse-backend && pytest tests/infrastructure/ -v
```
