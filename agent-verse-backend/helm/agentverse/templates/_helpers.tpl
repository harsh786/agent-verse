{{/*
Expand the name of the chart.
*/}}
{{- define "agentverse.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Full release name.
*/}}
{{- define "agentverse.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "agentverse.labels" -}}
helm.sh/chart: {{ include "agentverse.name" . }}-{{ .Chart.Version }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "agentverse.selectorLabels" -}}
app.kubernetes.io/name: {{ include "agentverse.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Full image references
*/}}
{{- define "agentverse.backendImage" -}}
{{ .Values.global.imageRegistry }}/{{ .Values.backend.image.name }}:{{ .Values.backend.image.tag }}
{{- end }}

{{- define "agentverse.workerImage" -}}
{{ .Values.global.imageRegistry }}/{{ .Values.worker.image.name }}:{{ .Values.worker.image.tag }}
{{- end }}

{{- define "agentverse.subgoalWorkerImage" -}}
{{ .Values.global.imageRegistry }}/{{ .Values.subgoalWorker.image.name }}:{{ .Values.subgoalWorker.image.tag }}
{{- end }}

{{- define "agentverse.frontendImage" -}}
{{ .Values.global.imageRegistry }}/{{ .Values.frontend.image.name }}:{{ .Values.frontend.image.tag }}
{{- end }}

{{/*
NF-15: the app secrets EVERY app workload gets (API, workers, sub-goal workers,
beat) from one helper, so a worker never lacks a secret the API has. The workers
had only DATABASE_URL / REDIS_URL: no platform LLM key and no vault master key
(which the API passed as MASTER_ENCRYPTION_KEY, a name the vault never reads —
it reads VAULT_MASTER_KEY). Enforced by tests/infra/test_vault_key_distribution.py.
*/}}
{{- define "agentverse.appSecretEnv" -}}
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
- name: VAULT_MASTER_KEY
  valueFrom:
    secretKeyRef:
      name: agentverse-secrets
      key: master-encryption-key
{{- end }}
