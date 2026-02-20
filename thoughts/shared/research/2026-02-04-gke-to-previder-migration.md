---
date: 2026-02-04T10:04:03Z
researcher: claude
git_commit: 64c3333
branch: main
repository: soev-gitops + open-webui
topic: "Migrating the GKE Open WebUI demo instance to the Previder tenant"
tags: [research, migration, gke, previder, platform-independence, kubernetes, helm]
status: complete
last_updated: 2026-02-04
last_updated_by: claude
---

# Research: Migrating the GKE Open WebUI Demo Instance to the Previder Tenant

**Date**: 2026-02-04T10:04:03Z
**Researcher**: Claude
**Git Commit**: 64c3333
**Branch**: main
**Repository**: soev-gitops + open-webui

## Research Question

What would it take to migrate the Open WebUI instance running on GKE (`gke-demo` cluster, `demo` tenant) to the Previder bare-metal cluster (`previder-prod`)? How platform-independent is the current setup?

## Summary

The migration is **straightforward and well-supported by the existing tooling**. The `soev-gitops` repository was explicitly designed for multi-cluster deployment, and the `open-webui-tenant` Helm chart abstracts away all platform differences through a small set of toggle values. The main work items are:

1. **Scaffold a new tenant** on `previder-prod` using `create-tenant.sh` (~5 min)
2. **Adapt 5 platform-specific settings** in `values-patch.yaml` (storage class, ingress, secrets, network policy, Cilium policy)
3. **Migrate secrets** from GCP Secret Manager to SOPS/age encryption
4. **Add Gateway infrastructure** (HTTPS listener, Certificate, HTTPRoute, HTTP redirect entry)
5. **Decide on data migration** (fresh start vs. PostgreSQL dump/restore + Weaviate backup)
6. **DNS** -- point the new domain at `10.10.0.200`

There are **no code changes required** to Open WebUI itself. The Helm chart handles all platform differences through values overrides.

## Detailed Findings

### Platform Differences (GKE vs. Previder)

The two clusters differ in exactly 6 infrastructure concerns. All are parameterized in the Helm chart:

| Concern | GKE Demo | Previder Prod | Helm Toggle |
|---------|----------|---------------|-------------|
| **Storage** | `standard-rwo` (GCE PD CSI) | `longhorn` (Longhorn) | `global.storageClass` |
| **Ingress** | NGINX Ingress Controller | Cilium Gateway API | `ingress.enabled` + external Gateway config |
| **TLS** | cert-manager annotation on Ingress | cert-manager Certificate + Gateway listener | External to chart |
| **Secrets** | External Secrets Operator + GCP Secret Manager | SOPS/age encrypted files | `externalSecrets.enabled` |
| **Network Policy** | Standard K8s NetworkPolicy only | Standard + CiliumNetworkPolicy | `ciliumNetworkPolicy.enabled` |
| **Load Balancing** | GKE cloud LB (automatic) | Cilium LB IPAM + L2 announcement (`10.10.0.200`) | External to chart |

### What the Helm Chart Already Handles

The `open-webui-tenant` chart (`open-webui/helm/open-webui-tenant/`) uses conditional templates for all platform-specific resources:

- **Secrets**: `templates/secrets.yaml` (inline, for SOPS) vs. `templates/external-secrets.yaml` + `templates/cluster-secret-store.yaml` (for GCP SM) -- toggled by `externalSecrets.enabled`
- **CiliumNetworkPolicy**: `templates/cilium-networkpolicy.yaml` -- creates `allow-gateway-ingress` policy only when `ciliumNetworkPolicy.enabled: true`
- **Ingress**: `templates/ingress.yaml` -- only created when `ingress.enabled: true` (disabled on Previder since Cilium Gateway handles routing externally)
- **Storage class**: All PVCs use the `open-webui-tenant.storageClass` helper which renders `storageClassName` only when `global.storageClass` is set

No `if/else` branching names "GKE" or "Previder" -- it's all generic toggles.

### Step-by-Step Migration Plan

#### Step 1: Scaffold the Tenant

```bash
cd soev-gitops
./scripts/create-tenant.sh previder-prod demo demo.soev.ai
```

This creates `tenants/previder-prod/demo/` with namespace, resourcequota, limitrange, values-patch template, and secrets template. The script auto-detects `previder-prod` and generates:
- `storageClass: "longhorn"`
- `ingress.enabled: false`
- `ciliumNetworkPolicy.enabled: true`
- `externalSecrets.enabled: false`
- An HTTPRoute in `infrastructure/previder-prod/gateway/httproute-demo.yaml`

(`scripts/create-tenant.sh:49-103` handles the previder-prod branch)

#### Step 2: Configure `values-patch.yaml`

Port the application-level settings from the GKE demo (`tenants/gke-demo/demo/values-patch.yaml`) into the new Previder tenant file. The platform settings are already correct from the scaffold; you only need to copy over:

