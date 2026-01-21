# Kubernetes Access Setup - Soev AI Clusters

This guide covers RBAC setup for multi-tenant access to Soev AI Kubernetes clusters.

## Clusters

| Cluster | Provider | Purpose | Admin Kubeconfig (1Password) |
|---------|----------|---------|------------------------------|
| `previder-prod` | Previder | Production tenants | `kube/soev-previder` |
| `gke-demo` | GCP | Demo/staging | `kube/soev-gke` |
| `intermax-prod` | Intermax | Future expansion | TBD |

## Namespace Structure

All clusters follow the same namespace layout:

| Namespace | Purpose |
|-----------|---------|
| `shared-services` | Gradient Gateway, SearXNG, Crawl4AI, Reranker (shared per cluster) |
| `observability` | LGTM stack: Loki, Grafana, Tempo, Mimir, Alloy |
| `ingress-nginx` | Ingress controller |
| `cert-manager` | TLS certificate management |
| `open-webui-{tenant}` | Per-tenant Open WebUI + PostgreSQL + Weaviate |

## RBAC Roles

| Role Pattern | Scope | Purpose |
|--------------|-------|---------|
| `{tenant}-admin` | `open-webui-{tenant}` | Tenant admin (e.g., `demo-admin` for `open-webui-demo`) |
| `shared-services-admin` | `shared-services`, `ingress-nginx`, `cert-manager` | Infrastructure management |
| `observability-admin` | `observability` | Dashboards, alerting, log access |

## Prerequisites

- Cluster-admin access (see 1Password vault `kube` for your cluster)
- `kubectl` installed
- `op` CLI configured for 1Password

## 1. Create Namespaces

Replace `CLUSTER_ALIAS` with your cluster's kubectl alias (e.g., `ksoev-previder`, `ksoev-gke`).

```bash
# Infrastructure namespaces (once per cluster)
$CLUSTER_ALIAS create namespace shared-services
$CLUSTER_ALIAS create namespace observability
$CLUSTER_ALIAS create namespace ingress-nginx
$CLUSTER_ALIAS create namespace cert-manager

# Tenant namespace (repeat for each tenant)
$CLUSTER_ALIAS create namespace open-webui-{tenant}  # e.g., open-webui-demo
```

## 2. RBAC Setup

### Tenant Admin (per tenant)

Replace `{tenant}` with the tenant name (e.g., `demo`, `kwink`, `client-a`).

```bash
TENANT=demo  # Change this for each tenant
NAMESPACE="open-webui-${TENANT}"

# Create service account
$CLUSTER_ALIAS create serviceaccount ${TENANT}-admin -n ${NAMESPACE}

# Create namespace-scoped admin role
$CLUSTER_ALIAS apply -f - <<EOF
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: tenant-admin
  namespace: ${NAMESPACE}
rules:
- apiGroups: ["", "apps", "networking.k8s.io", "batch", "autoscaling"]
  resources: ["*"]
  verbs: ["*"]
- apiGroups: ["cert-manager.io"]
  resources: ["certificates", "certificaterequests"]
  verbs: ["get", "list", "watch", "create", "delete"]
EOF

# Bind role to service account
$CLUSTER_ALIAS create rolebinding ${TENANT}-admin-binding \
  --role=tenant-admin \
  --serviceaccount=${NAMESPACE}:${TENANT}-admin \
  -n ${NAMESPACE}
```

### Shared Services Admin

Manages Gradient Gateway stack, ingress, and certificates.

```bash
# Create service account in shared-services namespace
$CLUSTER_ALIAS create serviceaccount shared-services-admin -n shared-services

# Create ClusterRole for multi-namespace access
$CLUSTER_ALIAS apply -f - <<'EOF'
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: shared-services-admin
rules:
- apiGroups: ["", "apps", "networking.k8s.io", "batch", "autoscaling"]
  resources: ["*"]
  verbs: ["*"]
- apiGroups: ["cert-manager.io"]
  resources: ["*"]
  verbs: ["*"]
- apiGroups: ["gateway.networking.k8s.io"]
  resources: ["*"]
  verbs: ["*"]
EOF

# Bind to infrastructure namespaces
for ns in shared-services ingress-nginx cert-manager; do
  $CLUSTER_ALIAS create rolebinding shared-services-admin-binding \
    --clusterrole=shared-services-admin \
    --serviceaccount=shared-services:shared-services-admin \
    -n $ns
done

# Grant read access to all namespaces (for debugging)
$CLUSTER_ALIAS apply -f - <<'EOF'
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: shared-services-admin-readonly
subjects:
- kind: ServiceAccount
  name: shared-services-admin
  namespace: shared-services
roleRef:
  kind: ClusterRole
  name: view
  apiGroup: rbac.authorization.k8s.io
EOF
```

