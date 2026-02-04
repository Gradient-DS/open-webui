# Migrate GKE Demo Tenant to Previder Implementation Plan

## Overview

Migrate the `demo` Open WebUI tenant from the GKE cluster (`gke-demo`) to the Previder bare-metal cluster (`previder-prod`), including full data migration (PostgreSQL, Weaviate, uploaded files). The domain `demo.soev.ai` stays the same. After verification, the GKE cluster will be decommissioned.

## Current State Analysis

- **GKE demo tenant**: Running in namespace `open-webui` on `gke-demo` cluster
- **Storage**: GCE Persistent Disk (`standard-rwo`)
- **Ingress**: NGINX Ingress Controller with cert-manager
- **Secrets**: External Secrets Operator + GCP Secret Manager (project `soev-ai-001`)
- **Inference**: Default from base chart (HuggingFace router)
- **OTEL**: Partial config (missing endpoint/protocol/sampler)

### Key Discoveries:
- GKE uses namespace `open-webui` (not `open-webui-demo`) -- `tenants/gke-demo/demo/namespace.yaml:4`
- GKE uses `fullnameOverride: open-webui-open-webui-stack` to preserve legacy PVCs -- `tenants/gke-demo/demo/values-patch.yaml:8`
- Previder tenants follow `open-webui-{tenant}` namespace convention and `{tenant}` as fullnameOverride
- Alloy log collection targets are hardcoded -- `observability/base/alloy/values.yaml:97`
- Loki ruler rules are mounted per-tenant via extraVolumes -- `observability/previder-prod/loki-values-patch.yaml:97-134`

## Desired End State

- Demo tenant running on `previder-prod` at `demo.soev.ai` with all user data intact
- Full observability: Alloy log collection, Grafana datasource, Loki alerting rules
- SOPS-encrypted secrets (no more GCP Secret Manager dependency)
- Cilium Gateway routing with valid TLS certificate
- GKE cluster ready for decommissioning

### How to Verify:
1. `https://demo.soev.ai` loads and serves valid TLS
2. All user accounts can log in
3. Chat history is intact
4. Uploaded files / knowledge bases are accessible
5. RAG search returns results (Weaviate data intact)
6. Logs appear in Grafana under "Loki (Demo)" datasource
7. Alerting rules fire correctly (test with `absent_over_time`)

## What We're NOT Doing

