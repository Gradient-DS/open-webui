# Observability, Secrets, and GitOps Implementation Plan

## Overview

Implement the Haven+ observability stack (Mimir, Loki, Tempo, Alloy), External Secrets Operator with GCP Secret Manager backend, and FluxCD GitOps for the open-webui deployment on GKE.

**Key Architecture Decision: Multi-Tenant Observability**

The observability stack uses a **hybrid multi-tenant architecture**:
- **Central backends** (Mimir, Loki, Tempo) in `observability` namespace with multi-tenancy enabled
- **Per-deployment Alloy** sends data with tenant ID (`X-Scope-OrgID` header)
- **Admin Grafana** at `grafana.soev.ai` sees all tenants
- **Per-tenant Grafana** (future) can be added for enterprise customers with isolated views

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              GKE Cluster                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐            │
│  │ open-webui ns   │   │ enterprise-a ns │   │ enterprise-b ns │            │
│  │                 │   │                 │   │                 │            │
│  │  Open WebUI     │   │  Open WebUI     │   │  Open WebUI     │            │
│  │  (tenant:soev)  │   │  (tenant:ent-a) │   │  (tenant:ent-b) │            │
│  │       │         │   │       │         │   │       │         │            │
│  │       ▼         │   │       ▼         │   │       ▼         │            │
│  │     Alloy       │   │     Alloy       │   │     Alloy       │            │
│  │ X-Scope-OrgID   │   │ X-Scope-OrgID   │   │ X-Scope-OrgID   │            │
│  │   = "soev"      │   │   = "ent-a"     │   │   = "ent-b"     │            │
│  └────────┬────────┘   └────────┬────────┘   └────────┬────────┘            │
│           │                     │                     │                      │
│           └─────────────────────┼─────────────────────┘                      │
│                                 │                                            │
│                                 ▼                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                      observability namespace                          │   │
│  │                                                                       │   │
│  │   ┌─────────┐      ┌─────────┐      ┌─────────┐      ┌─────────┐    │   │
│  │   │  Mimir  │      │  Loki   │      │  Tempo  │      │ Grafana │    │   │
│  │   │(metrics)│      │ (logs)  │      │(traces) │      │ (admin) │    │   │
│  │   │         │      │         │      │         │      │         │    │   │
│  │   │ multi-  │      │ multi-  │      │ multi-  │      │ sees    │    │   │
│  │   │ tenant  │      │ tenant  │      │ tenant  │      │ all     │    │   │
│  │   └─────────┘      └─────────┘      └─────────┘      └─────────┘    │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Current State Analysis

| Component | Current State | Target State |
|-----------|--------------|--------------|
| **Observability** | OTEL backend support exists but not exposed in Helm | Full telemetry pipeline to Mimir/Loki/Tempo via Alloy |
| **Secrets** | Inline Helm secrets with `values-secrets.yaml` | External Secrets Operator syncing from GCP Secret Manager |
| **GitOps** | Manual `helm upgrade` | FluxCD automated deployments |

### Key Code References

| Area | File | Notes |
|------|------|-------|
| OTEL variables | `backend/open_webui/env.py:808-874` | 25+ OTEL environment variables |
| Current secrets | `helm/open-webui-stack/templates/secrets.yaml` | 8 secret keys |
| Deployment secrets | `helm/open-webui-stack/templates/open-webui/deployment.yaml:34-69` | secretKeyRef pattern |
| ConfigMap | `helm/open-webui-stack/templates/open-webui/configmap.yaml` | No OTEL variables |
| Production values | `helm/open-webui-stack/values-prod.yaml` | GKE cluster: `demo-cluster`, project: `soev-ai-001` |

## Desired End State

After completing this plan:

1. **Observability**: Open WebUI sends traces, metrics, and logs via OTLP to Alloy, which routes them to Tempo, Mimir, and Loki respectively. Grafana dashboards visualize all three signals with trace-to-log correlation.

2. **Secrets**: All secrets are stored in GCP Secret Manager and synced to Kubernetes via External Secrets Operator. No secrets in Git, automatic rotation support.

3. **GitOps**: FluxCD watches the repository and automatically deploys changes to the cluster. Image updates trigger automatic rollouts.

### Verification Checklist
- [ ] `kubectl get externalsecrets -n open-webui` shows `SecretSynced` status
- [ ] `kubectl logs -n open-webui deployment/open-webui | grep -i "OTEL"` shows telemetry initialization
- [ ] Grafana Explore shows traces, metrics, and logs from `open-webui` service
- [ ] `flux get sources git` shows healthy sync status
- [ ] `flux get helmreleases -A` shows successful reconciliation

## What We're NOT Doing

- **Loki external storage**: Using filesystem storage initially (no S3/Azure Blob yet)
- **HashiCorp Vault**: Using GCP Secret Manager as simpler alternative
- **Multi-cluster**: Single cluster deployment only
- **HA mode**: Using monolithic/single-binary mode for Mimir/Loki/Tempo initially
- **Alerting**: No Alertmanager configuration in this plan

## Implementation Approach

The implementation follows a bottom-up dependency order:

```
Phase 1: Infrastructure Namespace & ESO Setup
    ↓
Phase 2: GCP Secret Manager + External Secrets
    ↓
Phase 3: Observability Stack (Mimir/Loki/Tempo/Alloy)
    ↓
Phase 4: Helm Chart Updates (OTEL + ESO integration)
    ↓
Phase 5: FluxCD GitOps Bootstrap
```

---

## Phase 1: Infrastructure Namespaces and External Secrets Operator

### Overview
Create infrastructure namespaces and install External Secrets Operator as the foundation for secrets management.

### Changes Required:

#### 1. Create Namespace Manifests