- `openWebui.config.*` (CORS, sharing permissions, telemetry)
- Any tenant-specific LLM endpoint, embedding model, RAG settings
- Admin email/name
- OAuth/SSO config (if applicable)

The key values that **change** between platforms:

```yaml
# These are AUTO-SET by create-tenant.sh for previder-prod:
global:
  storageClass: "longhorn"           # was: standard-rwo
  imagePullSecrets: [ghcr-secret]    # same

ingress:
  enabled: false                     # was: true (nginx)
  # No className, annotations, or tls needed

ciliumNetworkPolicy:
  enabled: true                      # was: false (or absent)

externalSecrets:
  enabled: false                     # was: true (GCP SM)

# OTEL endpoint stays the same (alloy.observability.svc:4317)
```

#### Step 3: Migrate Secrets

The GKE demo uses External Secrets Operator pulling from GCP Secret Manager (project `soev-ai-001`). For Previder, you need to:

1. Retrieve current secret values from GCP SM (or generate new ones)
2. Create `tenants/previder-prod/demo/secrets.yaml` with the values
3. Encrypt with SOPS:
   ```bash
   export SOPS_AGE_KEY_FILE=clusters/previder-prod/age.key
   cd tenants/previder-prod/demo
   sops --encrypt secrets.yaml > secrets.enc.yaml
   rm secrets.yaml
   ```

Required secrets (from `tenants/gke-demo/demo/values-patch.yaml:48-55` and chart `templates/secrets.yaml`):
- `webuiSecretKey` -- session encryption key
- `postgresPassword` -- PostgreSQL password
- `openaiApiKey` -- LLM API key
- `ragOpenaiApiKey` -- RAG embedding API key
- (optional) `microsoftClientSecret`, `externalDocumentLoaderApiKey`, `ragExternalRerankerApiKey`

#### Step 4: Add Gateway Infrastructure

The `create-tenant.sh` script auto-generates the HTTPRoute, but you still need to manually add:

**4a.** HTTPS listener in `infrastructure/previder-prod/gateway/gateway.yaml`:
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

**4b.** Certificate in `infrastructure/previder-prod/gateway/certificates.yaml`:
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

**4c.** Hostname in `infrastructure/previder-prod/gateway/http-redirect.yaml` hostnames list

**4d.** HTTPRoute entry in `infrastructure/previder-prod/gateway/kustomization.yaml` resources list

