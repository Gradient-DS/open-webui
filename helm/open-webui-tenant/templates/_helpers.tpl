{{/*
Expand the name of the chart.
*/}}
{{- define "open-webui-tenant.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "open-webui-tenant.fullname" -}}
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
{{- define "open-webui-tenant.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "open-webui-tenant.labels" -}}
helm.sh/chart: {{ include "open-webui-tenant.chart" . }}
{{ include "open-webui-tenant.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- if .Values.tenant.name }}
tenant: {{ .Values.tenant.name | quote }}
{{- end }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "open-webui-tenant.selectorLabels" -}}
app.kubernetes.io/name: {{ include "open-webui-tenant.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "open-webui-tenant.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "open-webui-tenant.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Component-specific names
*/}}
{{- define "open-webui-tenant.openWebui.fullname" -}}
{{- printf "%s-open-webui" (include "open-webui-tenant.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "open-webui-tenant.postgres.fullname" -}}
{{- printf "%s-postgres" (include "open-webui-tenant.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "open-webui-tenant.weaviate.fullname" -}}
{{- printf "%s-weaviate" (include "open-webui-tenant.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Component-specific labels
*/}}
{{- define "open-webui-tenant.openWebui.labels" -}}
{{ include "open-webui-tenant.labels" . }}
app.kubernetes.io/component: open-webui
{{- end }}

{{- define "open-webui-tenant.openWebui.selectorLabels" -}}
{{ include "open-webui-tenant.selectorLabels" . }}
app.kubernetes.io/component: open-webui
{{- end }}

{{- define "open-webui-tenant.postgres.labels" -}}
{{ include "open-webui-tenant.labels" . }}
app.kubernetes.io/component: postgres
{{- end }}

{{- define "open-webui-tenant.postgres.selectorLabels" -}}
{{ include "open-webui-tenant.selectorLabels" . }}
app.kubernetes.io/component: postgres
{{- end }}

{{- define "open-webui-tenant.weaviate.labels" -}}
{{ include "open-webui-tenant.labels" . }}
app.kubernetes.io/component: weaviate
{{- end }}

{{- define "open-webui-tenant.weaviate.selectorLabels" -}}
{{ include "open-webui-tenant.selectorLabels" . }}
app.kubernetes.io/component: weaviate
{{- end }}

{{/*
Image pull secrets
*/}}
{{- define "open-webui-tenant.imagePullSecrets" -}}
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
{{- define "open-webui-tenant.storageClass" -}}
{{- if .Values.global.storageClass }}
storageClassName: {{ .Values.global.storageClass }}
{{- end }}
{{- end }}

{{/*
Shared services URLs
*/}}
{{- define "open-webui-tenant.sharedServices.gatewayUrl" -}}
{{- printf "http://%s.%s.svc:%d" .Values.sharedServices.gateway.host .Values.sharedServices.namespace (int .Values.sharedServices.gateway.port) }}
{{- end }}

{{- define "open-webui-tenant.sharedServices.rerankerUrl" -}}
{{- printf "http://%s.%s.svc:%d" .Values.sharedServices.reranker.host .Values.sharedServices.namespace (int .Values.sharedServices.reranker.port) }}
{{- end }}

{{- define "open-webui-tenant.sharedServices.searxngUrl" -}}
{{- printf "http://%s.%s.svc:%d" .Values.sharedServices.searxng.host .Values.sharedServices.namespace (int .Values.sharedServices.searxng.port) }}
{{- end }}