**File**: `helm/flux/infrastructure/namespaces.yaml` (new)

```yaml
---
apiVersion: v1
kind: Namespace
metadata:
  name: external-secrets
  labels:
    app.kubernetes.io/managed-by: flux
---
apiVersion: v1
kind: Namespace
metadata:
  name: observability
  labels:
    app.kubernetes.io/managed-by: flux
```

#### 2. Install External Secrets Operator

**Commands**:
```bash
# Add Helm repository
helm repo add external-secrets https://charts.external-secrets.io
helm repo update

# Install ESO in external-secrets namespace
helm install external-secrets external-secrets/external-secrets \
  -n external-secrets \
  --create-namespace \
  --set installCRDs=true \
  --set webhook.port=9443
```

### Success Criteria:

#### Automated Verification:
- [ ] Namespace exists: `kubectl get namespace external-secrets`
- [ ] Namespace exists: `kubectl get namespace observability`
- [ ] ESO pods running: `kubectl get pods -n external-secrets`
- [ ] CRDs installed: `kubectl get crd externalsecrets.external-secrets.io`

#### Manual Verification:
- [ ] ESO webhook is responding: `kubectl logs -n external-secrets deployment/external-secrets-webhook`

**Implementation Note**: Pause here for confirmation before proceeding to Phase 2.

---

## Phase 2: GCP Secret Manager Integration

### Overview
Configure GCP Secret Manager as the secrets backend and create SecretStore + ExternalSecret resources.

### Prerequisites
- `gcloud` CLI installed and authenticated
- Access to GCP project `soev-ai-001`
- GKE cluster with Workload Identity enabled

### Changes Required:

#### 1. Verify GKE Workload Identity

First, check that Workload Identity is enabled on your GKE cluster:

**Commands**:
```bash
# Check if Workload Identity is enabled on the cluster
gcloud container clusters describe demo-cluster \
  --zone=europe-west4-a \
  --project=soev-ai-001 \
  --format="value(workloadIdentityConfig.workloadPool)"

# Expected output: soev-ai-001.svc.id.goog
# If empty, Workload Identity needs to be enabled first
```

If Workload Identity is not enabled, enable it:
```bash
gcloud container clusters update demo-cluster \
  --zone=europe-west4-a \
  --project=soev-ai-001 \
  --workload-pool=soev-ai-001.svc.id.goog
```

#### 2. Enable GCP Secret Manager API

**Commands**:
```bash
# Enable Secret Manager API in GCP project
gcloud services enable secretmanager.googleapis.com --project=soev-ai-001

# Verify it's enabled
gcloud services list --enabled --project=soev-ai-001 | grep secretmanager
```

#### 3. Create GCP Service Account for ESO

**Commands**:
```bash
# Create service account for ESO
gcloud iam service-accounts create external-secrets-sa \
  --display-name="External Secrets Operator" \
  --project=soev-ai-001

# Grant Secret Manager accessor role
gcloud projects add-iam-policy-binding soev-ai-001 \
  --member="serviceAccount:external-secrets-sa@soev-ai-001.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

# Create Workload Identity binding (connects K8s SA to GCP SA)
gcloud iam service-accounts add-iam-policy-binding external-secrets-sa@soev-ai-001.iam.gserviceaccount.com \
  --role="roles/iam.workloadIdentityUser" \
  --member="serviceAccount:soev-ai-001.svc.id.goog[external-secrets/external-secrets]" \
  --project=soev-ai-001

# Verify the binding
gcloud iam service-accounts get-iam-policy external-secrets-sa@soev-ai-001.iam.gserviceaccount.com \
  --project=soev-ai-001
```

#### 4. Annotate ESO Service Account for Workload Identity

**Commands**:
```bash
# Annotate the Kubernetes service account to use the GCP service account
kubectl annotate serviceaccount external-secrets \
  -n external-secrets \
  iam.gke.io/gcp-service-account=external-secrets-sa@soev-ai-001.iam.gserviceaccount.com \
  --overwrite

# Verify the annotation
kubectl get serviceaccount external-secrets -n external-secrets -o yaml | grep -A2 annotations
```

#### 5. Create Secrets in GCP Secret Manager

**Commands**:
```bash
# Create all required secrets
for secret in webui-secret-key admin-password openai-api-key rag-openai-api-key postgres-password searxng-secret-key; do
  gcloud secrets create "open-webui-${secret}" --project=soev-ai-001 --replication-policy=automatic
  echo "Created secret: open-webui-${secret}"
done

# Now add the actual secret values
# IMPORTANT: Use your actual values from values-secrets.yaml

# Generate new random values for keys (recommended for fresh install)
echo -n "$(openssl rand -hex 32)" | gcloud secrets versions add open-webui-webui-secret-key --data-file=- --project=soev-ai-001
echo -n "$(openssl rand -hex 16)" | gcloud secrets versions add open-webui-postgres-password --data-file=- --project=soev-ai-001
echo -n "$(openssl rand -hex 16)" | gcloud secrets versions add open-webui-searxng-secret-key --data-file=- --project=soev-ai-001

# Set admin password (change this!)
echo -n "your-secure-admin-password" | gcloud secrets versions add open-webui-admin-password --data-file=- --project=soev-ai-001

# Set API keys (copy from existing values-secrets.yaml)
echo -n "hf_your_huggingface_token" | gcloud secrets versions add open-webui-openai-api-key --data-file=- --project=soev-ai-001
echo -n "sk-proj-your_openai_key" | gcloud secrets versions add open-webui-rag-openai-api-key --data-file=- --project=soev-ai-001

# Verify secrets were created
gcloud secrets list --project=soev-ai-001 --filter="name:open-webui"
```

#### 6. Verify Secret Access (Test)