**4e.** TLS certificate issuance order: Apply gateway config **without** the HTTPS listener first, wait for cert-manager to issue the cert, then add the listener (due to Cilium's HTTPS redirect interfering with HTTP-01 challenges -- documented in `soev-gitops/README.md:365-370`)

#### Step 5: DNS

Point `demo.soev.ai` (or whatever domain) at `10.10.0.200` (the Cilium Gateway L2 address).

#### Step 6: Create GHCR Pull Secret + Deploy

```bash
kubectl create namespace open-webui-demo
kubectl create secret docker-registry ghcr-secret \
  --docker-server=ghcr.io \
  --docker-username=$GITHUB_USER \
  --docker-password=$GITHUB_TOKEN \
  -n open-webui-demo

kubectl apply -k infrastructure/previder-prod/gateway/
./scripts/deploy-tenant-sops.sh previder-prod demo
```

#### Step 7: Observability (Optional)

Add a Loki datasource for the demo tenant in `observability/previder-prod/grafana-values-patch.yaml` and add tenant-specific alerting rules to the Loki rules ConfigMap.

Also add `open-webui-demo` to Alloy's log collection targets in `observability/base/alloy/values.yaml` (currently hardcoded to `open-webui-gradient, open-webui-kwink, open-webui-haute-equipe`).

### Data Migration Considerations

| Approach | Effort | Risk |
|----------|--------|------|
| **Fresh start** (no data migration) | Minimal | None -- just deploy |
| **PostgreSQL dump/restore** | Medium | Need to ensure schema compatibility, export from GKE pod, import to Previder pod |
| **Full data migration** (PG + Weaviate + uploaded files) | Higher | Weaviate backup/restore, PVC data copy, potential embedding model mismatch |

For a platform independence test, a **fresh start** is the cleanest approach. If data needs to move, the existing migration tooling in `soev-gitops/migration/` covers LibreChat-to-OpenWebUI migration but not cross-cluster OpenWebUI migration. A `pg_dump`/`pg_restore` would be the standard PostgreSQL approach.

### Platform Independence Assessment

**Verdict: The architecture is highly platform-independent.**

The design separates concerns cleanly:

1. **Application logic** (Open WebUI code) -- zero platform coupling
2. **Helm chart** (`open-webui-tenant`) -- abstracts all platform differences behind 4 boolean/string toggles
3. **GitOps layer** (`soev-gitops`) -- base/overlay Kustomize pattern with cluster-specific directories
4. **Deployment scripts** -- parameterized by cluster name, with SOPS/non-SOPS variants

The only areas with platform coupling are:

| Coupling Point | Impact | Severity |
|----------------|--------|----------|
| Alloy log collection targets are hardcoded to previder-prod namespace names | GKE demo tenant logs not collected by Alloy | Low -- observability config, not app functionality |
| `gateway-ingress-cilium-policy.yaml` in base infra hardcodes `open-webui-kwink` namespace | CiliumNetworkPolicy only covers kwink, not other tenants | Low -- other tenants use the Helm chart's `ciliumNetworkPolicy.enabled` |
| GKE `deploy-observability.sh` has `if gke-demo` branch for Grafana password | Cluster name dependency in script | Low -- only affects observability deployment |
| External Secrets template only maps 4 of 7 possible secrets | Microsoft/reranker/loader secrets unavailable via GCP SM | Medium -- limits GKE feature parity |

### What Would Make It Even More Portable

1. **FluxCD adoption** -- The Kustomization stubs (`resources: []`) throughout the repo are waiting for FluxCD HelmRelease resources. This would eliminate the imperative deploy scripts and make cluster bootstrapping fully declarative.
2. **DNS-01 validation** -- The README notes this as a high-priority task (`soev-gitops/README.md:65`). HTTP-01 challenges are fragile with Cilium HTTPS listeners. DNS-01 would also enable wildcard certificates.
3. **Alloy dynamic log targets** -- Replace hardcoded namespace list with label-based discovery (e.g., all namespaces with `uses-shared-services: "true"`).
4. **External Secrets parity** -- Add the 3 missing secret mappings to `templates/external-secrets.yaml` so GKE tenants can use Microsoft SSO, reranker, and document loader APIs.

## Code References

- `soev-gitops/scripts/create-tenant.sh:49-103` -- Previder-specific tenant scaffolding logic
- `soev-gitops/scripts/deploy-tenant-sops.sh:41-66` -- SOPS secret decryption flow
- `soev-gitops/tenants/gke-demo/demo/values-patch.yaml:48-55` -- GKE External Secrets config
- `soev-gitops/infrastructure/previder-prod/gateway/gateway.yaml:1-66` -- Cilium Gateway with all HTTPS listeners
- `soev-gitops/infrastructure/previder-prod/gateway/certificates.yaml:1-51` -- TLS certificates for existing tenants
- `open-webui/helm/open-webui-tenant/values.yaml:473-498` -- External Secrets + Network Policy toggles
- `open-webui/helm/open-webui-tenant/templates/cilium-networkpolicy.yaml` -- Cilium ingress policy (13 lines)
- `open-webui/helm/open-webui-tenant/templates/secrets.yaml` -- Inline secrets (SOPS path)
- `open-webui/helm/open-webui-tenant/templates/external-secrets.yaml` -- GCP Secret Manager path

## Architecture Insights

- The entire multi-cluster setup is designed around the **base/overlay Kustomize pattern** at every layer (infrastructure, shared-services, tenants, observability), with Helm values layering (base + cluster patch + secrets) on top.
- Platform differences are concentrated in **6 toggles**, none of which require chart code changes -- only values overrides.
- The `create-tenant.sh` script is the single source of truth for what differs between clusters. It branches on `$CLUSTER == "previder-prod"` at line 49 to generate the correct defaults.
- Deployment is currently **imperative** (shell scripts + `helm upgrade --install`), with FluxCD planned but not yet adopted. This actually makes migration easier -- you just run the scripts against a different cluster context.

## Historical Context

- `soev-gitops/migration/README.md` -- Existing migration tooling covers LibreChat-to-OpenWebUI data migration (users, conversations, files). Not directly applicable but shows migration patterns.
- `soev-gitops/docs/audits/2026-01-22-previder-prod-security-audit.md` -- Security audit of previder-prod rated MEDIUM RISK overall. Relevant to ensure the migrated tenant meets the same security baseline.
- `soev-gitops/docs/architecture/workspace.dsl` -- C4 architecture model defines three deployment environments (Docker Compose, Kubernetes multi-tenant, Minimal edge), confirming platform flexibility is a design goal.

## Related Research

No prior research documents found on this topic.

## Open Questions

1. **Domain name**: Will the demo tenant keep `demo.soev.ai` or get a new domain on Previder?
2. **Data migration**: Is this a fresh deployment or should data (users, chats, files, vector embeddings) be migrated?
3. **LLM provider**: Will the Previder demo use the same LLM endpoint as GKE, or switch to a different provider (e.g., HuggingFace Router like Gradient, or Nebul like Kwink)?
4. **GKE shutdown**: After migration, should the GKE demo cluster be decommissioned? This would save cloud costs.
5. **Observability**: Should the demo tenant be added to the existing Previder LGTM stack (Alloy log targets, Grafana datasource, Loki alerting rules)?
