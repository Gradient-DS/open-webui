---
date: 2026-01-14T09:49:58Z
researcher: Claude
git_commit: 9c2fe51ef57f2ae02744a806f8248533c42c9f6c
branch: main
repository: open-webui
topic: "Integrating Haven+ observability stack (Mimir, Loki, Tempo), Vault, and FluxCD"
tags: [research, observability, mimir, loki, tempo, vault, fluxcd, gitops, kubernetes]
status: complete
last_updated: 2026-01-14
last_updated_by: Claude
---

# Research: Integrating Haven+ Observability Stack, Vault, and FluxCD

**Date**: 2026-01-14T09:49:58Z
**Researcher**: Claude
**Git Commit**: 9c2fe51ef57f2ae02744a806f8248533c42c9f6c
**Branch**: main
**Repository**: open-webui

## Research Question

How can we integrate the following components into the open-webui stack?
- **Metrics**: Mimir + Grafana (Prometheus-compatible, scalable)
- **Logging**: Loki + Alloy (logs stay searchable, Dutch storage possible)
- **Tracing**: Tempo (OpenTelemetry-native)
- **Secrets**: HashiCorp Vault
- **GitOps**: FluxCD

## Summary

The open-webui codebase already has **comprehensive OpenTelemetry instrumentation** that exports traces, metrics, and logs via OTLP. This makes it straightforward to integrate with the Haven+ stack (Mimir, Loki, Tempo) since all three accept OTLP ingestion. The main work involves:

1. **Observability**: Configure OTEL Collector (Alloy) as the ingestion gateway to route telemetry to Mimir/Loki/Tempo
2. **Secrets**: Add External Secrets Operator with Vault backend to replace inline Kubernetes secrets
3. **GitOps**: Add FluxCD manifests to enable automated deployments from Git

## Current State Analysis

### Observability (Already Implemented)

| Signal | Current Support | Export Method |
|--------|----------------|---------------|
| **Metrics** | Full OTEL metrics | OTLP gRPC/HTTP to collector |
| **Logs** | Loguru + OTEL export | OTLP gRPC/HTTP + stdout |
| **Traces** | Auto-instrumented | OTLP gRPC/HTTP to collector |

**Key files:**
- `backend/open_webui/utils/telemetry/setup.py:28-58` - Main setup
- `backend/open_webui/utils/telemetry/metrics.py:114-203` - Custom metrics
- `backend/open_webui/utils/telemetry/instrumentors.py:165-201` - Auto-instrumentation
- `backend/open_webui/env.py:808-874` - Environment variables

**Environment variables already supported:**
```bash
ENABLE_OTEL=true
ENABLE_OTEL_TRACES=true
ENABLE_OTEL_METRICS=true
ENABLE_OTEL_LOGS=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4317
OTEL_SERVICE_NAME=open-webui
```

### Secrets Management (Current)

- Kubernetes Secrets via Helm (`helm/open-webui-stack/templates/secrets.yaml`)
- Auto-generates secrets if not provided (using `randAlphaNum`)
- No Vault or External Secrets Operator integration

### GitOps (Current)

- Manual `helm upgrade` deployments
- GitHub Actions for image builds only
- No ArgoCD or FluxCD

---

## Integration Plan

### 1. Observability: Mimir + Loki + Tempo + Alloy

#### Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Kubernetes Cluster                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐        │
│  │  Open WebUI  │     │   Gateway    │     │   Weaviate   │        │
│  │  (OTEL SDK)  │     │  (OTEL SDK)  │     │              │        │
│  └──────┬───────┘     └──────┬───────┘     └──────────────┘        │
│         │                    │                                      │
│         │ OTLP               │ OTLP                                │
│         ▼                    ▼                                      │
│  ┌─────────────────────────────────────┐                           │
│  │           Grafana Alloy             │  ← Node logs via          │
│  │  (OTEL Collector + log collection)  │    Kubernetes API         │
│  └───────────────┬─────────────────────┘                           │
│                  │                                                  │
│    ┌─────────────┼─────────────┬──────────────┐                    │
│    │ metrics     │ logs        │ traces       │                    │
│    ▼             ▼             ▼              │                    │
│  ┌──────┐    ┌──────┐     ┌──────┐           │                    │
│  │Mimir │    │ Loki │     │Tempo │           │                    │
│  └──────┘    └──────┘     └──────┘           │                    │
│       │          │            │               │                    │
│       └──────────┴────────────┘               │                    │
│                  │                            │                    │
│                  ▼                            │                    │
│           ┌───────────┐                       │                    │
│           │  Grafana  │◄──────────────────────┘                    │
│           └───────────┘                                            │
└─────────────────────────────────────────────────────────────────────┘
```

#### Option A: Deploy via Helm Charts (Recommended for Simplicity)

Add to `helm/open-webui-stack/Chart.yaml` as dependencies:

```yaml
dependencies:
  - name: grafana
    version: "8.x.x"
    repository: "https://grafana.github.io/helm-charts"
    condition: grafana.enabled
  - name: mimir-distributed
    version: "5.x.x"
    repository: "https://grafana.github.io/helm-charts"
    condition: mimir.enabled
  - name: loki
    version: "6.x.x"
    repository: "https://grafana.github.io/helm-charts"
    condition: loki.enabled
  - name: tempo
    version: "1.x.x"
    repository: "https://grafana.github.io/helm-charts"
    condition: tempo.enabled
  - name: alloy
    version: "0.x.x"
    repository: "https://grafana.github.io/helm-charts"
    condition: alloy.enabled
```

#### Option B: Separate Infrastructure Helm Release (Recommended for Production)

Keep observability stack in a separate Helm release for independent lifecycle management:

```bash
# Create separate namespace
kubectl create namespace observability

# Install the Grafana stack
helm repo add grafana https://grafana.github.io/helm-charts

# Deploy components
helm install mimir grafana/mimir-distributed -n observability -f mimir-values.yaml
helm install loki grafana/loki -n observability -f loki-values.yaml
helm install tempo grafana/tempo -n observability -f tempo-values.yaml
helm install alloy grafana/alloy -n observability -f alloy-values.yaml
helm install grafana grafana/grafana -n observability -f grafana-values.yaml
```

#### Alloy Configuration for OTLP Ingestion

Create `helm/observability/alloy-values.yaml`:

```yaml
alloy:
  configMap:
    content: |
      // Receive OTLP telemetry
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

      // Batch processor for efficiency
      otelcol.processor.batch "default" {
        output {
          metrics = [otelcol.exporter.prometheus.mimir.input]
          logs    = [otelcol.exporter.loki.default.input]
          traces  = [otelcol.exporter.otlp.tempo.input]
        }
      }

      // Export metrics to Mimir
      otelcol.exporter.prometheus "mimir" {
        forward_to = [prometheus.remote_write.mimir.receiver]
      }

      prometheus.remote_write "mimir" {
        endpoint {
          url = "http://mimir-nginx.observability.svc:8080/api/v1/push"
        }
      }

      // Export logs to Loki
      otelcol.exporter.loki "default" {
        forward_to = [loki.write.default.receiver]
      }

      loki.write "default" {
        endpoint {
          url = "http://loki-gateway.observability.svc:80/loki/api/v1/push"
        }
      }

      // Export traces to Tempo
      otelcol.exporter.otlp "tempo" {
        client {
          endpoint = "http://tempo.observability.svc:4317"
          tls {
            insecure = true
          }
        }
      }

      // Collect Kubernetes pod logs
      discovery.kubernetes "pods" {
        role = "pod"
      }

      loki.source.kubernetes "pods" {
        targets    = discovery.kubernetes.pods.targets
        forward_to = [loki.write.default.receiver]
      }
```

#### Update Open WebUI Helm ConfigMap

Add to `helm/open-webui-stack/templates/open-webui/configmap.yaml`:

```yaml
# OpenTelemetry Configuration
ENABLE_OTEL: "{{ .Values.openWebui.telemetry.enabled }}"
ENABLE_OTEL_TRACES: "{{ .Values.openWebui.telemetry.traces }}"
ENABLE_OTEL_METRICS: "{{ .Values.openWebui.telemetry.metrics }}"
ENABLE_OTEL_LOGS: "{{ .Values.openWebui.telemetry.logs }}"
OTEL_EXPORTER_OTLP_ENDPOINT: "{{ .Values.openWebui.telemetry.endpoint }}"
OTEL_SERVICE_NAME: "{{ .Values.openWebui.telemetry.serviceName }}"
OTEL_EXPORTER_OTLP_INSECURE: "{{ .Values.openWebui.telemetry.insecure }}"
```

Add to `helm/open-webui-stack/values.yaml`:

```yaml
openWebui:
  telemetry:
    enabled: true
    traces: true
    metrics: true
    logs: true
    endpoint: "http://alloy.observability.svc:4317"
    serviceName: "open-webui"
    insecure: true