**Commands**:
```bash
# Test that you can read the secrets
gcloud secrets versions access latest --secret=open-webui-admin-password --project=soev-ai-001

# Test from inside the cluster (after ESO annotation is done)
kubectl run test-wi --rm -it --restart=Never \
  --image=google/cloud-sdk:slim \
  --serviceaccount=external-secrets \
  --namespace=external-secrets \
  -- gcloud secrets versions access latest --secret=open-webui-admin-password --project=soev-ai-001
```

#### 7. Create ClusterSecretStore

**File**: `helm/open-webui-stack/templates/cluster-secret-store.yaml` (new)

```yaml
{{- if .Values.externalSecrets.enabled }}
apiVersion: external-secrets.io/v1beta1
kind: ClusterSecretStore
metadata:
  name: gcp-secret-manager
spec:
  provider:
    gcpsm:
      projectID: {{ .Values.externalSecrets.gcpProject | quote }}
{{- end }}
```

#### 8. Create ExternalSecret Resource

**File**: `helm/open-webui-stack/templates/external-secrets.yaml` (new)

```yaml
{{- if .Values.externalSecrets.enabled }}
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: {{ include "open-webui-stack.fullname" . }}-secrets
  labels:
    {{- include "open-webui-stack.labels" . | nindent 4 }}
spec:
  refreshInterval: {{ .Values.externalSecrets.refreshInterval | default "1h" }}
  secretStoreRef:
    name: gcp-secret-manager
    kind: ClusterSecretStore
  target:
    name: {{ include "open-webui-stack.fullname" . }}-secrets
    creationPolicy: Owner
  data:
    - secretKey: webui-secret-key
      remoteRef:
        key: open-webui-webui-secret-key
    - secretKey: admin-password
      remoteRef:
        key: open-webui-admin-password
    - secretKey: openai-api-key
      remoteRef:
        key: open-webui-openai-api-key
    - secretKey: rag-openai-api-key
      remoteRef:
        key: open-webui-rag-openai-api-key
    - secretKey: postgres-password
      remoteRef:
        key: open-webui-postgres-password
    - secretKey: searxng-secret-key
      remoteRef:
        key: open-webui-searxng-secret-key
{{- end }}
```

#### 9. Update secrets.yaml for Conditional Rendering

**File**: `helm/open-webui-stack/templates/secrets.yaml`
**Changes**: Wrap entire content with condition

```yaml
{{- if not .Values.externalSecrets.enabled }}
apiVersion: v1
kind: Secret
metadata:
  name: {{ include "open-webui-stack.fullname" . }}-secrets
  labels:
    {{- include "open-webui-stack.labels" . | nindent 4 }}
type: Opaque
stringData:
  # ... existing secret definitions ...
{{- end }}
```

#### 10. Add External Secrets Values

**File**: `helm/open-webui-stack/values.yaml`
**Changes**: Add new section after `serviceAccount`

```yaml
# External Secrets Operator configuration
externalSecrets:
  enabled: false
  gcpProject: ""
  refreshInterval: "1h"
```

**File**: `helm/open-webui-stack/values-prod.yaml`
**Changes**: Add ESO configuration

```yaml
externalSecrets:
  enabled: true
  gcpProject: "soev-ai-001"
  refreshInterval: "1h"
```

### Success Criteria:

#### Automated Verification:
- [ ] ClusterSecretStore ready: `kubectl get clustersecretstores gcp-secret-manager`
- [ ] ExternalSecret synced: `kubectl get externalsecrets -n open-webui`
- [ ] Secret created: `kubectl get secret open-webui-stack-secrets -n open-webui`
- [ ] Helm template renders: `helm template helm/open-webui-stack -f helm/open-webui-stack/values-prod.yaml | grep -A20 "kind: ExternalSecret"`

#### Manual Verification:
- [ ] Secret values match GCP Secret Manager: compare `kubectl get secret -o yaml` with GCP console
- [ ] Application starts successfully with ESO-managed secrets

**Implementation Note**: Pause here for confirmation before proceeding to Phase 3.

---

## Phase 3: Observability Stack Deployment

### Overview
Deploy Grafana Alloy as the OTLP collector, with Mimir (metrics), Loki (logs), and Tempo (traces) as backends. Use monolithic mode with **multi-tenancy enabled** for future enterprise deployments.

### Multi-Tenancy Design

All backends support multi-tenancy via `X-Scope-OrgID` header:
- **soev**: Default tenant for soev.ai deployment
- **enterprise-{name}**: Future tenants for enterprise customers

Alloy injects the tenant ID into all telemetry data, allowing data isolation at the storage level while using shared infrastructure.

### Changes Required:

#### 1. Create Observability Helm Values Directory

**Directory**: `helm/observability/` (new)

```bash
mkdir -p helm/observability
```

#### 2. Create Documentation

**File**: `helm/observability/README.md` (new)

```markdown
# Observability Stack

This directory contains Helm values for the central observability stack.

## Architecture

Central multi-tenant observability using Grafana LGTM stack:
- **Mimir**: Prometheus-compatible metrics storage (multi-tenant)
- **Loki**: Log aggregation (multi-tenant)
- **Tempo**: Distributed tracing (multi-tenant)
- **Alloy**: OpenTelemetry collector and log shipper
- **Grafana**: Visualization (admin instance)

## Multi-Tenancy

All backends use `X-Scope-OrgID` header for tenant isolation:
- `soev` - Main soev.ai deployment
- `enterprise-{name}` - Enterprise customer deployments

Each deployment's Alloy instance injects the appropriate tenant ID.

## Deployment

```bash
# Add Grafana Helm repository
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