- Changing the domain (stays `demo.soev.ai`)
- Changing the LLM inference provider (stays same as GKE)
- Adding Microsoft SSO (GKE demo doesn't use it)
- Setting up FluxCD (not yet in use)
- Migrating observability history (metrics/logs/traces from GKE are ephemeral)

## Implementation Approach

The migration requires a **maintenance window** because the same domain must point to only one cluster at a time. The sequence is:

1. **Prepare**: Create all config files on Previder side (Claude does this)
2. **Deploy empty**: User deploys the tenant on Previder (empty, no data)
3. **Freeze GKE**: Scale down GKE Open WebUI to prevent writes
4. **Export data**: Dump PostgreSQL, backup Weaviate, copy uploaded files from GKE
5. **Import data**: Restore everything to Previder
6. **DNS cutover**: Switch `demo.soev.ai` to Previder gateway IP (`10.10.0.200`)
7. **TLS**: Issue certificate on Previder via HTTP-01
8. **Verify**: Confirm everything works
9. **Observability**: Deploy updated LGTM stack with demo tenant

---

## Phase 1: Create Tenant Config Files

### Overview
Claude creates all the scaffolding files for the demo tenant on previder-prod. No kubectl needed.

### Changes Required:

#### 1. Namespace
**File**: `soev-gitops/tenants/previder-prod/demo/namespace.yaml` (new)

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: open-webui-demo
  labels:
    kubernetes.io/metadata.name: open-webui-demo
    uses-shared-services: "true"
    tenant: demo
    pod-security.kubernetes.io/enforce: baseline
    pod-security.kubernetes.io/audit: restricted
    pod-security.kubernetes.io/warn: restricted
```

#### 2. ResourceQuota
**File**: `soev-gitops/tenants/previder-prod/demo/resourcequota.yaml` (new)

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: tenant-quota
  namespace: open-webui-demo
spec:
  hard:
    requests.cpu: "4"
    requests.memory: 8Gi
    limits.cpu: "8"
    limits.memory: 16Gi
    pods: "20"
    persistentvolumeclaims: "10"
```

#### 3. LimitRange
**File**: `soev-gitops/tenants/previder-prod/demo/limitrange.yaml` (new)

```yaml
apiVersion: v1
kind: LimitRange
metadata:
  name: tenant-limits
  namespace: open-webui-demo
spec:
  limits:
    - type: Container
      default:
        memory: 512Mi
        cpu: 500m
      defaultRequest:
        memory: 256Mi
        cpu: 100m
      max:
        memory: 4Gi
        cpu: 2000m
      min:
        memory: 64Mi
        cpu: 50m
```

#### 4. Kustomization
**File**: `soev-gitops/tenants/previder-prod/demo/kustomization.yaml` (new)

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../base
  - namespace.yaml
```

#### 5. Values Patch
**File**: `soev-gitops/tenants/previder-prod/demo/values-patch.yaml` (new)

Ports the application config from GKE (`tenants/gke-demo/demo/values-patch.yaml`) with Previder platform settings.

```yaml
# demo.soev.ai tenant configuration (previder-prod cluster)
fullnameOverride: demo

tenant:
  name: demo
  domain: demo.soev.ai

sharedServices:
  namespace: shared-services

global:
  storageClass: "longhorn"
  imagePullSecrets:
    - ghcr-secret

openWebui:
  config:
    corsAllowOrigin: "https://demo.soev.ai"

    # Disable public/community sharing (ported from GKE config)
    enableCommunitySharing: "false"
    userPermissionsWorkspaceModelsAllowPublicSharing: "false"
    userPermissionsWorkspaceKnowledgeAllowPublicSharing: "false"
    userPermissionsWorkspacePromptsAllowPublicSharing: "false"
    userPermissionsWorkspaceToolsAllowPublicSharing: "false"
    userPermissionsNotesAllowPublicSharing: "false"

    # Reranker (shared service)
    ragExternalRerankerUrl: "http://gradient-reranker.shared-services.svc:8000/v1/rerank"

    # Telemetry - OpenTelemetry to Alloy collector
    telemetry:
      otel:
        enabled: true
        traces: "true"
        metrics: "true"
        logs: "false"
        endpoint: "http://alloy.observability.svc:4317"
        serviceName: "open-webui-demo"
        insecure: "true"
        sampler: "always_on"
        protocol: "grpc"
        resourceAttributes: "deployment.environment=production,k8s.cluster.name=previder-prod,tenant.name=demo"

# Disable nginx ingress - routing handled by Cilium Gateway API
ingress:
  enabled: false

# Allow traffic from Cilium Gateway L7 proxy (Envoy)
ciliumNetworkPolicy:
  enabled: true

# IMPORTANT: Disable external secrets - we use SOPS
externalSecrets:
  enabled: false
```

#### 6. Secrets Template
**File**: `soev-gitops/tenants/previder-prod/demo/secrets.yaml` (new, user fills in and encrypts)

```yaml
# FILL IN AND ENCRYPT -- DO NOT COMMIT THIS FILE
# After filling in values, run:
#   export SOPS_AGE_KEY_FILE=clusters/previder-prod/age.key
#   sops --encrypt secrets.yaml > secrets.enc.yaml
#   rm secrets.yaml
secrets:
    webuiSecretKey: ""        # MUST match GKE value for existing sessions to work
    postgresPassword: ""      # New password is fine (pg_restore sets up the DB fresh)
    openaiApiKey: ""          # Same API key as GKE
    ragOpenaiApiKey: ""       # Same RAG API key as GKE
```

### Success Criteria:

#### Automated Verification:
- [ ] All 6 files created in `tenants/previder-prod/demo/`
- [ ] `values-patch.yaml` has `storageClass: "longhorn"`, `ingress.enabled: false`, `ciliumNetworkPolicy.enabled: true`, `externalSecrets.enabled: false`
- [ ] Namespace follows `open-webui-demo` convention

#### Manual Verification:
- [ ] Review `values-patch.yaml` -- confirm app-level settings are correct
- [ ] Fill in `secrets.yaml` with actual values from GCP Secret Manager

---

## Phase 2: Create Gateway Infrastructure

### Overview
Add the demo tenant to the Cilium Gateway: HTTPRoute, HTTPS listener, TLS certificate, HTTP redirect entry.

### Changes Required:

#### 1. HTTPRoute
**File**: `soev-gitops/infrastructure/previder-prod/gateway/httproute-demo.yaml` (new)

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: demo-route
  namespace: open-webui-demo
spec:
  parentRefs:
    - name: soev-gateway
      namespace: gateway
  hostnames:
    - demo.soev.ai
  rules:
    - backendRefs:
        - name: demo-open-webui
          port: 8080
```

#### 2. HTTPS Listener
**File**: `soev-gitops/infrastructure/previder-prod/gateway/gateway.yaml` (edit)

Append a new listener after the existing `grafana-https` listener:

```yaml
    - name: demo-https
      hostname: demo.soev.ai
      protocol: HTTPS
      port: 443
      tls:
        mode: Terminate
        certificateRefs:
          - name: demo-tls
      allowedRoutes:
        namespaces:
          from: All
```

#### 3. TLS Certificate
**File**: `soev-gitops/infrastructure/previder-prod/gateway/certificates.yaml` (edit)

Append after the `grafana-tls` certificate:

```yaml
---
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: demo-tls
  namespace: gateway
spec:
  secretName: demo-tls
  issuerRef:
    name: letsencrypt-prod
    kind: ClusterIssuer
  dnsNames:
    - demo.soev.ai
```

#### 4. HTTP Redirect
**File**: `soev-gitops/infrastructure/previder-prod/gateway/http-redirect.yaml` (edit)

Add `demo.soev.ai` to the hostnames list:

```yaml
  hostnames:
    - gradient.soev.ai
    - kwink.soev.ai
    - haute-equipe.soev.ai
    - grafana-previder.soev.ai
    - demo.soev.ai
```

#### 5. Kustomization
**File**: `soev-gitops/infrastructure/previder-prod/gateway/kustomization.yaml` (edit)

Add the HTTPRoute resource:

```yaml
resources:
  - namespace.yaml
  - clusterissuer.yaml
  - gateway.yaml
  - certificates.yaml
  - httproute-gradient.yaml
  - httproute-kwink.yaml
  - httproute-haute-equipe.yaml
  - httproute-grafana.yaml
  - httproute-demo.yaml
  - http-redirect.yaml
```

### Success Criteria:

#### Automated Verification:
- [ ] `httproute-demo.yaml` created with correct service name `demo-open-webui`
- [ ] `gateway.yaml` has 6 listeners (http + 5 HTTPS)
- [ ] `certificates.yaml` has 5 certificates
- [ ] `http-redirect.yaml` has 5 hostnames
- [ ] `kustomization.yaml` includes `httproute-demo.yaml`

#### Manual Verification:
- [ ] `kubectl apply -k infrastructure/previder-prod/gateway/` succeeds (applied in Phase 5)

---

## Phase 3: Update Observability Stack

### Overview
Add the demo tenant to Alloy log collection, Grafana datasources, and Loki alerting rules.

### Changes Required:

#### 1. Alloy Log Collection
**File**: `soev-gitops/observability/base/alloy/values.yaml` (edit)

**Change 1** -- Add `open-webui-demo` to pod discovery namespaces (line 97):

```
          names = ["observability", "shared-services", "open-webui-gradient", "open-webui-kwink", "open-webui-haute-equipe", "open-webui-demo"]
```

**Change 2** -- Add tenant routing rule after the `haute-equipe` block (after line 196):

```
        stage.match {
          selector = "{namespace=\"open-webui-demo\"}"
          stage.tenant {
            value = "demo"
          }
        }
```

#### 2. Grafana Datasource
**File**: `soev-gitops/observability/previder-prod/grafana-values-patch.yaml` (edit)

Add after the `Loki (Haute Equipe)` datasource (after line 59):

```yaml
      - name: Loki (Demo)
        type: loki
        url: http://loki-gateway.observability.svc:80
        access: proxy
        uid: loki-demo
        jsonData:
          httpHeaderName1: "X-Scope-OrgID"
        secureJsonData:
          httpHeaderValue1: "demo"
```

#### 3. Loki Alerting Rules
**File**: `soev-gitops/observability/previder-prod/loki-rules-configmap.yaml` (edit)

Add after the `haute-equipe-rules.yaml` entry (after line 135):

```yaml
  # --- demo tenant rules ---
  demo-rules.yaml: |
    groups:
      - name: demo-log-alerts
        interval: 1m
        rules:
          - alert: DemoHighErrorRate
            expr: |
              sum(count_over_time({namespace="open-webui-demo"} | detected_level=~"error|fatal|critical" [5m])) > 50
            for: 5m
            labels:
              severity: warning
              tenant: demo
            annotations:
              summary: "High error rate in Demo namespace"
              description: "More than 50 error/exception/fatal log lines in open-webui-demo in the last 5 minutes."

          - alert: DemoNoLogs
            expr: |
              absent_over_time({namespace="open-webui-demo", container="open-webui"} [1h])
            for: 30m
            labels:
              severity: warning
              tenant: demo
            annotations:
              summary: "No logs from Demo Open WebUI"
              description: "No log lines received from the open-webui container in open-webui-demo namespace for 1 hour."
```

#### 4. Loki Ruler Volume Mount
**File**: `soev-gitops/observability/previder-prod/loki-values-patch.yaml` (edit)

Add volume and mount for demo rules after the haute-equipe entries.

In `backend.extraVolumes` (after line 121):
```yaml
    - name: loki-rules-demo
      configMap:
        name: loki-ruler-rules
        items:
          - key: demo-rules.yaml
            path: rules.yaml
```

In `backend.extraVolumeMounts` (after line 134):
```yaml
    - name: loki-rules-demo
      mountPath: /etc/loki/rules/demo
      readOnly: true
```

### Success Criteria:

#### Automated Verification:
- [ ] Alloy config includes `open-webui-demo` in namespace discovery and tenant routing
- [ ] Grafana values include `Loki (Demo)` datasource with `X-Scope-OrgID: demo`
- [ ] Loki rules ConfigMap includes `demo-rules.yaml` with 2 alert rules
- [ ] Loki values patch includes demo volume and mount

#### Manual Verification:
- [ ] After deploying observability (`./scripts/deploy-observability.sh previder-prod`), Grafana shows "Loki (Demo)" datasource
- [ ] Demo namespace logs appear in Grafana when queried

---

## Phase 4: Deploy Empty Tenant on Previder

### Overview
User deploys the tenant to Previder (empty, no data yet). This creates the namespace, PVCs, and services.

### Commands for User to Run:

```bash
# 1. Encrypt secrets (after filling in secrets.yaml)
cd soev-gitops
export SOPS_AGE_KEY_FILE=clusters/previder-prod/age.key
cd tenants/previder-prod/demo
sops --encrypt secrets.yaml > secrets.enc.yaml
rm secrets.yaml

# 2. Create namespace and GHCR pull secret
kubectl create namespace open-webui-demo
kubectl create secret docker-registry ghcr-secret \
  --docker-server=ghcr.io \
  --docker-username=$GITHUB_USER \
  --docker-password=$GITHUB_TOKEN \
  -n open-webui-demo

# 3. Apply gateway infrastructure (WITHOUT the demo-https listener first)
#    This allows cert-manager to issue the cert via HTTP-01
#    NOTE: temporarily remove the demo-https listener block from gateway.yaml
#    before applying, then add it back after cert is ready (Phase 6)
kubectl apply -k infrastructure/previder-prod/gateway/

# 4. Deploy the tenant
cd soev-gitops
./scripts/deploy-tenant-sops.sh previder-prod demo

# 5. Verify pods are running
kubectl get pods -n open-webui-demo
kubectl rollout status deployment/demo-open-webui -n open-webui-demo
```

### Success Criteria:

#### Manual Verification:
- [ ] All pods running: `demo-open-webui`, `demo-postgres-0`, `demo-weaviate-0`
- [ ] PVCs bound: `kubectl get pvc -n open-webui-demo`
- [ ] Open WebUI responds to health check: `kubectl exec -n open-webui-demo deployment/demo-open-webui -- wget -q -O- http://localhost:8080/health`

**Implementation Note**: After completing this phase and confirming pods are healthy, proceed to data migration.

---

## Phase 5: Data Migration

### Overview
Export all data from GKE and import to Previder. This is the critical phase -- handle with care.

### Pre-Migration: Freeze GKE

```bash
# Scale down GKE Open WebUI to prevent writes during migration
# (using GKE kubectl context)
kubectl scale deployment/open-webui-open-webui-stack-open-webui \
  -n open-webui --replicas=0
```

### 5a. PostgreSQL Migration

```bash
# === EXPORT from GKE ===
# Get the GKE postgres pod name
kubectl get pods -n open-webui -l app.kubernetes.io/component=postgres

# Dump the database (from GKE context)
kubectl exec -n open-webui <gke-postgres-pod> -- \
  pg_dump -U openwebui -d openwebui -Fc -f /tmp/openwebui.dump

# Copy dump to local machine
kubectl cp open-webui/<gke-postgres-pod>:/tmp/openwebui.dump ./openwebui.dump

# === IMPORT to Previder ===
# Scale down Open WebUI on Previder to prevent connections during restore
kubectl scale deployment/demo-open-webui -n open-webui-demo --replicas=0

# Copy dump to Previder postgres pod
kubectl cp ./openwebui.dump open-webui-demo/demo-postgres-0:/tmp/openwebui.dump

# Drop existing (empty) database and restore
kubectl exec -n open-webui-demo demo-postgres-0 -- \
  bash -c "dropdb -U openwebui openwebui && createdb -U openwebui openwebui"

kubectl exec -n open-webui-demo demo-postgres-0 -- \
  pg_restore -U openwebui -d openwebui --no-owner --no-privileges /tmp/openwebui.dump

# Verify row counts
kubectl exec -n open-webui-demo demo-postgres-0 -- \
  psql -U openwebui -d openwebui -c "SELECT 'users' as tbl, count(*) FROM \"user\" UNION ALL SELECT 'chats', count(*) FROM chat;"

# Clean up dump file
kubectl exec -n open-webui-demo demo-postgres-0 -- rm /tmp/openwebui.dump
rm ./openwebui.dump
```

### 5b. Weaviate Migration

```bash
# === EXPORT from GKE ===
# Port-forward GKE Weaviate
kubectl port-forward -n open-webui svc/open-webui-open-webui-stack-weaviate 8081:8080 &

# Create a backup (Weaviate filesystem backup)
curl -X POST http://localhost:8081/v1/backups/filesystem \
  -H 'Content-Type: application/json' \
  -d '{"id": "migration-backup"}'

# Wait for backup to complete
curl http://localhost:8081/v1/backups/filesystem/migration-backup

# The backup is stored inside the Weaviate pod at /var/lib/weaviate/backups/
# Copy it to local
kubectl cp open-webui/<gke-weaviate-pod>:/var/lib/weaviate/backups/migration-backup ./weaviate-backup/

kill %1  # stop port-forward

# === IMPORT to Previder ===
# Copy backup to Previder Weaviate pod
kubectl cp ./weaviate-backup/ open-webui-demo/demo-weaviate-0:/var/lib/weaviate/backups/migration-backup

# Port-forward Previder Weaviate
kubectl port-forward -n open-webui-demo svc/demo-weaviate 8082:8080 &

# Restore the backup
curl -X POST http://localhost:8082/v1/backups/filesystem/migration-backup/restore \
  -H 'Content-Type: application/json'

# Verify restore
curl http://localhost:8082/v1/backups/filesystem/migration-backup/restore

# Check collections exist
curl http://localhost:8082/v1/schema

kill %1  # stop port-forward
rm -rf ./weaviate-backup/
```

### 5c. Uploaded Files Migration

```bash
# === EXPORT from GKE ===
# Open WebUI stores uploaded files at /app/backend/data
kubectl cp open-webui/<gke-open-webui-pod>:/app/backend/data ./open-webui-data/

# === IMPORT to Previder ===
# Scale up Open WebUI briefly to get the pod, then scale down again
kubectl scale deployment/demo-open-webui -n open-webui-demo --replicas=1
kubectl rollout status deployment/demo-open-webui -n open-webui-demo
kubectl scale deployment/demo-open-webui -n open-webui-demo --replicas=0

# Copy files to the PVC (mounted by the pod)
# Since the pod is scaled down, we need a temporary pod to access the PVC
kubectl run data-mover --image=busybox --restart=Never \
  -n open-webui-demo \
  --overrides='{"spec":{"containers":[{"name":"data-mover","image":"busybox","command":["sleep","3600"],"volumeMounts":[{"name":"data","mountPath":"/data"}]}],"volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"demo-open-webui"}}]}}'

kubectl wait --for=condition=ready pod/data-mover -n open-webui-demo
kubectl cp ./open-webui-data/ open-webui-demo/data-mover:/data/
kubectl delete pod data-mover -n open-webui-demo

rm -rf ./open-webui-data/
```

### 5d. Scale Up Previder

```bash
# Scale Open WebUI back up
kubectl scale deployment/demo-open-webui -n open-webui-demo --replicas=1
kubectl rollout status deployment/demo-open-webui -n open-webui-demo

# Verify via port-forward (before DNS cutover)
kubectl port-forward -n open-webui-demo svc/demo-open-webui 8080:8080
# Visit http://localhost:8080 -- verify login, chat history, files
```

### Success Criteria:

#### Manual Verification:
- [ ] PostgreSQL: User count matches GKE (`SELECT count(*) FROM "user"`)
- [ ] PostgreSQL: Chat count matches GKE (`SELECT count(*) FROM chat`)
- [ ] Weaviate: Schema/collections exist (`curl localhost:8082/v1/schema`)
- [ ] Weaviate: Object counts match GKE
- [ ] Files: `/app/backend/data` contents match GKE
- [ ] Login works via port-forward
- [ ] Chat history visible for test user
- [ ] RAG search returns results

**Implementation Note**: This is the point of no return. Verify everything thoroughly before proceeding to DNS cutover.

---

## Phase 6: DNS Cutover and TLS

### Overview
Switch DNS from GKE to Previder and issue the TLS certificate.

### Important: TLS Certificate Issuance Order

Cilium's HTTPS listener redirects HTTP-01 challenge traffic to HTTPS, causing validation to fail. The documented workaround (`soev-gitops/README.md:365-370`):

1. Apply gateway config **without** the `demo-https` listener
2. Wait for cert-manager to issue the certificate
3. Add the `demo-https` listener back
4. Re-apply gateway config

### Commands:

```bash
# 1. Switch DNS: point demo.soev.ai to 10.10.0.200
#    (Do this in your DNS provider)

# 2. Wait for DNS propagation
dig demo.soev.ai  # should resolve to 10.10.0.200

# 3. Apply gateway WITHOUT the demo-https listener
#    (temporarily comment out the demo-https block in gateway.yaml)
kubectl apply -k infrastructure/previder-prod/gateway/

# 4. Wait for certificate issuance
kubectl get certificate -n gateway demo-tls -w
# Wait until READY = True

# 5. Add the demo-https listener back to gateway.yaml
#    (uncomment the block)
kubectl apply -k infrastructure/previder-prod/gateway/

# 6. Verify HTTPS
curl -I https://demo.soev.ai
```

### Success Criteria:

#### Manual Verification:
- [ ] DNS resolves `demo.soev.ai` to `10.10.0.200`
- [ ] Certificate shows `Ready=True`: `kubectl get certificate -n gateway demo-tls`
- [ ] `https://demo.soev.ai` returns 200 with valid TLS
- [ ] HTTP redirects to HTTPS: `curl -I http://demo.soev.ai` returns 301

---

## Phase 7: Deploy Observability Updates

### Overview
Deploy the updated observability stack so the demo tenant gets log collection, Grafana datasource, and alerting.

### Commands:

```bash
# Deploy the full observability stack (picks up Alloy, Grafana, and Loki changes)
./scripts/deploy-observability.sh previder-prod
```

### Success Criteria:

#### Manual Verification:
- [ ] Alloy pod restarted and running: `kubectl get pods -n observability -l app.kubernetes.io/name=alloy`
- [ ] Grafana shows "Loki (Demo)" datasource
- [ ] Query `{namespace="open-webui-demo"}` in Loki (Demo) returns logs
- [ ] Loki ruler has demo rules loaded: check Grafana alerting rules page
- [ ] Demo alerts appear in Grafana under Alerting > Alert rules

---

## Phase 8: Final Verification and Cleanup

### Verification Checklist:

- [ ] `https://demo.soev.ai` loads Open WebUI with valid TLS
- [ ] All user accounts can log in
- [ ] Chat history is complete and intact
- [ ] Uploaded files / knowledge bases are accessible
- [ ] RAG search returns relevant results
- [ ] Web search (SearXNG) works
- [ ] New chats can be created and responses stream
- [ ] Logs flowing to Grafana "Loki (Demo)" datasource
- [ ] OTEL traces visible in Tempo (if applicable)
- [ ] No error alerts firing in Grafana

### GKE Cleanup (after verification period):

```bash
# After sufficient burn-in period (suggest 1-2 weeks):

# Delete GKE demo resources
kubectl delete namespace open-webui  # removes all tenant resources

# If decommissioning the entire GKE cluster:
gcloud container clusters delete demo-cluster --zone=europe-west4-a
gcloud compute addresses delete demo-ip --region=europe-west4
```

### Git Commit:

```bash
cd soev-gitops
git add tenants/previder-prod/demo/
git add infrastructure/previder-prod/gateway/
git add observability/
# Only add .enc.yaml files, never plaintext secrets
git commit -m "feat: migrate demo tenant from gke-demo to previder-prod"
```

---

## Migration Notes

### Downtime Window

The migration requires downtime between:
- Scaling down GKE (Phase 5 start)
- HTTPS working on Previder (Phase 6 end)

To minimize this window:
- Have all config files ready (Phases 1-3) before starting
- Pre-deploy empty tenant (Phase 4) before the maintenance window
- The actual data migration + DNS cutover is the only part that requires downtime

### Rollback Plan

If something goes wrong during migration:
1. Point DNS back to GKE cluster IP
2. Scale GKE Open WebUI back to 1 replica
3. Service restored on GKE within DNS TTL

### Data Integrity

- PostgreSQL: Use `pg_dump -Fc` (custom format) for reliable dump/restore
- Weaviate: Use native backup/restore API for consistency
- Files: `kubectl cp` preserves file permissions and timestamps
- **Verify row counts and object counts** before proceeding to DNS cutover

## References

- Research: `thoughts/shared/research/2026-02-04-gke-to-previder-migration.md`
- Existing Previder tenant pattern: `soev-gitops/tenants/previder-prod/gradient/`
- Gateway config: `soev-gitops/infrastructure/previder-prod/gateway/`
- Alloy config: `soev-gitops/observability/base/alloy/values.yaml`
- Loki rules: `soev-gitops/observability/previder-prod/loki-rules-configmap.yaml`
- Helm chart: `open-webui/helm/open-webui-tenant/`