```

#### Dutch Storage for Loki

Configure Loki with Dutch S3-compatible storage (e.g., Scaleway, OVH, or Azure Blob with Netherlands region):

```yaml
# loki-values.yaml
loki:
  storage:
    type: s3
    s3:
      endpoint: s3.nl-ams.scw.cloud  # Scaleway Amsterdam
      region: nl-ams
      bucketnames: loki-logs-nl
      access_key_id: ${S3_ACCESS_KEY}
      secret_access_key: ${S3_SECRET_KEY}

  # Or Azure Blob Storage (Netherlands West)
  # storage:
  #   type: azure
  #   azure:
  #     accountName: ${AZURE_STORAGE_ACCOUNT}
  #     accountKey: ${AZURE_STORAGE_KEY}
  #     containerName: loki-logs
```

---

### 2. Secrets Management: HashiCorp Vault + External Secrets Operator

#### Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     Kubernetes Cluster                            │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│   ┌─────────────────┐         ┌──────────────────────────────┐   │
│   │ ExternalSecret  │────────▶│  External Secrets Operator   │   │
│   │   (CR yaml)     │         │                              │   │
│   └─────────────────┘         └──────────────┬───────────────┘   │
│                                              │                    │
│                                              │ SecretStore        │
│                                              │ connection         │
│                                              ▼                    │
│   ┌─────────────────┐         ┌──────────────────────────────┐   │
│   │ Kubernetes      │◀────────│     HashiCorp Vault          │   │
│   │ Secret (synced) │  create │  (external or in-cluster)    │   │
│   └────────┬────────┘         └──────────────────────────────┘   │
│            │                                                      │
│            │ secretKeyRef                                         │
│            ▼                                                      │
│   ┌─────────────────┐                                            │
│   │   Open WebUI    │                                            │
│   │   Deployment    │                                            │
│   └─────────────────┘                                            │
└──────────────────────────────────────────────────────────────────┘
```

#### Step 1: Install External Secrets Operator

```bash
helm repo add external-secrets https://charts.external-secrets.io
helm install external-secrets external-secrets/external-secrets \
  -n external-secrets --create-namespace
```

#### Step 2: Configure Vault SecretStore

Create `helm/open-webui-stack/templates/vault-secretstore.yaml`:

```yaml
{{- if .Values.vault.enabled }}
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: vault-backend
  namespace: {{ .Release.Namespace }}
spec:
  provider:
    vault:
      server: {{ .Values.vault.server | quote }}
      path: {{ .Values.vault.path | quote }}
      version: "v2"
      auth:
        kubernetes:
          mountPath: "kubernetes"
          role: {{ .Values.vault.role | quote }}
          serviceAccountRef:
            name: {{ include "open-webui-stack.serviceAccountName" . }}
{{- end }}
```

#### Step 3: Create ExternalSecret Resources

Replace `helm/open-webui-stack/templates/secrets.yaml` with `helm/open-webui-stack/templates/external-secrets.yaml`:

```yaml
{{- if .Values.vault.enabled }}
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: {{ include "open-webui-stack.fullname" . }}-secrets
  labels:
    {{- include "open-webui-stack.labels" . | nindent 4 }}
spec:
  refreshInterval: "1h"
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: {{ include "open-webui-stack.fullname" . }}-secrets
    creationPolicy: Owner
  data:
    - secretKey: webui-secret-key
      remoteRef:
        key: open-webui/secrets
        property: webui-secret-key
    - secretKey: admin-password
      remoteRef:
        key: open-webui/secrets
        property: admin-password
    - secretKey: openai-api-key
      remoteRef:
        key: open-webui/secrets
        property: openai-api-key
    - secretKey: rag-openai-api-key
      remoteRef:
        key: open-webui/secrets
        property: rag-openai-api-key
    - secretKey: postgres-password
      remoteRef:
        key: open-webui/secrets
        property: postgres-password
    - secretKey: searxng-secret-key
      remoteRef:
        key: open-webui/secrets
        property: searxng-secret-key
{{- else }}
# Fallback to inline secrets when Vault is not enabled
{{- include "open-webui-stack.inline-secrets" . }}
{{- end }}
```

