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
{{ include "agentverse.fullname" . }}-postgres
{{- end -}}

{{- define "agentverse.redisHost" -}}
{{ include "agentverse.fullname" . }}-redis
{{- end -}}