# Deploy in order
helm install mimir grafana/mimir-distributed -n observability -f mimir-values.yaml --wait
helm install loki grafana/loki -n observability -f loki-values.yaml --wait
helm install tempo grafana/tempo -n observability -f tempo-values.yaml --wait
helm install alloy grafana/alloy -n observability -f alloy-values.yaml --wait
helm install grafana grafana/grafana -n observability -f grafana-values.yaml --wait
```

## Access

- **Grafana**: https://grafana.soev.ai (admin console)
- **Port-forward**: `kubectl port-forward -n observability svc/grafana 3000:80`

## Adding New Tenants

1. No backend changes needed (multi-tenancy is automatic)
2. Deploy Alloy in the new namespace with tenant ID configured
3. Optionally deploy tenant-specific Grafana instance

## Files

| File | Purpose |
|------|---------|
| `mimir-values.yaml` | Metrics storage configuration |
| `loki-values.yaml` | Log storage configuration |
| `tempo-values.yaml` | Trace storage configuration |
| `alloy-values.yaml` | Central Alloy for observability namespace |
| `grafana-values.yaml` | Admin Grafana configuration |
```

#### 3. Alloy Configuration (Multi-Tenant)

**File**: `helm/observability/alloy-values.yaml` (new)

```yaml
# Central Alloy instance for observability namespace
# Note: Each application namespace will have its own Alloy sidecar/deployment
# This Alloy collects logs from the observability namespace itself

alloy:
  configMap:
    create: true
    content: |
      // ======================
      // OTLP Receiver (from apps)
      // ======================
      otelcol.receiver.otlp "default" {
        grpc {
          endpoint = "0.0.0.0:4317"
        }
        http {
          endpoint = "0.0.0.0:4318"
        }
        output {
          metrics = [otelcol.processor.batch.default.input]
          logs    = [otelcol.processor.batch.default.input]
          traces  = [otelcol.processor.batch.default.input]
        }
      }

      // ======================
      // Batch Processor
      // ======================
      otelcol.processor.batch "default" {
        output {
          metrics = [otelcol.exporter.prometheusremotewrite.mimir.input]
          logs    = [otelcol.exporter.loki.default.input]
          traces  = [otelcol.exporter.otlp.tempo.input]
        }
      }

      // ======================
      // Export Metrics to Mimir (multi-tenant)
      // ======================
      otelcol.exporter.prometheusremotewrite "mimir" {
        endpoint {
          url = "http://mimir.observability.svc:9009/api/v1/push"
          headers = {
            "X-Scope-OrgID" = "soev",
          }
        }
      }

      // ======================
      // Export Logs to Loki (multi-tenant)
      // ======================
      otelcol.exporter.loki "default" {
        forward_to = [loki.write.default.receiver]
      }

      loki.write "default" {
        endpoint {
          url = "http://loki.observability.svc:3100/loki/api/v1/push"
          headers = {
            "X-Scope-OrgID" = "soev",
          }
        }
      }

      // ======================
      // Export Traces to Tempo (multi-tenant)
      // ======================
      otelcol.exporter.otlp "tempo" {
        client {
          endpoint = "tempo.observability.svc:4317"
          headers = {
            "X-Scope-OrgID" = "soev",
          }
          tls {
            insecure = true
          }
        }
      }

      // ======================
      // Kubernetes Pod Log Collection
      // ======================
      discovery.kubernetes "pods" {
        role = "pod"
        namespaces {
          names = ["open-webui", "observability"]
        }
      }

      discovery.relabel "pods" {
        targets = discovery.kubernetes.pods.targets
        rule {
          source_labels = ["__meta_kubernetes_namespace"]
          target_label  = "namespace"
        }
        rule {
          source_labels = ["__meta_kubernetes_pod_name"]
          target_label  = "pod"
        }
        rule {
          source_labels = ["__meta_kubernetes_pod_container_name"]
          target_label  = "container"
        }
        // Add tenant label based on namespace
        rule {
          source_labels = ["__meta_kubernetes_namespace"]
          regex         = "open-webui"
          replacement   = "soev"
          target_label  = "tenant"
        }
        rule {
          source_labels = ["__meta_kubernetes_namespace"]
          regex         = "enterprise-(.*)"
          replacement   = "enterprise-$1"
          target_label  = "tenant"
        }
      }

      loki.source.kubernetes "pods" {
        targets    = discovery.relabel.pods.output
        forward_to = [loki.process.add_tenant.receiver]
      }

      // Add tenant header to logs
      loki.process "add_tenant" {
        forward_to = [loki.write.default.receiver]

        stage.tenant {
          source = "tenant"
        }
      }

  # Alloy service configuration
  service:
    type: ClusterIP

  # Resource limits
  resources:
    requests:
      cpu: 100m
      memory: 256Mi
    limits:
      cpu: 500m
      memory: 512Mi

# Enable RBAC for Kubernetes discovery
rbac:
  create: true

# Service account
serviceAccount:
  create: true
```

#### 4. Mimir Configuration (Monolithic Mode, Multi-Tenant)

**File**: `helm/observability/mimir-values.yaml` (new)

```yaml
# Monolithic single-binary mode with multi-tenancy
mimir:
  structuredConfig:
    # Enable multi-tenancy
    multitenancy_enabled: true

    common:
      storage:
        backend: filesystem
        filesystem:
          dir: /data

    blocks_storage:
      backend: filesystem
      filesystem:
        dir: /data/blocks

    ruler_storage:
      backend: filesystem
      filesystem:
        dir: /data/rules

    # Limits per tenant (can be customized per tenant)
    limits:
      max_global_series_per_user: 1000000
      ingestion_rate: 100000
      ingestion_burst_size: 200000

# Single replica for simplicity
replicas: 1

# Persistence
persistence:
  enabled: true
  size: 10Gi

# Resources
resources:
  requests:
    cpu: 100m
    memory: 512Mi
  limits:
    cpu: 1
    memory: 1Gi

# Service
service:
  type: ClusterIP
  port: 9009
```