### Observability Admin

Manages LGTM stack (Loki, Grafana, Tempo, Mimir) and Alloy collectors.

```bash
# Create service account
$CLUSTER_ALIAS create serviceaccount observability-admin -n observability

# Create role with full observability namespace access
$CLUSTER_ALIAS apply -f - <<'EOF'
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: observability-admin
  namespace: observability
rules:
- apiGroups: ["", "apps", "networking.k8s.io", "batch", "autoscaling"]
  resources: ["*"]
  verbs: ["*"]
- apiGroups: ["monitoring.coreos.com"]
  resources: ["*"]
  verbs: ["*"]
EOF

# Bind role
$CLUSTER_ALIAS create rolebinding observability-admin-binding \
  --role=observability-admin \
  --serviceaccount=observability:observability-admin \
  -n observability

# Grant cluster-wide read access for metrics/logs scraping
$CLUSTER_ALIAS apply -f - <<'EOF'
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: observability-admin-cluster-view
subjects:
- kind: ServiceAccount
  name: observability-admin
  namespace: observability
roleRef:
  kind: ClusterRole
  name: view
  apiGroup: rbac.authorization.k8s.io
EOF
```

## 3. Generate Kubeconfigs

### Helper Script

Save this as `generate-kubeconfig.sh`. It works with any cluster by reading from your current kubeconfig context.

```bash
#!/bin/bash
set -e

SA_NAME=$1
NAMESPACE=$2
OUTPUT_FILE=$3
CLUSTER_NAME=${4:-$(kubectl config current-context)}
CLUSTER_SERVER=${5:-$(kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')}

if [[ -z "$SA_NAME" || -z "$NAMESPACE" || -z "$OUTPUT_FILE" ]]; then
  echo "Usage: $0 <service-account> <namespace> <output-file> [cluster-name] [cluster-server]"
  exit 1
fi

echo "Generating kubeconfig for $SA_NAME in $NAMESPACE on $CLUSTER_NAME..."

# Get cluster CA cert from current context
CA_CERT=$(kubectl config view --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')

# Generate token (1 year expiry)
TOKEN=$(kubectl create token "$SA_NAME" -n "$NAMESPACE" --duration=8760h)

# Create kubeconfig
cat > "$OUTPUT_FILE" <<EOF
apiVersion: v1
kind: Config
clusters:
- name: ${CLUSTER_NAME}
  cluster:
    certificate-authority-data: ${CA_CERT}
    server: ${CLUSTER_SERVER}
contexts:
- name: ${SA_NAME}@${CLUSTER_NAME}
  context:
    cluster: ${CLUSTER_NAME}
    namespace: ${NAMESPACE}
    user: ${SA_NAME}
current-context: ${SA_NAME}@${CLUSTER_NAME}
users:
- name: ${SA_NAME}
  user:
    token: ${TOKEN}
EOF

echo "Kubeconfig written to $OUTPUT_FILE"
```

### Generate Kubeconfigs (Example for Previder)

```bash
chmod +x generate-kubeconfig.sh

# Set context to target cluster first
export KUBECONFIG=<(op document get "soev-previder" --vault kube)

# Infrastructure roles
./generate-kubeconfig.sh shared-services-admin shared-services kubeconfig-previder-shared.yaml soev-previder
./generate-kubeconfig.sh observability-admin observability kubeconfig-previder-observability.yaml soev-previder

# Tenant roles (repeat for each tenant)
./generate-kubeconfig.sh demo-admin open-webui-demo kubeconfig-previder-demo.yaml soev-previder
```

### Store in 1Password

Naming convention: `soev-{cluster}-{role}` (e.g., `soev-previder-demo`, `soev-gke-shared`)

```bash
# Upload each kubeconfig as a Document in the 'kube' vault
op document create kubeconfig-previder-shared.yaml --vault kube --title "soev-previder-shared"
op document create kubeconfig-previder-observability.yaml --vault kube --title "soev-previder-observability"
op document create kubeconfig-previder-demo.yaml --vault kube --title "soev-previder-demo"

# Clean up local files
rm kubeconfig-*.yaml
```

## 4. ZSH Shortcuts

Add to your `~/.zshrc`:

