{{/*
Expand the name of the chart.
*/}}
{{- define "open-webui-stack.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "open-webui-stack.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "open-webui-stack.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "open-webui-stack.labels" -}}
helm.sh/chart: {{ include "open-webui-stack.chart" . }}
{{ include "open-webui-stack.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "open-webui-stack.selectorLabels" -}}
app.kubernetes.io/name: {{ include "open-webui-stack.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "open-webui-stack.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "open-webui-stack.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Component-specific names
*/}}
{{- define "open-webui-stack.openWebui.fullname" -}}
{{- printf "%s-open-webui" (include "open-webui-stack.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "open-webui-stack.postgres.fullname" -}}
{{- printf "%s-postgres" (include "open-webui-stack.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "open-webui-stack.weaviate.fullname" -}}
{{- printf "%s-weaviate" (include "open-webui-stack.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "open-webui-stack.gateway.fullname" -}}
{{- printf "%s-gateway" (include "open-webui-stack.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "open-webui-stack.searxng.fullname" -}}
{{- printf "%s-searxng" (include "open-webui-stack.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "open-webui-stack.crawl4ai.fullname" -}}
{{- printf "%s-crawl4ai" (include "open-webui-stack.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "open-webui-stack.reranker.fullname" -}}
{{- printf "%s-reranker" (include "open-webui-stack.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Component-specific labels
*/}}
{{- define "open-webui-stack.openWebui.labels" -}}
{{ include "open-webui-stack.labels" . }}
app.kubernetes.io/component: open-webui
{{- end }}

{{- define "open-webui-stack.openWebui.selectorLabels" -}}
{{ include "open-webui-stack.selectorLabels" . }}
app.kubernetes.io/component: open-webui
{{- end }}

{{- define "open-webui-stack.postgres.labels" -}}
{{ include "open-webui-stack.labels" . }}
app.kubernetes.io/component: postgres
{{- end }}

{{- define "open-webui-stack.postgres.selectorLabels" -}}
{{ include "open-webui-stack.selectorLabels" . }}
app.kubernetes.io/component: postgres
{{- end }}

{{- define "open-webui-stack.weaviate.labels" -}}
{{ include "open-webui-stack.labels" . }}
app.kubernetes.io/component: weaviate
{{- end }}

{{- define "open-webui-stack.weaviate.selectorLabels" -}}
{{ include "open-webui-stack.selectorLabels" . }}
app.kubernetes.io/component: weaviate
{{- end }}

{{- define "open-webui-stack.gateway.labels" -}}
{{ include "open-webui-stack.labels" . }}
app.kubernetes.io/component: gateway
{{- end }}

{{- define "open-webui-stack.gateway.selectorLabels" -}}
{{ include "open-webui-stack.selectorLabels" . }}
app.kubernetes.io/component: gateway
{{- end }}

{{- define "open-webui-stack.searxng.labels" -}}
{{ include "open-webui-stack.labels" . }}
app.kubernetes.io/component: searxng
{{- end }}

{{- define "open-webui-stack.searxng.selectorLabels" -}}
{{ include "open-webui-stack.selectorLabels" . }}
app.kubernetes.io/component: searxng
{{- end }}

{{- define "open-webui-stack.crawl4ai.labels" -}}
{{ include "open-webui-stack.labels" . }}
app.kubernetes.io/component: crawl4ai
{{- end }}

{{- define "open-webui-stack.crawl4ai.selectorLabels" -}}
{{ include "open-webui-stack.selectorLabels" . }}
app.kubernetes.io/component: crawl4ai
{{- end }}

{{- define "open-webui-stack.reranker.labels" -}}
{{ include "open-webui-stack.labels" . }}
app.kubernetes.io/component: reranker
{{- end }}

{{- define "open-webui-stack.reranker.selectorLabels" -}}
{{ include "open-webui-stack.selectorLabels" . }}
app.kubernetes.io/component: reranker
{{- end }}

{{/*
Image pull secrets
*/}}
{{- define "open-webui-stack.imagePullSecrets" -}}
{{- if .Values.global.imagePullSecrets }}
imagePullSecrets:
{{- range .Values.global.imagePullSecrets }}
  - name: {{ . }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Storage class
*/}}
{{- define "open-webui-stack.storageClass" -}}
{{- if .Values.global.storageClass }}
storageClassName: {{ .Values.global.storageClass }}
{{- end }}
{{- end }}