#### 5. Loki Configuration (Monolithic, Multi-Tenant)

**File**: `helm/observability/loki-values.yaml` (new)

```yaml
# Monolithic deployment mode with multi-tenancy
deploymentMode: SingleBinary

loki:
  # Enable multi-tenancy (requires X-Scope-OrgID header)
  auth_enabled: true

  commonConfig:
    replication_factor: 1

  storage:
    type: filesystem

  schemaConfig:
    configs:
      - from: "2024-01-01"
        store: tsdb
        object_store: filesystem
        schema: v13
        index:
          prefix: index_
          period: 24h

  # Per-tenant limits
  limits_config:
    retention_period: 744h  # 31 days
    max_query_series: 500
    max_entries_limit_per_query: 5000

# Single replica
singleBinary:
  replicas: 1
  persistence:
    enabled: true
    size: 10Gi

# Disable components not needed in monolithic mode
read:
  replicas: 0
write:
  replicas: 0
backend:
  replicas: 0

# Gateway disabled (direct access)
gateway:
  enabled: false

# Resources
resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    cpu: 500m
    memory: 512Mi
```

#### 6. Tempo Configuration (Monolithic, Multi-Tenant)

**File**: `helm/observability/tempo-values.yaml` (new)

```yaml
# Monolithic mode with multi-tenancy
tempo:
  # Enable multi-tenancy
  multitenancyEnabled: true

  storage:
    trace:
      backend: local
      local:
        path: /var/tempo/traces
      wal:
        path: /var/tempo/wal

  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: "0.0.0.0:4317"
        http:
          endpoint: "0.0.0.0:4318"

# Persistence
persistence:
  enabled: true
  size: 10Gi

# Resources
resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    cpu: 500m
    memory: 512Mi

# Service
service:
  type: ClusterIP
```

#### 7. Grafana Configuration (Admin Instance)

**File**: `helm/observability/grafana-values.yaml` (new)

```yaml
# Admin Grafana instance - can see ALL tenants
# For per-tenant Grafana, deploy separate instances with tenant-specific headers

# Admin credentials (store in GCP Secret Manager for production)
adminUser: admin
adminPassword: admin  # CHANGE THIS - or use ESO

# Persistence
persistence:
  enabled: true
  size: 5Gi

# Data sources with multi-tenant support
# Admin Grafana uses httpHeaderName1 to query specific tenants
datasources:
  datasources.yaml:
    apiVersion: 1
    datasources:
      # Mimir - Metrics (default tenant: soev)
      - name: Mimir
        type: prometheus
        url: http://mimir.observability.svc:9009/prometheus
        access: proxy
        isDefault: true
        jsonData:
          httpMethod: POST
          httpHeaderName1: "X-Scope-OrgID"
        secureJsonData:
          httpHeaderValue1: "soev"

      # Mimir - All Tenants (for admin view)
      - name: Mimir (All Tenants)
        type: prometheus
        url: http://mimir.observability.svc:9009/prometheus
        access: proxy
        jsonData:
          httpMethod: POST
          httpHeaderName1: "X-Scope-OrgID"
        secureJsonData:
          httpHeaderValue1: "soev|enterprise-.*"

      # Loki - Logs (default tenant: soev)
      - name: Loki
        type: loki
        url: http://loki.observability.svc:3100
        access: proxy
        uid: loki
        jsonData:
          httpHeaderName1: "X-Scope-OrgID"
          derivedFields:
            - datasourceUid: tempo
              matcherRegex: '"trace_id":"([a-f0-9]+)"'
              name: TraceID
              url: '$${__value.raw}'
        secureJsonData:
          httpHeaderValue1: "soev"

      # Tempo - Traces (default tenant: soev)
      - name: Tempo
        type: tempo
        url: http://tempo.observability.svc:3200
        access: proxy
        uid: tempo
        jsonData:
          httpHeaderName1: "X-Scope-OrgID"
          tracesToLogsV2:
            datasourceUid: loki
            filterByTraceID: true
          serviceMap:
            datasourceUid: mimir
        secureJsonData:
          httpHeaderValue1: "soev"

# Resources
resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    cpu: 500m
    memory: 512Mi

# Ingress - Admin Grafana at grafana.soev.ai
ingress:
  enabled: true
  ingressClassName: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
  hosts:
    - grafana.soev.ai
  tls:
    - secretName: grafana-tls
      hosts:
        - grafana.soev.ai
```

#### 8. Deploy Observability Stack

**Commands**:
```bash
# Add Grafana Helm repository
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

# Deploy Mimir
helm install mimir grafana/mimir-distributed \
  -n observability \
  -f helm/observability/mimir-values.yaml \
  --wait

# Deploy Loki
helm install loki grafana/loki \
  -n observability \
  -f helm/observability/loki-values.yaml \
  --wait

# Deploy Tempo
helm install tempo grafana/tempo \
  -n observability \
  -f helm/observability/tempo-values.yaml \
  --wait

# Deploy Alloy
helm install alloy grafana/alloy \
  -n observability \
  -f helm/observability/alloy-values.yaml \
  --wait

# Deploy Grafana
helm install grafana grafana/grafana \
  -n observability \
  -f helm/observability/grafana-values.yaml \
  --wait
```

### Success Criteria:

#### Automated Verification:
- [ ] All pods running: `kubectl get pods -n observability`
- [ ] Mimir healthy: `kubectl exec -n observability deploy/mimir -- wget -qO- http://localhost:9009/ready`
- [ ] Loki healthy: `kubectl exec -n observability deploy/loki -- wget -qO- http://localhost:3100/ready`
- [ ] Tempo healthy: `kubectl exec -n observability deploy/tempo -- wget -qO- http://localhost:3200/ready`
- [ ] Alloy running: `kubectl logs -n observability deploy/alloy --tail=50`

