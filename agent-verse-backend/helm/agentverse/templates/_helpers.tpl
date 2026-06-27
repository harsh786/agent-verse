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

{{- define "agentverse.frontendImage" -}}
{{ .Values.global.imageRegistry }}/{{ .Values.frontend.image.name }}:{{ .Values.frontend.image.tag }}
{{- end }}
