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

{{/*
PostgreSQL host every DB consumer connects to (Open WebUI, migration job,
loader-worker, bootstrap job). Empty postgres.host = the bundled StatefulSet's
Service; set it for an external server, e.g. a CloudNativePG `<cluster>-rw`
Service in another namespace. postgres.enabled only gates the bundled
StatefulSet + Service; it never rewrites hostnames.
*/}}
{{- define "open-webui-tenant.postgres.host" -}}
{{- .Values.postgres.host | default (include "open-webui-tenant.postgres.fullname" .) -}}
{{- end }}

{{/*
PostgreSQL port every DB consumer connects to.
*/}}
{{- define "open-webui-tenant.postgres.port" -}}
{{- .Values.postgres.port | default 5432 -}}
{{- end }}

{{- define "open-webui-tenant.weaviate.fullname" -}}
{{- printf "%s-weaviate" (include "open-webui-tenant.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "open-webui-tenant.redis.fullname" -}}
{{- printf "%s-redis" (include "open-webui-tenant.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "open-webui-tenant.loaderWorker.fullname" -}}
{{- printf "%s-loader-worker" (include "open-webui-tenant.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "open-webui-tenant.syncDaemon.fullname" -}}
{{- printf "%s-sync-daemon" (include "open-webui-tenant.fullname" .) | trunc 63 | trimSuffix "-" }}
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

{{- define "open-webui-tenant.redis.labels" -}}
{{ include "open-webui-tenant.labels" . }}
app.kubernetes.io/component: redis
{{- end }}

{{- define "open-webui-tenant.redis.selectorLabels" -}}
{{ include "open-webui-tenant.selectorLabels" . }}
app.kubernetes.io/component: redis
{{- end }}

{{- define "open-webui-tenant.loaderWorker.labels" -}}
{{ include "open-webui-tenant.labels" . }}
app.kubernetes.io/component: loader-worker
{{- end }}

{{- define "open-webui-tenant.loaderWorker.selectorLabels" -}}
{{ include "open-webui-tenant.selectorLabels" . }}
app.kubernetes.io/component: loader-worker
{{- end }}

{{- define "open-webui-tenant.syncDaemon.labels" -}}
{{ include "open-webui-tenant.labels" . }}
app.kubernetes.io/component: sync-daemon
{{- end }}

{{- define "open-webui-tenant.syncDaemon.selectorLabels" -}}
{{ include "open-webui-tenant.selectorLabels" . }}
app.kubernetes.io/component: sync-daemon
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

{{/*
[Gradient] Secret-backed and extra env of the open-webui container, shared with the
v2-migrate Job so it runs with exactly the app's credentials.
*/}}
{{- define "open-webui-tenant.openWebui.env" -}}
# Secrets
- name: WEBUI_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: webui-secret-key
- name: DATABASE_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: postgres-password
- name: DATABASE_URL
  value: "postgresql://{{ .Values.postgres.user }}:$(DATABASE_PASSWORD)@{{ include "open-webui-tenant.postgres.host" . }}:{{ include "open-webui-tenant.postgres.port" . }}/{{ .Values.postgres.database }}"
- name: OPENAI_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: openai-api-key
- name: IMAGES_OPENAI_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: openai-api-key
- name: RAG_OPENAI_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: rag-openai-api-key
# Google OAuth (only if configured)
{{- if or .Values.secrets.googleClientSecret .Values.externalSecrets.onepassword.fields.googleClientSecret }}
- name: GOOGLE_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: google-client-secret
{{- end }}
{{- if or .Values.secrets.googleDriveApiKey .Values.externalSecrets.onepassword.fields.googleDriveApiKey }}
- name: GOOGLE_DRIVE_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: google-drive-api-key
{{- end }}
# Microsoft OAuth (only if configured)
{{- if or .Values.secrets.microsoftClientSecret .Values.externalSecrets.onepassword.fields.microsoftClientSecret }}
- name: MICROSOFT_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: microsoft-client-secret
{{- end }}
# Confluence / Atlassian OAuth (only if configured)
{{- if or .Values.secrets.confluenceOauthClientSecret .Values.externalSecrets.onepassword.fields.confluenceOauthClientSecret }}
- name: CONFLUENCE_OAUTH_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: confluence-oauth-client-secret
{{- end }}
# Confluence basic-auth API token (only if configured)
{{- if or .Values.secrets.confluenceBasicAuthApiToken .Values.externalSecrets.onepassword.fields.confluenceBasicAuthApiToken }}
- name: CONFLUENCE_BASIC_AUTH_API_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: confluence-basic-auth-api-token
{{- end }}
# Email Graph API secret (only if configured)
{{- if or .Values.secrets.emailGraphClientSecret .Values.externalSecrets.onepassword.fields.emailGraphClientSecret }}
- name: EMAIL_GRAPH_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: email-graph-client-secret
{{- end }}
# Feedback Reporting Slack webhook (only if configured)
{{- if or .Values.secrets.feedbackReportSlackWebhookUrl .Values.externalSecrets.onepassword.fields.feedbackReportSlackWebhookUrl }}
- name: FEEDBACK_REPORT_SLACK_WEBHOOK_URL
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: feedback-report-slack-webhook-url
{{- end }}
# External Services API Keys (only if configured)
{{- if or .Values.secrets.externalDocumentLoaderApiKey .Values.externalSecrets.onepassword.fields.externalDocumentLoaderApiKey }}
- name: EXTERNAL_DOCUMENT_LOADER_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: external-document-loader-api-key
{{- end }}
{{- if or .Values.secrets.ragExternalRerankerApiKey .Values.externalSecrets.onepassword.fields.ragExternalRerankerApiKey }}
- name: RAG_EXTERNAL_RERANKER_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: rag-external-reranker-api-key
{{- end }}
{{- if .Values.soevApi.url }}
- name: SOEV_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: soev-api-key
- name: SOEV_API_SIGNING_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: soev-api-signing-key
{{- end }}
# Agent API key — single shared secret used by both:
#   - open-webui → agent (outbound; the agent-proxy / chat path)
#   - agent → open-webui (inbound; gates /api/v1/internal/retrieval/*)
# Mounted whenever either direction is enabled and a value exists
# in the tenant secret store.
{{- if and (or (eq .Values.openWebui.config.agentApiEnabled "true") .Values.agentSearch.enabled) (or .Values.secrets.agentApiKey .Values.externalSecrets.onepassword.fields.agentApiKey) }}
- name: AGENT_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: agent-api-key
{{- end }}
# Search API X-API-Key — outbound credential for the discovery proxy
# at /api/v1/discovery/*. Mounted whenever a value exists in either
# secret path, regardless of baseUrl (router gates on baseUrl at
# request time).
{{- if or .Values.secrets.searchApiKey .Values.externalSecrets.onepassword.fields.searchApiKey }}
- name: SEARCH_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: search-api-key
{{- end }}
# Loader-worker → /ingest callback bearer (validated by service_auth.LoaderPrincipal)
{{- if or .Values.secrets.loaderIngestApiKey .Values.externalSecrets.onepassword.fields.loaderIngestApiKey }}
- name: LOADER_INGEST_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: loader-ingest-api-key
{{- end }}
# Distributed doc-pipeline submit bearer (matches pipeline-api inbound PIPELINE_API_KEY)
{{- if or .Values.secrets.pipelineApiKey .Values.externalSecrets.onepassword.fields.pipelineApiKey }}
- name: PIPELINE_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: pipeline-api-key
{{- end }}
# Sync machine key — OWUI validates the daemon inbound (SyncPrincipal
# on /sync/diff, /sync/cleanup, /api/v1/sync-daemon/*, D-5). Mounted
# whenever a value exists; the sync_daemon.enabled flag gates use.
{{- if or .Values.secrets.syncApiKey .Values.externalSecrets.onepassword.fields.syncApiKey }}
- name: SYNC_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: sync-api-key
{{- end }}
# Sync-daemon bearer — OWUI presents this to the daemon on the manual
# "Sync now" / cancel forwards (services/sync/daemon_client). Mounted
# whenever a value exists; SYNC_DAEMON_URL (configmap) gates use.
{{- if or .Values.secrets.syncDaemonApiKey .Values.externalSecrets.onepassword.fields.syncDaemonApiKey }}
- name: SYNC_DAEMON_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: sync-daemon-api-key
{{- end }}
# S3 Storage credentials (only if configured)
{{- if or .Values.secrets.s3AccessKeyId .Values.externalSecrets.onepassword.fields.s3AccessKeyId }}
- name: S3_ACCESS_KEY_ID
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: s3-access-key-id
{{- end }}
{{- if or .Values.secrets.s3SecretAccessKey .Values.externalSecrets.onepassword.fields.s3AccessKeyId }}
- name: S3_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "open-webui-tenant.fullname" . }}-secrets
      key: s3-secret-access-key
{{- end }}
{{- with .Values.extraEnv }}
{{- toYaml . | nindent 0 }}
{{- end }}
{{- end }}