#### Step 4: Add Vault Values

Add to `helm/open-webui-stack/values.yaml`:

```yaml
vault:
  enabled: false
  server: "https://vault.example.com"
  path: "secret"
  role: "open-webui"
```

#### Step 5: Populate Vault Secrets

```bash
# Enable KV secrets engine
vault secrets enable -path=secret kv-v2

# Write secrets
vault kv put secret/open-webui/secrets \
  webui-secret-key="$(openssl rand -hex 32)" \
  admin-password="secure-password" \
  openai-api-key="sk-xxx" \
  rag-openai-api-key="sk-xxx" \
  postgres-password="$(openssl rand -hex 16)" \
  searxng-secret-key="$(openssl rand -hex 16)"

# Create Kubernetes auth policy
vault policy write open-webui - <<EOF
path "secret/data/open-webui/*" {
  capabilities = ["read"]
}
EOF

# Enable Kubernetes auth
vault auth enable kubernetes
vault write auth/kubernetes/config \
  kubernetes_host="https://kubernetes.default.svc"

vault write auth/kubernetes/role/open-webui \
  bound_service_account_names=open-webui \
  bound_service_account_namespaces=open-webui \
  policies=open-webui \
  ttl=1h
```

---

### 3. GitOps: FluxCD

#### Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        Git Repository                             │
│                    (github.com/Gradient-DS/open-webui)           │
├──────────────────────────────────────────────────────────────────┤
│  helm/                                                            │
│  ├── open-webui-stack/                                           │
│  │   ├── Chart.yaml                                              │
│  │   └── values.yaml                                             │
│  └── flux/                                                       │
│      ├── clusters/                                               │
│      │   └── production/                                         │
│      │       ├── flux-system/        ← Flux bootstrap            │
│      │       ├── infrastructure/     ← Observability, ESO        │
│      │       └── apps/               ← Open WebUI                │
│      └── base/                                                   │
│          └── open-webui/                                         │
│              ├── helmrelease.yaml                                │
│              ├── helmrepository.yaml                             │
│              └── kustomization.yaml                              │
└──────────────────────────────────────────────────────────────────┘
                            │
                            │ GitOps sync
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│                     Kubernetes Cluster                            │
├──────────────────────────────────────────────────────────────────┤
│  flux-system namespace:                                          │
│  ├── source-controller     (watches Git repos)                   │
│  ├── kustomize-controller  (applies Kustomizations)              │
│  ├── helm-controller       (manages HelmReleases)                │
│  └── notification-controller (sends alerts)                      │
│                                                                   │
│  open-webui namespace:                                           │
│  └── HelmRelease → deploys helm/open-webui-stack                 │
└──────────────────────────────────────────────────────────────────┘
```

#### Step 1: Bootstrap FluxCD

```bash
# Install Flux CLI
brew install fluxcd/tap/flux  # or curl -s https://fluxcd.io/install.sh | sudo bash

# Bootstrap Flux on the cluster
flux bootstrap github \
  --owner=Gradient-DS \
  --repository=open-webui \
  --branch=main \
  --path=helm/flux/clusters/production \
  --personal
```

#### Step 2: Create Directory Structure

```bash
mkdir -p helm/flux/clusters/production/{flux-system,infrastructure,apps}
mkdir -p helm/flux/base/open-webui
```

#### Step 3: Define GitRepository Source

Create `helm/flux/base/open-webui/helmrepository.yaml`:

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
    name: github-credentials  # If private repo
```

#### Step 4: Define HelmRelease