#### Manual Verification:
- [ ] Port-forward Grafana: `kubectl port-forward -n observability svc/grafana 3000:80`
- [ ] Login to Grafana and verify data sources are connected
- [ ] Check Explore view for each data source

**Implementation Note**: Pause here for confirmation before proceeding to Phase 4.

---

## Phase 4: Helm Chart OTEL Integration

### Overview
Update the open-webui Helm chart to expose OTEL environment variables and configure telemetry by default.

### Changes Required:

#### 1. Add OTEL Values Configuration

**File**: `helm/open-webui-stack/values.yaml`
**Changes**: Add telemetry section under `openWebui.config` (after line 244)

```yaml
  # OpenTelemetry Configuration
  telemetry:
    otel:
      enabled: false
      traces: true
      metrics: true
      logs: true
      endpoint: "http://alloy.observability.svc:4317"
      serviceName: "open-webui"
      insecure: true
      sampler: "parentbased_always_on"
      protocol: "grpc"  # grpc or http
      resourceAttributes: ""  # key1=val1,key2=val2
      # Multi-tenant configuration
      tenantId: "soev"  # X-Scope-OrgID for observability backends
```

#### 2. Update ConfigMap Template

**File**: `helm/open-webui-stack/templates/open-webui/configmap.yaml`
**Changes**: Add OTEL environment variables section (after line 176)

```yaml
{{- if .Values.openWebui.config.telemetry.otel.enabled }}
# OpenTelemetry Configuration
ENABLE_OTEL: "true"
ENABLE_OTEL_TRACES: {{ .Values.openWebui.config.telemetry.otel.traces | quote }}
ENABLE_OTEL_METRICS: {{ .Values.openWebui.config.telemetry.otel.metrics | quote }}
ENABLE_OTEL_LOGS: {{ .Values.openWebui.config.telemetry.otel.logs | quote }}
OTEL_EXPORTER_OTLP_ENDPOINT: {{ .Values.openWebui.config.telemetry.otel.endpoint | quote }}
OTEL_SERVICE_NAME: {{ .Values.openWebui.config.telemetry.otel.serviceName | quote }}
OTEL_EXPORTER_OTLP_INSECURE: {{ .Values.openWebui.config.telemetry.otel.insecure | quote }}
OTEL_TRACES_SAMPLER: {{ .Values.openWebui.config.telemetry.otel.sampler | quote }}
OTEL_OTLP_SPAN_EXPORTER: {{ .Values.openWebui.config.telemetry.otel.protocol | quote }}
{{- if .Values.openWebui.config.telemetry.otel.resourceAttributes }}
OTEL_RESOURCE_ATTRIBUTES: {{ .Values.openWebui.config.telemetry.otel.resourceAttributes | quote }}
{{- end }}
{{- end }}
```

#### 3. Update Production Values

**File**: `helm/open-webui-stack/values-prod.yaml`
**Changes**: Enable OTEL in production

```yaml
openWebui:
  config:
    telemetry:
      otel:
        enabled: true
        traces: true
        metrics: true
        logs: true
        endpoint: "http://alloy.observability.svc:4317"
        serviceName: "open-webui-prod"
        insecure: true
        resourceAttributes: "deployment.environment=production,k8s.cluster.name=demo-cluster"
        tenantId: "soev"  # For enterprise deployments, use "enterprise-{customer}"
```

#### 4. Update Chart Version

**File**: `helm/open-webui-stack/Chart.yaml`
**Changes**: Bump version

```yaml
version: 0.2.0
```

### Success Criteria:

#### Automated Verification:
- [ ] Helm template renders correctly: `helm template helm/open-webui-stack -f helm/open-webui-stack/values-prod.yaml | grep -A15 "ENABLE_OTEL"`
- [ ] Helm lint passes: `helm lint helm/open-webui-stack`
- [ ] Deploy succeeds: `helm upgrade --install open-webui helm/open-webui-stack -n open-webui -f helm/open-webui-stack/values-prod.yaml`

#### Manual Verification:
- [ ] Open WebUI logs show OTEL initialization: `kubectl logs -n open-webui deployment/open-webui | grep -i telemetry`
- [ ] Traces appear in Grafana Tempo after using the application
- [ ] Metrics appear in Grafana Mimir under `open-webui` namespace
- [ ] Logs appear in Grafana Loki with `service_name="open-webui-prod"` label

**Implementation Note**: Pause here for confirmation before proceeding to Phase 5.

---

## Phase 5: FluxCD GitOps Bootstrap

### Overview
Bootstrap FluxCD to enable GitOps-driven deployments from the repository. Pushes to `main` branch automatically trigger deployments.

### Changes Required:

#### 1. Create FluxCD Directory Structure

**Commands**:
```bash
mkdir -p helm/flux/clusters/production/{flux-system,infrastructure,apps}
mkdir -p helm/flux/base/{open-webui,observability}
```

#### 2. Create FluxCD Documentation

**File**: `helm/flux/README.md` (new)

```markdown
# FluxCD GitOps Configuration

This directory contains FluxCD manifests for GitOps-driven deployments.

## Directory Structure

```
flux/
├── README.md                          # This file
├── clusters/
│   └── production/                    # Production cluster configuration
│       ├── flux-system/               # FluxCD bootstrap (auto-generated)
│       ├── infrastructure/            # Infrastructure dependencies
│       │   └── kustomization.yaml     # ESO, observability prereqs
│       └── apps/                      # Application deployments
│           └── kustomization.yaml     # Open WebUI HelmRelease
└── base/
    └── open-webui/                    # Base Kustomization for Open WebUI
        ├── source.yaml                # GitRepository source
        ├── helmrelease.yaml           # HelmRelease definition
        ├── kustomization.yaml         # Kustomize config
        └── image-automation.yaml      # Image update automation
