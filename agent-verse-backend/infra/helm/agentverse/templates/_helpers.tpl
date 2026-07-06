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