Create `helm/flux/base/open-webui/helmrelease.yaml`:

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

  values:
    # Base values inline or via valuesFrom
    openWebui:
      replicaCount: 2
      telemetry:
        enabled: true
        endpoint: "http://alloy.observability.svc:4317"

    vault:
      enabled: true
      server: "https://vault.soev.ai"
      role: "open-webui"

    ingress:
      enabled: true
      className: nginx
      hosts:
        - host: voorbeeld.soev.ai
          paths:
            - path: /
              pathType: Prefix
      tls:
        - secretName: voorbeeld-soev-ai-tls
          hosts:
            - voorbeeld.soev.ai

  valuesFrom:
    - kind: ConfigMap
      name: open-webui-values
      valuesKey: values.yaml
      optional: true
```

#### Step 5: Create Kustomization for Apps

Create `helm/flux/clusters/production/apps/kustomization.yaml`:

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

#### Step 6: Image Automation (Optional)

Enable automatic image updates when new images are pushed:

Create `helm/flux/base/open-webui/imagepolicy.yaml`:

```yaml
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
```

Update HelmRelease to use image automation:

```yaml
spec:
  values:
    openWebui:
      image:
        tag: "0.6.11" # {"$imagepolicy": "flux-system:open-webui:tag"}
```

---

## Code References

| Component | File | Line | Description |
|-----------|------|------|-------------|
| OTEL Setup | `backend/open_webui/utils/telemetry/setup.py` | 28-58 | Main telemetry initialization |
| OTEL Env Vars | `backend/open_webui/env.py` | 808-874 | All OTEL configuration options |
| Metrics | `backend/open_webui/utils/telemetry/metrics.py` | 114-203 | Custom application metrics |
| Auto-instrumentation | `backend/open_webui/utils/telemetry/instrumentors.py` | 165-201 | FastAPI, SQLAlchemy, Redis, etc. |
| Log Export | `backend/open_webui/utils/telemetry/logs.py` | 24-53 | OTLP log forwarding |
| Current Secrets | `helm/open-webui-stack/templates/secrets.yaml` | 1-30 | Kubernetes Secrets template |
| Helm Values | `helm/open-webui-stack/values.yaml` | 1-570 | All configurable values |
| Production Values | `helm/open-webui-stack/values-prod.yaml` | 1-61 | GKE deployment overrides |
| Docker OTEL | `docker-compose.otel.yaml` | 1-36 | Dev observability stack |

## Architecture Insights

### Strengths of Current Implementation

1. **OTEL-First**: The codebase uses OpenTelemetry SDK natively, making it vendor-agnostic and compatible with any OTLP-compatible backend
2. **Comprehensive Instrumentation**: Auto-instruments FastAPI, SQLAlchemy, Redis, HTTPX, aiohttp, and standard logging
3. **Custom Metrics**: Already exposes business metrics (user counts, request duration)
4. **Helm-Based**: Clean Helm chart structure that's easy to extend

### Integration Considerations

1. **Alloy vs OTel Collector**: Grafana Alloy is a superset of OTel Collector with native Loki/Mimir/Tempo support - recommended over vanilla OTel Collector
2. **Separate Namespaces**: Keep observability stack in dedicated namespace for independent lifecycle
3. **Storage Locality**: Loki supports Dutch S3-compatible storage (Scaleway Amsterdam, OVH, or Azure Netherlands West)
4. **Vault HA**: For production, deploy Vault in HA mode with Raft storage or use managed Vault (HCP Vault)
5. **FluxCD vs ArgoCD**: FluxCD is lighter-weight and Git-native; ArgoCD offers better UI but more complexity

## Implementation Priority

| Priority | Component | Effort | Impact |
|----------|-----------|--------|--------|
| 1 | Observability (Mimir/Loki/Tempo/Alloy) | Medium | High - immediate visibility |
| 2 | FluxCD GitOps | Low | High - automated deployments |
| 3 | Vault + ESO | Medium | Medium - improved secret hygiene |

## Open Questions

1. **Vault Deployment**: Self-hosted Vault on GKE or use HashiCorp Cloud Platform (HCP) managed Vault?
2. **Multi-Cluster**: Will this stack be deployed to multiple clusters requiring multi-cluster FluxCD setup?
3. **Retention**: What's the desired log/metrics/trace retention period for Dutch storage compliance?
4. **High Availability**: Should Mimir/Loki/Tempo be deployed in distributed mode or monolithic for simplicity?
5. **Alerting**: Should we also configure Grafana Alertmanager for observability alerts?