```

## How It Works

1. **Source Controller** watches the Git repository for changes
2. **Kustomize Controller** applies Kustomizations from `clusters/production/`
3. **Helm Controller** reconciles HelmReleases
4. **Image Automation** (optional) updates image tags automatically

## Deployment Flow

```
Push to main → Source Controller detects → Kustomize reconciles → Helm deploys
```

## Common Commands

```bash
# Check Flux status
flux check

# View all sources
flux get sources git

# View HelmReleases
flux get helmreleases -A

# Force reconciliation
flux reconcile source git flux-system
flux reconcile kustomization apps

# Suspend/Resume
flux suspend kustomization apps
flux resume kustomization apps

# View events
flux events --watch
```

## Adding New Deployments

1. Create base Kustomization in `base/{app-name}/`
2. Reference it from `clusters/production/apps/kustomization.yaml`
3. Push to main branch
4. FluxCD will automatically deploy

## Troubleshooting

```bash
# Check controller logs
kubectl logs -n flux-system deploy/source-controller
kubectl logs -n flux-system deploy/kustomize-controller
kubectl logs -n flux-system deploy/helm-controller

# Describe failing resources
kubectl describe helmrelease -n open-webui open-webui
kubectl describe kustomization -n flux-system apps
```

## Image Automation

To enable automatic image updates:

1. Image Reflector Controller scans container registries
2. Image Automation Controller updates HelmRelease values
3. Changes are committed back to Git
4. Regular FluxCD reconciliation deploys the update

Marker format in HelmRelease:
```yaml
image:
  tag: "0.6.11"  # {"$imagepolicy": "flux-system:open-webui:tag"}
```
```

#### 3. GitRepository Source

**File**: `helm/flux/base/open-webui/source.yaml` (new)

```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  name: open-webui
  namespace: flux-system
spec:
  interval: 1m
  url: https://github.com/Gradient-DS/open-webui
  ref:
    branch: main
  secretRef:
    name: github-credentials
```

#### 4. HelmRelease for Open WebUI

**File**: `helm/flux/base/open-webui/helmrelease.yaml` (new)

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: open-webui
  namespace: open-webui
spec:
  interval: 30m
  chart:
    spec:
      chart: ./helm/open-webui-stack
      sourceRef:
        kind: GitRepository
        name: open-webui
        namespace: flux-system
      interval: 1m

  install:
    createNamespace: true
    remediation:
      retries: 3

  upgrade:
    cleanupOnFail: true
    remediation:
      retries: 3

  valuesFrom:
    - kind: ConfigMap
      name: open-webui-values
      valuesKey: values.yaml
      optional: true

  values:
    # Base production values
    global:
      storageClass: "standard-rwo"
      imagePullSecrets:
        - ghcr-secret

    openWebui:
      config:
        adminEmail: "lex@gradient-ds.com"
        adminName: "Lex"
        corsAllowOrigin: "https://voorbeeld.soev.ai"
        telemetry:
          otel:
            enabled: true
            endpoint: "http://alloy.observability.svc:4317"
            serviceName: "open-webui-prod"
            resourceAttributes: "deployment.environment=production"

    externalSecrets:
      enabled: true
      gcpProject: "soev-ai-001"

    ingress:
      enabled: true
      className: nginx
      annotations:
        cert-manager.io/cluster-issuer: letsencrypt-prod
        nginx.ingress.kubernetes.io/proxy-body-size: "100m"
        nginx.ingress.kubernetes.io/proxy-read-timeout: "600"
        nginx.ingress.kubernetes.io/proxy-send-timeout: "600"
      hosts:
        - host: voorbeeld.soev.ai
          paths:
            - path: /
              pathType: Prefix
      tls:
        - secretName: open-webui-tls
          hosts:
            - voorbeeld.soev.ai
```

#### 5. Kustomization for Open WebUI

**File**: `helm/flux/base/open-webui/kustomization.yaml` (new)

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - source.yaml
  - helmrelease.yaml
```

#### 6. Production Cluster Apps Kustomization

**File**: `helm/flux/clusters/production/apps/kustomization.yaml` (new)

```yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: apps
  namespace: flux-system
spec:
  interval: 10m
  sourceRef:
    kind: GitRepository
    name: flux-system
  path: ./helm/flux/base/open-webui
  prune: true
  wait: true
  dependsOn:
    - name: infrastructure
```

#### 7. Infrastructure Kustomization (for observability)

**File**: `helm/flux/clusters/production/infrastructure/kustomization.yaml` (new)

```yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: infrastructure
  namespace: flux-system
spec:
  interval: 10m
  sourceRef:
    kind: GitRepository
    name: flux-system
  path: ./helm/flux/infrastructure
  prune: true
  wait: true
```

#### 8. Bootstrap FluxCD

**Commands**:
```bash
# Install Flux CLI
brew install fluxcd/tap/flux

# Verify cluster compatibility
flux check --pre

# Bootstrap Flux with GitHub
# This will:
# 1. Create flux-system namespace
# 2. Install Flux controllers
# 3. Create GitRepository and Kustomization for the repo
# 4. Commit flux-system manifests to the repo
flux bootstrap github \
  --owner=Gradient-DS \
  --repository=open-webui \
  --branch=main \
  --path=helm/flux/clusters/production \
  --personal

# Create GitHub credentials secret (for private repo access)
flux create secret git github-credentials \
  --url=https://github.com/Gradient-DS/open-webui \
  --username=git \
  --password=$GITHUB_TOKEN
```