```bash
# =============================================================================
# Soev AI Clusters - Admin Access (full cluster access)
# =============================================================================
ksoev-previder() {
  KUBECONFIG=<(op document get "soev-previder" --vault kube) kubectl "$@"
}

ksoev-gke() {
  KUBECONFIG=<(op document get "soev-gke" --vault kube) kubectl "$@"
}

# =============================================================================
# Previder Cluster - Role-based access
# =============================================================================
# Shared services (shared-services, ingress-nginx, cert-manager)
ksoev-previder-shared() {
  KUBECONFIG=<(op document get "soev-previder-shared" --vault kube) kubectl "$@"
}

# Observability (LGTM stack)
ksoev-previder-obs() {
  KUBECONFIG=<(op document get "soev-previder-observability" --vault kube) kubectl "$@"
}

# Tenant: demo (open-webui-demo namespace)
ksoev-previder-demo() {
  KUBECONFIG=<(op document get "soev-previder-demo" --vault kube) kubectl "$@"
}

# =============================================================================
# GKE Cluster - Role-based access
# =============================================================================
ksoev-gke-shared() {
  KUBECONFIG=<(op document get "soev-gke-shared" --vault kube) kubectl "$@"
}

ksoev-gke-demo() {
  KUBECONFIG=<(op document get "soev-gke-demo" --vault kube) kubectl "$@"
}
```

Reload your shell:

```bash
source ~/.zshrc
```

## 5. Usage Examples

```bash
# =============================================================================
# Admin access - full cluster visibility
# =============================================================================
ksoev-previder get pods -A
ksoev-previder get nodes

# =============================================================================
# Tenant access (scoped to open-webui-demo namespace)
# =============================================================================
ksoev-previder-demo get pods
ksoev-previder-demo apply -f deployment.yaml
ksoev-previder-demo logs -f deployment/open-webui

# =============================================================================
# Shared services - manage gateway, ingress, certs
# =============================================================================
ksoev-previder-shared get pods -n shared-services
ksoev-previder-shared get pods -n ingress-nginx
ksoev-previder-shared get certificates -n cert-manager

# =============================================================================
# Observability - Grafana, Loki, etc.
# =============================================================================
ksoev-previder-obs get pods
ksoev-previder-obs port-forward svc/grafana 3000:3000
```

## 6. Verify Permissions

```bash
# Tenant can manage their own namespace
ksoev-previder-demo auth can-i create deployments
# yes

# Tenant cannot access other namespaces
ksoev-previder-demo auth can-i create deployments -n kube-system
# no

ksoev-previder-demo auth can-i create deployments -n shared-services
# no

# Shared services has read access everywhere (for debugging)
ksoev-previder-shared auth can-i get pods -n observability
# yes

# But can only write to its namespaces
ksoev-previder-shared auth can-i create deployments -n observability
# no

# Observability admin can manage their namespace
ksoev-previder-obs auth can-i create configmaps -n observability
# yes
```

## 7. Onboarding a New Tenant

Quick checklist for adding a new tenant to a cluster:

1. Create namespace: `open-webui-{tenant}`
2. Create service account: `{tenant}-admin`
3. Create Role and RoleBinding (see Tenant Admin section)
4. Generate kubeconfig
5. Store in 1Password: `soev-{cluster}-{tenant}`
6. Add ZSH shortcut (optional)
7. Deploy Open WebUI tenant via soev-gitops

## Token Renewal

Tokens expire after 1 year. To renew:

```bash
# Set context to target cluster
export KUBECONFIG=<(op document get "soev-previder" --vault kube)

# Regenerate kubeconfig
./generate-kubeconfig.sh demo-admin open-webui-demo kubeconfig-previder-demo.yaml soev-previder

# Update in 1Password (delete old, create new)
op document delete "soev-previder-demo" --vault kube
op document create kubeconfig-previder-demo.yaml --vault kube --title "soev-previder-demo"
```

## Troubleshooting

**"error: You must be logged in to the server (Unauthorized)"**
- Token expired, regenerate kubeconfig

**"Error from server (Forbidden): pods is forbidden"**
- Service account doesn't have access to that namespace
- Check with `kubectl auth can-i --list`

**Tenant can't reach shared services**
- Verify NetworkPolicy allows egress to `shared-services` namespace
- Check service DNS: `gradient-gateway.shared-services.svc.cluster.local`

**1Password CLI not working**
- Ensure 1Password desktop app is running
- Enable CLI integration in 1Password Settings > Developer

## Related Documentation

- **Deployments**: See `soev-gitops/` repository for actual deployment manifests
- **GitOps Structure**: See `thoughts/shared/research/2026-01-20-gitops-helm-restructure.md`
- **Helm Charts**: `open-webui/helm/open-webui-tenant/` and `genai-utils/helm/gradient-gateway/`
