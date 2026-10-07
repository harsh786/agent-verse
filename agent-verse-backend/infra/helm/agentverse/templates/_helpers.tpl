{{- define "agentverse.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "agentverse.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "agentverse.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "agentverse.labels" -}}
app.kubernetes.io/name: {{ include "agentverse.name" . }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: agentverse
{{- end -}}

{{- define "agentverse.selectorLabels" -}}
app.kubernetes.io/name: {{ include "agentverse.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "agentverse.secretName" -}}
{{ include "agentverse.fullname" . }}-secrets
{{- end -}}

{{- /*
  BYOK-1: the vault master key (+ rotation companion) for EVERY app workload —
  API, workers, beat. The API encrypts tenant BYOK keys with it and the workers
  decrypt them; a workload without it (the worker used to be) decrypts with no /
  another key and every BYOK run fails. Enforced by
  tests/infra/test_vault_key_distribution.py.
*/}}
{{- define "agentverse.vaultEnv" -}}
- name: VAULT_MASTER_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "agentverse.secretName" . }}
      key: VAULT_MASTER_KEY
- name: VAULT_PREVIOUS_MASTER_KEYS
  valueFrom:
    secretKeyRef:
      name: {{ include "agentverse.secretName" . }}
      key: VAULT_PREVIOUS_MASTER_KEYS
      optional: true
{{- end -}}

{{- /*
  NF-15: the app secrets EVERY app workload gets — API, workers, sub-goal
  workers, beat — from one place, so a worker can never again run without a
  secret the API has (it used to lack MinIO, JWT, goal-token and manifest
  secrets: artifact uploads, training exports, HITL links and manifest checks
  failed only on workers). Provider keys and SMTP credentials are optional
  (BYOK-only / no-mail deployments). API-only secrets (PLATFORM_ADMIN_KEY) are
  added by the backend alone. Enforced by tests/infra/test_vault_key_distribution.py.

  NF-16, the three database roles (repo-root CLAUDE.md): DATABASE_URL is the
  least-privilege APPLICATION role (postgresql.appUsername: NOSUPERUSER,
  NOBYPASSRLS — created/repaired by the migrate Job), MAINTENANCE_DATABASE_URL
  the BYPASSRLS role the cross-tenant system jobs use (beat scans executed by
  the workers, the API's startup warm-up; default: the owner), and the owner
  DSN (MIGRATION_DATABASE_URL) is given to the migrate Job ONLY.
*/}}
{{- define "agentverse.appSecretEnv" -}}
- name: APP_DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "agentverse.secretName" . }}
      key: APP_DB_PASSWORD
- name: DATABASE_URL
  value: postgresql+asyncpg://{{ .Values.postgresql.appUsername }}:$(APP_DB_PASSWORD)@{{ include "agentverse.postgresHost" . }}:{{ .Values.postgresql.service.port }}/{{ .Values.postgresql.database }}
- name: MAINTENANCE_DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "agentverse.secretName" . }}
      key: MAINTENANCE_DB_PASSWORD
- name: MAINTENANCE_DATABASE_URL
  value: postgresql+asyncpg://{{ include "agentverse.maintenanceUsername" . }}:$(MAINTENANCE_DB_PASSWORD)@{{ include "agentverse.postgresHost" . }}:{{ .Values.postgresql.service.port }}/{{ .Values.postgresql.database }}
- name: REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "agentverse.secretName" . }}
      key: REDIS_PASSWORD
- name: REDIS_URL
  value: redis://:$(REDIS_PASSWORD)@{{ include "agentverse.redisHost" . }}:{{ .Values.redis.service.port }}/0
- name: MINIO_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "agentverse.secretName" . }}
      key: MINIO_ACCESS_KEY
- name: MINIO_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "agentverse.secretName" . }}
      key: MINIO_SECRET_KEY
- name: JWT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "agentverse.secretName" . }}
      key: JWT_SECRET
{{ include "agentverse.vaultEnv" . }}
- name: GOAL_TOKEN_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "agentverse.secretName" . }}
      key: GOAL_TOKEN_SECRET
- name: MANIFEST_SIGNING_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "agentverse.secretName" . }}
      key: MANIFEST_SIGNING_SECRET
- name: ANTHROPIC_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "agentverse.secretName" . }}
      key: ANTHROPIC_API_KEY
      optional: true
- name: OPENAI_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "agentverse.secretName" . }}
      key: OPENAI_API_KEY
      optional: true
- name: VOYAGE_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "agentverse.secretName" . }}
      key: VOYAGE_API_KEY
      optional: true
- name: GOOGLE_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "agentverse.secretName" . }}
      key: GOOGLE_API_KEY
      optional: true
- name: NVIDIA_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "agentverse.secretName" . }}
      key: NVIDIA_API_KEY
      optional: true
- name: EMBEDDING_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "agentverse.secretName" . }}
      key: EMBEDDING_API_KEY
      optional: true
- name: SMTP_USER
  valueFrom:
    secretKeyRef:
      name: {{ include "agentverse.secretName" . }}
      key: SMTP_USER
      optional: true
- name: SMTP_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "agentverse.secretName" . }}
      key: SMTP_PASSWORD
      optional: true
{{- end -}}

{{- /*
  The code-sandbox runner (templates/code-sandbox.yaml) for the workloads that
  execute tenant code: the API (/tools/execute-code, chat) and the goal/workflow
  workers (workflow code steps, the code tool). They have no Docker daemon and
  must never get the node's socket; with this they send code to the runner.
  Enforced by tests/infra/test_code_sandbox_deploy.py.
*/}}
{{- define "agentverse.codeSandboxEnv" -}}
{{- if .Values.codeSandbox.enabled }}
- name: CODE_SANDBOX_URL
  value: "http://{{ include "agentverse.fullname" . }}-code-sandbox:{{ .Values.codeSandbox.service.port }}"
- name: CODE_SANDBOX_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "agentverse.secretName" . }}
      key: CODE_SANDBOX_TOKEN
{{- end }}
{{- end -}}

{{- define "agentverse.maintenanceUsername" -}}
{{- default .Values.postgresql.username .Values.postgresql.maintenanceUsername -}}
{{- end -}}

{{- define "agentverse.postgresHost" -}}
{{- if and (not .Values.postgresql.enabled) .Values.externalServices.postgresHost -}}
{{- .Values.externalServices.postgresHost -}}
{{- else -}}
{{ include "agentverse.fullname" . }}-postgres
{{- end -}}
{{- end -}}

{{- define "agentverse.redisHost" -}}
{{- if and (not .Values.redis.enabled) .Values.externalServices.redisHost -}}
{{- .Values.externalServices.redisHost -}}
{{- else -}}
{{ include "agentverse.fullname" . }}-redis
{{- end -}}
{{- end -}}

{{- define "agentverse.minioEndpoint" -}}
{{- if and (not .Values.minio.enabled) .Values.externalServices.minioEndpoint -}}
{{- .Values.externalServices.minioEndpoint -}}
{{- else -}}
http://{{ include "agentverse.fullname" . }}-minio:{{ .Values.minio.service.apiPort }}
{{- end -}}
{{- end -}}