#### 9. Image Automation (Auto-deploy on push to main)

**File**: `helm/flux/base/open-webui/image-automation.yaml` (new)

This enables automatic deployment when new images are pushed to GHCR.

```yaml
---
apiVersion: image.toolkit.fluxcd.io/v1beta2
kind: ImageRepository
metadata:
  name: open-webui
  namespace: flux-system
spec:
  image: ghcr.io/gradient-ds/open-webui
  interval: 1m
  secretRef:
    name: ghcr-credentials
---
apiVersion: image.toolkit.fluxcd.io/v1beta2
kind: ImagePolicy
metadata:
  name: open-webui
  namespace: flux-system
spec:
  imageRepositoryRef:
    name: open-webui
  policy:
    semver:
      range: ">=0.0.1"
---
apiVersion: image.toolkit.fluxcd.io/v1beta1
kind: ImageUpdateAutomation
metadata:
  name: open-webui
  namespace: flux-system
spec:
  interval: 30m
  sourceRef:
    kind: GitRepository
    name: open-webui
  git:
    checkout:
      ref:
        branch: main
    commit:
      author:
        email: fluxcdbot@gradient-ds.com
        name: FluxCD Bot
      messageTemplate: 'chore: update open-webui image to {{.NewTag}}'
    push:
      branch: main
  update:
    path: ./helm/flux/base/open-webui
    strategy: Setters
```

### Success Criteria:

#### Automated Verification:
- [ ] Flux components installed: `flux check`
- [ ] Git source synced: `flux get sources git`
- [ ] HelmRelease reconciled: `flux get helmreleases -n open-webui`
- [ ] Kustomizations healthy: `flux get kustomizations`
- [ ] Events stream: `flux events --watch`

#### Manual Verification:
- [ ] Push a change to `values-prod.yaml` and verify automatic deployment
- [ ] Check Flux dashboard (if using Weave GitOps UI)
- [ ] Verify deployment rollout after image push

**Implementation Note**: After Phase 5, the GitOps pipeline is complete. Future deployments will be automatic on Git push.

---

## Testing Strategy

### Unit Tests
- Helm template rendering: `helm template` with various value combinations
- ConfigMap validation: Ensure all OTEL variables render correctly
- Secret template conditions: Test `externalSecrets.enabled` toggle

### Integration Tests
- Deploy to staging cluster with all components
- Generate traces via API calls and verify in Grafana
- Verify secret rotation (change in GCP Secret Manager → sync to K8s)
- Test FluxCD reconciliation on Git push

### Manual Testing Steps
1. Port-forward Grafana and explore all three data sources
2. Make API requests to Open WebUI and trace the request flow
3. Verify log correlation with trace IDs
4. Test secret rotation by updating GCP Secret Manager
5. Push a commit and verify FluxCD deploys the change

---

## Migration Notes

### From Current State
1. **Secrets**: After enabling ESO, secrets will be managed externally. Remove `values-secrets.yaml` from any deployment pipelines.
2. **Deployment**: After FluxCD bootstrap, stop using `helm upgrade` directly. All changes should go through Git.
3. **Monitoring**: Existing logs in stdout will continue to work. OTEL adds structured telemetry on top.

### Rollback Plan
- **ESO**: Set `externalSecrets.enabled: false` to revert to inline secrets
- **OTEL**: Set `telemetry.otel.enabled: false` to disable telemetry
- **FluxCD**: Suspend reconciliation with `flux suspend kustomization apps`

---

## Performance Considerations

- **Alloy**: Batches telemetry data before forwarding, reducing network overhead
- **Mimir/Loki/Tempo**: Monolithic mode is suitable for moderate workloads. Consider distributed mode for high volume.
- **ESO Refresh**: 1h refresh interval balances freshness vs GCP API quota
- **FluxCD Interval**: 30m reconciliation interval prevents excessive Git polling

---

## Documentation Summary

The following documentation files will be created as part of this plan:

| File | Purpose |
|------|---------|
| `helm/observability/README.md` | Observability stack overview, deployment commands, multi-tenancy guide |
| `helm/flux/README.md` | FluxCD GitOps overview, common commands, troubleshooting |

Each README includes:
- Architecture overview
- Deployment instructions
- Common commands
- Troubleshooting steps
- How to extend (add tenants, new deployments)

---

## Quick Reference: Adding Enterprise Tenant

When adding a new enterprise customer deployment:

1. **Create namespace**: `kubectl create namespace enterprise-{customer}`
2. **Deploy Open WebUI** with tenant-specific values:
   ```yaml
   openWebui:
     config:
       telemetry:
         otel:
           tenantId: "enterprise-{customer}"
           serviceName: "open-webui-{customer}"
   ```
3. **No changes needed** to Mimir/Loki/Tempo (multi-tenancy is automatic)
4. **Optionally** deploy customer-specific Grafana with tenant header preset

---

## References

- Research document: `thoughts/shared/research/2026-01-14-observability-secrets-gitops-integration.md`
- Current secrets template: `helm/open-webui-stack/templates/secrets.yaml`
- OTEL environment variables: `backend/open_webui/env.py:808-874`
- Production values: `helm/open-webui-stack/values-prod.yaml`
- Docker OTEL example: `docker-compose.otel.yaml`

### External Documentation
- [External Secrets Operator + GCP](https://external-secrets.io/latest/provider/google-secrets-manager/)
- [Grafana Mimir Multi-Tenancy](https://grafana.com/docs/mimir/latest/configure/configure-tenants/)
- [Grafana Loki Multi-Tenancy](https://grafana.com/docs/loki/latest/operations/multi-tenancy/)
- [FluxCD Documentation](https://fluxcd.io/docs/)
