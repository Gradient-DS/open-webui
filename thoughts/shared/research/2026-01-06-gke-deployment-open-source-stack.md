---
date: 2026-01-06T12:00:00+01:00
researcher: Claude
git_commit: cde6c1f98802afcb06cfdfd6c116a44af73486fc
branch: feat/admin-config
repository: Gradient-DS/open-webui
topic: "GKE Deployment with Open Source Tooling Stack"
tags: [research, kubernetes, gke, deployment, infrastructure, ingress-nginx, cert-manager, prometheus, grafana, loki]
status: complete
last_updated: 2026-01-06
last_updated_by: Claude
---

# Research: GKE Deployment with Open Source Tooling Stack

**Date**: 2026-01-06T12:00:00+01:00
**Researcher**: Claude
**Git Commit**: cde6c1f98802afcb06cfdfd6c116a44af73486fc
**Branch**: feat/admin-config
**Repository**: Gradient-DS/open-webui

## Research Question

What code, configurations, and infrastructure are needed to deploy Open WebUI (including web search, PostgreSQL, and persistent storage) on GKE using:
- **Ingress**: ingress-nginx or Traefik
- **TLS/Certs**: cert-manager + Let's Encrypt
- **Monitoring**: Prometheus + Grafana
- **Logging**: Loki + Promtail

With preference for Google Cloud CLI configuration.

## Summary

Open WebUI can be deployed on GKE with the requested stack. The codebase already supports PostgreSQL, web search (SearXNG), and has extensive Docker configuration. No Kubernetes manifests exist in the repo (external helm-charts at https://github.com/open-webui/helm-charts), so we'll need to create custom manifests.

**Total estimated effort: 3-5 days for initial deployment, 1-2 additional days for production hardening.**

---

## 1. Application Architecture (Current State)

### Services Required

| Service | Image | Purpose | Persistence |
|---------|-------|---------|-------------|
| `open-webui` | `ghcr.io/open-webui/open-webui:main` | Main application | `/app/backend/data` |
| `postgres` | `postgres:16-alpine` | Database | `/var/lib/postgresql/data` |
| `searxng` | `searxng/searxng:latest` | Web search | `/etc/searxng` (config) |
| `playwright` | `mcr.microsoft.com/playwright:v1.57.0-noble` | JS rendering for web loader | None (stateless) |
| `reranker` (optional) | `michaelf34/infinity:latest-cpu` | Search result reranking | `/app/.cache` |

### Container Configuration (from Dockerfile)

```
Port: 8080
Health check: GET /health
Startup: bash start.sh (uvicorn with configurable workers)
User: root (or configurable UID/GID with USE_PERMISSION_HARDENING)
```

### Environment Variables (Critical for Production)

**Security (Must Change)**:
```bash
WEBUI_SECRET_KEY=<generate-unique-secret>  # JWT signing
DATABASE_URL=postgresql://user:pass@postgres:5432/openwebui
WEBUI_AUTH=True
```

**Database (PostgreSQL)**:
```bash
DATABASE_URL=postgresql://${DB_USER}:${DB_PASS}@postgres:5432/${DB_NAME}
# OR individual variables:
DATABASE_TYPE=postgresql
DATABASE_HOST=postgres
DATABASE_PORT=5432
DATABASE_NAME=openwebui
DATABASE_USER=openwebui
DATABASE_PASSWORD=<secret>
DATABASE_POOL_SIZE=10
DATABASE_POOL_MAX_OVERFLOW=20
```

**Web Search (SearXNG)**:
```bash
ENABLE_WEB_SEARCH=True
WEB_SEARCH_ENGINE=searxng
SEARXNG_QUERY_URL=http://searxng:8080/search
WEB_LOADER_ENGINE=playwright
PLAYWRIGHT_WS_URL=ws://playwright:3000
```

**LLM Provider** (at least one required):
```bash
OLLAMA_BASE_URL=http://ollama:11434
# OR
OPENAI_API_KEY=<key>
OPENAI_API_BASE_URL=https://api.openai.com/v1
```

---

## 2. GKE Cluster Setup (gcloud CLI)

### 2.1 Prerequisites

```bash
# Install gcloud CLI and authenticate
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud config set compute/region europe-west4
gcloud config set compute/zone europe-west4-a

# Enable required APIs
gcloud services enable container.googleapis.com
gcloud services enable compute.googleapis.com
gcloud services enable dns.googleapis.com
gcloud services enable certificatemanager.googleapis.com
```

### 2.2 Create GKE Cluster

```bash
# Create Autopilot cluster (recommended for simplicity)
gcloud container clusters create-auto open-webui-cluster \
  --region=europe-west4 \
  --release-channel=regular \
  --enable-master-authorized-networks \
  --master-authorized-networks=YOUR_IP/32 \
  --network=default \
  --subnetwork=default

# OR Standard cluster (more control)
gcloud container clusters create open-webui-cluster \
  --region=europe-west4 \
  --num-nodes=2 \
  --machine-type=e2-standard-4 \
  --disk-size=50GB \
  --enable-autoscaling \
  --min-nodes=1 \
  --max-nodes=5 \
  --enable-ip-alias \
  --workload-pool=YOUR_PROJECT_ID.svc.id.goog

# Get credentials
gcloud container clusters get-credentials open-webui-cluster --region=europe-west4
```

### 2.3 Create Static IP for Ingress

```bash
gcloud compute addresses create open-webui-ip \
  --global \
  --ip-version=IPV4

# Get the IP address
gcloud compute addresses describe open-webui-ip --global --format="get(address)"
```

---

## 3. Infrastructure Stack Installation

### 3.1 ingress-nginx

```bash
# Using Helm (recommended)
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update

helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx \
  --create-namespace \
  --set controller.service.loadBalancerIP=$(gcloud compute addresses describe open-webui-ip --global --format="get(address)") \
  --set controller.service.annotations."cloud\.google\.com/load-balancer-type"="External" \
  --set controller.metrics.enabled=true \
  --set controller.metrics.serviceMonitor.enabled=true

# Verify
kubectl get svc -n ingress-nginx
```

**Alternative: Traefik**
```bash
helm repo add traefik https://traefik.github.io/charts
helm repo update

helm install traefik traefik/traefik \
  --namespace traefik \
  --create-namespace \
  --set service.type=LoadBalancer \
  --set ports.web.redirectTo.port=websecure \
  --set providers.kubernetesIngress.enabled=true
```

### 3.2 cert-manager + Let's Encrypt

```bash
# Install cert-manager
helm repo add jetstack https://charts.jetstack.io
helm repo update

helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --create-namespace \
  --set crds.enabled=true \
  --set global.leaderElection.namespace=cert-manager

# Verify installation
kubectl get pods -n cert-manager
```

**Create ClusterIssuer for Let's Encrypt**:

```yaml
# cluster-issuer.yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    email: your-email@example.com
    server: https://acme-v02.api.letsencrypt.org/directory
    privateKeySecretRef:
      name: letsencrypt-prod-account-key
    solvers:
    - http01:
        ingress:
          class: nginx
---
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-staging
spec:
  acme:
    email: your-email@example.com
    server: https://acme-staging-v02.api.letsencrypt.org/directory
    privateKeySecretRef:
      name: letsencrypt-staging-account-key
    solvers:
    - http01:
        ingress:
          class: nginx
```

```bash
kubectl apply -f cluster-issuer.yaml
```

### 3.3 Prometheus + Grafana

```bash
# Install kube-prometheus-stack (includes Prometheus, Grafana, AlertManager)
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

helm install prometheus prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace \
  --set prometheus.prometheusSpec.retention=30d \
  --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.storageClassName=standard-rwo \
  --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.resources.requests.storage=50Gi \
  --set grafana.adminPassword=<your-password> \
  --set grafana.persistence.enabled=true \
  --set grafana.persistence.size=10Gi

# Access Grafana (port-forward for testing)
kubectl port-forward svc/prometheus-grafana -n monitoring 3000:80
```

### 3.4 Loki + Promtail

```bash
# Install Loki stack
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

helm install loki grafana/loki-stack \
  --namespace monitoring \
  --set loki.persistence.enabled=true \
  --set loki.persistence.size=50Gi \
  --set promtail.enabled=true \
  --set grafana.enabled=false  # Using existing Grafana from kube-prometheus-stack

# Add Loki as data source in Grafana
# URL: http://loki:3100
```

---

## 4. Kubernetes Manifests for Open WebUI

### 4.1 Namespace and Secrets

```yaml
# namespace.yaml
apiVersion: v1
kind: Namespace
metadata:
  name: open-webui
---
# secrets.yaml
apiVersion: v1
kind: Secret
metadata:
  name: open-webui-secrets
  namespace: open-webui
type: Opaque
stringData:
  WEBUI_SECRET_KEY: "<generate-with-openssl-rand-hex-32>"
  DATABASE_PASSWORD: "<secure-db-password>"
  OPENAI_API_KEY: "<your-openai-key>"  # if using OpenAI
```

```bash
# Generate secret key
openssl rand -hex 32
```

### 4.2 PostgreSQL StatefulSet

```yaml
# postgres.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: postgres-config
  namespace: open-webui
data:
  POSTGRES_DB: openwebui
  POSTGRES_USER: openwebui
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgres-pvc
  namespace: open-webui
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: standard-rwo
  resources:
    requests:
      storage: 20Gi
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
  namespace: open-webui
spec:
  serviceName: postgres
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
      - name: postgres
        image: postgres:16-alpine
        ports:
        - containerPort: 5432
        envFrom:
        - configMapRef:
            name: postgres-config
        env:
        - name: POSTGRES_PASSWORD
          valueFrom:
            secretKeyRef:
              name: open-webui-secrets
              key: DATABASE_PASSWORD
        volumeMounts:
        - name: postgres-storage
          mountPath: /var/lib/postgresql/data
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "2Gi"
            cpu: "1000m"
        livenessProbe:
          exec:
            command:
            - pg_isready
            - -U
            - openwebui
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          exec:
            command:
            - pg_isready
            - -U
            - openwebui
          initialDelaySeconds: 5
          periodSeconds: 5
      volumes:
      - name: postgres-storage
        persistentVolumeClaim:
          claimName: postgres-pvc
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: open-webui
spec:
  selector:
    app: postgres
  ports:
  - port: 5432
    targetPort: 5432
  clusterIP: None
```

### 4.3 SearXNG Deployment

```yaml
# searxng.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: searxng-config
  namespace: open-webui
data:
  settings.yml: |
    use_default_settings: true
    general:
      instance_name: "Open WebUI Search"
    search:
      safe_search: 0
      autocomplete: ""
      formats:
        - html
        - json
    server:
      secret_key: "change-this-secret"
      limiter: false
      public_instance: false
    ui:
      static_use_hash: true
    engines:
      - name: google
        engine: google
        disabled: false
      - name: duckduckgo
        engine: duckduckgo
        disabled: false
      - name: brave
        engine: brave
        disabled: false
      - name: wikipedia
        engine: wikipedia
        disabled: false
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: searxng
  namespace: open-webui
spec:
  replicas: 1
  selector:
    matchLabels:
      app: searxng
  template:
    metadata:
      labels:
        app: searxng
    spec:
      containers:
      - name: searxng
        image: searxng/searxng:latest
        ports:
        - containerPort: 8080
        volumeMounts:
        - name: config
          mountPath: /etc/searxng/settings.yml
          subPath: settings.yml
        resources:
          requests:
            memory: "256Mi"
            cpu: "100m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /healthz
            port: 8080
          initialDelaySeconds: 10
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /healthz
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 10
      volumes:
      - name: config
        configMap:
          name: searxng-config
---
apiVersion: v1
kind: Service
metadata:
  name: searxng
  namespace: open-webui
spec:
  selector:
    app: searxng
  ports:
  - port: 8080
    targetPort: 8080
```

### 4.4 Playwright Deployment (for Web Loader)

```yaml
# playwright.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: playwright
  namespace: open-webui
spec:
  replicas: 1
  selector:
    matchLabels:
      app: playwright
  template:
    metadata:
      labels:
        app: playwright
    spec:
      containers:
      - name: playwright
        image: mcr.microsoft.com/playwright:v1.57.0-noble
        command: ["npx", "-y", "playwright@1.57.0", "run-server", "--port", "3000", "--host", "0.0.0.0"]
        ports:
        - containerPort: 3000
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "2Gi"
            cpu: "1000m"
---
apiVersion: v1
kind: Service
metadata:
  name: playwright
  namespace: open-webui
spec:
  selector:
    app: playwright
  ports:
  - port: 3000
    targetPort: 3000
```

### 4.5 Open WebUI Deployment

```yaml
# open-webui.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: open-webui-config
  namespace: open-webui
data:
  ENV: "prod"
  WEBUI_AUTH: "True"
  DATABASE_TYPE: "postgresql"
  DATABASE_HOST: "postgres"
  DATABASE_PORT: "5432"
  DATABASE_NAME: "openwebui"
  DATABASE_USER: "openwebui"
  DATABASE_POOL_SIZE: "10"
  DATABASE_POOL_MAX_OVERFLOW: "20"
  # Web Search
  ENABLE_WEB_SEARCH: "True"
  WEB_SEARCH_ENGINE: "searxng"
  SEARXNG_QUERY_URL: "http://searxng:8080/search"
  WEB_LOADER_ENGINE: "playwright"
  PLAYWRIGHT_WS_URL: "ws://playwright:3000"
  # Features
  ENABLE_SIGNUP: "False"
  DEFAULT_USER_ROLE: "pending"
  # OpenTelemetry (for monitoring)
  ENABLE_OTEL: "True"
  ENABLE_OTEL_METRICS: "True"
  OTEL_EXPORTER_OTLP_ENDPOINT: "http://prometheus-kube-prometheus-prometheus.monitoring:9090"
  OTEL_SERVICE_NAME: "open-webui"
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: open-webui-pvc
  namespace: open-webui
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: standard-rwo
  resources:
    requests:
      storage: 50Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: open-webui
  namespace: open-webui
spec:
  replicas: 2
  selector:
    matchLabels:
      app: open-webui
  template:
    metadata:
      labels:
        app: open-webui
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8080"
        prometheus.io/path: "/metrics"
    spec:
      containers:
      - name: open-webui
        image: ghcr.io/open-webui/open-webui:main
        ports:
        - containerPort: 8080
        envFrom:
        - configMapRef:
            name: open-webui-config
        env:
        - name: WEBUI_SECRET_KEY
          valueFrom:
            secretKeyRef:
              name: open-webui-secrets
              key: WEBUI_SECRET_KEY
        - name: DATABASE_PASSWORD
          valueFrom:
            secretKeyRef:
              name: open-webui-secrets
              key: DATABASE_PASSWORD
        - name: OPENAI_API_KEY
          valueFrom:
            secretKeyRef:
              name: open-webui-secrets
              key: OPENAI_API_KEY
        volumeMounts:
        - name: data
          mountPath: /app/backend/data
        resources:
          requests:
            memory: "1Gi"
            cpu: "500m"
          limits:
            memory: "4Gi"
            cpu: "2000m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 60
          periodSeconds: 30
          timeoutSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 30
          periodSeconds: 10
          timeoutSeconds: 5
        startupProbe:
          httpGet:
            path: /health
            port: 8080
          failureThreshold: 30
          periodSeconds: 10
      volumes:
      - name: data
        persistentVolumeClaim:
          claimName: open-webui-pvc
---
apiVersion: v1
kind: Service
metadata:
  name: open-webui
  namespace: open-webui
spec:
  selector:
    app: open-webui
  ports:
  - port: 8080
    targetPort: 8080
```

### 4.6 Ingress with TLS

```yaml
# ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: open-webui-ingress
  namespace: open-webui
  annotations:
    kubernetes.io/ingress.class: nginx
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/proxy-body-size: "100m"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "600"
    # WebSocket support
    nginx.ingress.kubernetes.io/proxy-http-version: "1.1"
    nginx.ingress.kubernetes.io/upstream-hash-by: "$remote_addr"
spec:
  tls:
  - hosts:
    - your-domain.com
    secretName: open-webui-tls
  rules:
  - host: your-domain.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: open-webui
            port:
              number: 8080
```

---

## 5. Deployment Script (gcloud + kubectl)

```bash
#!/bin/bash
set -e

# Configuration
PROJECT_ID="your-project-id"
REGION="europe-west4"
CLUSTER_NAME="open-webui-cluster"
DOMAIN="your-domain.com"
EMAIL="your-email@example.com"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

echo -e "${GREEN}Starting Open WebUI GKE Deployment${NC}"

# 1. Set project
gcloud config set project $PROJECT_ID

# 2. Enable APIs
echo "Enabling required APIs..."
gcloud services enable container.googleapis.com compute.googleapis.com dns.googleapis.com

# 3. Create cluster (if not exists)
if ! gcloud container clusters describe $CLUSTER_NAME --region=$REGION &>/dev/null; then
    echo "Creating GKE cluster..."
    gcloud container clusters create-auto $CLUSTER_NAME \
        --region=$REGION \
        --release-channel=regular
fi

# 4. Get credentials
gcloud container clusters get-credentials $CLUSTER_NAME --region=$REGION

# 5. Create static IP
if ! gcloud compute addresses describe open-webui-ip --global &>/dev/null; then
    echo "Creating static IP..."
    gcloud compute addresses create open-webui-ip --global --ip-version=IPV4
fi
STATIC_IP=$(gcloud compute addresses describe open-webui-ip --global --format="get(address)")
echo "Static IP: $STATIC_IP"

# 6. Install ingress-nginx
echo "Installing ingress-nginx..."
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update
helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
    --namespace ingress-nginx --create-namespace \
    --set controller.service.loadBalancerIP=$STATIC_IP \
    --set controller.metrics.enabled=true \
    --wait

# 7. Install cert-manager
echo "Installing cert-manager..."
helm repo add jetstack https://charts.jetstack.io
helm upgrade --install cert-manager jetstack/cert-manager \
    --namespace cert-manager --create-namespace \
    --set crds.enabled=true \
    --wait

# 8. Install monitoring stack
echo "Installing Prometheus + Grafana..."
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm upgrade --install prometheus prometheus-community/kube-prometheus-stack \
    --namespace monitoring --create-namespace \
    --set prometheus.prometheusSpec.retention=30d \
    --set grafana.adminPassword="admin-change-me" \
    --wait

# 9. Install Loki
echo "Installing Loki + Promtail..."
helm repo add grafana https://grafana.github.io/helm-charts
helm upgrade --install loki grafana/loki-stack \
    --namespace monitoring \
    --set loki.persistence.enabled=true \
    --set promtail.enabled=true \
    --set grafana.enabled=false

# 10. Create namespace and secrets
echo "Creating Open WebUI namespace and secrets..."
kubectl create namespace open-webui --dry-run=client -o yaml | kubectl apply -f -

# Generate secret key
SECRET_KEY=$(openssl rand -hex 32)
DB_PASSWORD=$(openssl rand -base64 24)

kubectl create secret generic open-webui-secrets \
    --namespace open-webui \
    --from-literal=WEBUI_SECRET_KEY=$SECRET_KEY \
    --from-literal=DATABASE_PASSWORD=$DB_PASSWORD \
    --from-literal=OPENAI_API_KEY="${OPENAI_API_KEY:-placeholder}" \
    --dry-run=client -o yaml | kubectl apply -f -

# 11. Apply Kubernetes manifests
echo "Deploying Open WebUI stack..."
kubectl apply -f k8s/

# 12. Create ClusterIssuer
cat <<EOF | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    email: $EMAIL
    server: https://acme-v02.api.letsencrypt.org/directory
    privateKeySecretRef:
      name: letsencrypt-prod-account-key
    solvers:
    - http01:
        ingress:
          class: nginx
EOF

# 13. Wait for deployments
echo "Waiting for deployments to be ready..."
kubectl rollout status deployment/open-webui -n open-webui --timeout=300s
kubectl rollout status statefulset/postgres -n open-webui --timeout=300s

echo -e "${GREEN}Deployment complete!${NC}"
echo "Static IP: $STATIC_IP"
echo "Point your DNS ($DOMAIN) to this IP"
echo "Access Grafana: kubectl port-forward svc/prometheus-grafana -n monitoring 3000:80"
```

---

## 6. DNS Configuration

```bash
# Option 1: Using Cloud DNS
gcloud dns managed-zones create open-webui-zone \
    --dns-name="your-domain.com." \
    --description="Open WebUI DNS zone"

gcloud dns record-sets create your-domain.com. \
    --zone=open-webui-zone \
    --type=A \
    --ttl=300 \
    --rrdatas=$(gcloud compute addresses describe open-webui-ip --global --format="get(address)")

# Option 2: Manual DNS (at your registrar)
# Create A record: your-domain.com -> <static-ip>
```

---

## 7. Complexity and Time Estimates

### Phase 1: Infrastructure Setup
| Task | Complexity | Estimated Time |
|------|------------|----------------|
| GKE Cluster creation | Low | 30 min |
| ingress-nginx installation | Low | 15 min |
| cert-manager + ClusterIssuer | Low | 20 min |
| Prometheus + Grafana | Medium | 45 min |
| Loki + Promtail | Medium | 30 min |
| **Subtotal** | | **~2.5 hours** |

### Phase 2: Application Deployment
| Task | Complexity | Estimated Time |
|------|------------|----------------|
| Write Kubernetes manifests | Medium | 2-3 hours |
| PostgreSQL StatefulSet | Medium | 45 min |
| SearXNG configuration | Low | 30 min |
| Playwright deployment | Low | 15 min |
| Open WebUI deployment | Medium | 1 hour |
| Ingress + TLS setup | Medium | 45 min |
| DNS configuration | Low | 15 min |
| **Subtotal** | | **~5-6 hours** |

### Phase 3: Testing & Hardening
| Task | Complexity | Estimated Time |
|------|------------|----------------|
| End-to-end testing | Medium | 2 hours |
| Grafana dashboards | Medium | 2 hours |
| Alert rules | Medium | 1-2 hours |
| Resource tuning | Medium | 1-2 hours |
| Security review | Medium | 1-2 hours |
| Documentation | Low | 1-2 hours |
| **Subtotal** | | **~8-12 hours** |

### Total Estimates

| Scenario | Time |
|----------|------|
| **Minimal viable deployment** | 1 day (8 hours) |
| **Production-ready deployment** | 3-4 days |
| **With monitoring dashboards + alerts** | 4-5 days |
| **With high availability + DR** | 5-7 days |

### Risk Factors
- **First-time GKE users**: Add 50% time buffer
- **Complex network requirements**: Add 1-2 days
- **GPU support for Ollama**: Add 1 day
- **Multi-region deployment**: Add 2-3 days

---

## 8. File Structure for Manifests

```
k8s/
├── namespace.yaml
├── secrets.yaml              # Store separately, gitignore
├── configmaps/
│   ├── open-webui-config.yaml
│   └── searxng-config.yaml
├── storage/
│   ├── postgres-pvc.yaml
│   └── open-webui-pvc.yaml
├── deployments/
│   ├── postgres.yaml
│   ├── searxng.yaml
│   ├── playwright.yaml
│   └── open-webui.yaml
├── services/
│   ├── postgres-svc.yaml
│   ├── searxng-svc.yaml
│   ├── playwright-svc.yaml
│   └── open-webui-svc.yaml
├── ingress/
│   ├── cluster-issuer.yaml
│   └── ingress.yaml
└── monitoring/
    ├── servicemonitor.yaml
    └── dashboards/
        └── open-webui-dashboard.json
```

---

## 9. Optional Enhancements

### Redis for Multi-Instance Session Storage

```yaml
# redis.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis
  namespace: open-webui
spec:
  replicas: 1
  selector:
    matchLabels:
      app: redis
  template:
    metadata:
      labels:
        app: redis
    spec:
      containers:
      - name: redis
        image: redis:7-alpine
        ports:
        - containerPort: 6379
        command: ["redis-server", "--appendonly", "yes"]
---
# Add to open-webui config:
# REDIS_URL: "redis://redis:6379"
# WEBSOCKET_REDIS_URL: "redis://redis:6379"
```

### HorizontalPodAutoscaler

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: open-webui-hpa
  namespace: open-webui
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: open-webui
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
```

### PodDisruptionBudget

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: open-webui-pdb
  namespace: open-webui
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: open-webui
```

---

## 10. Monitoring & Observability

### ServiceMonitor for Prometheus

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: open-webui
  namespace: monitoring
  labels:
    release: prometheus
spec:
  selector:
    matchLabels:
      app: open-webui
  namespaceSelector:
    matchNames:
    - open-webui
  endpoints:
  - port: http
    path: /metrics
    interval: 30s
```

### Grafana Dashboard Queries

```promql
# Request rate
rate(http_request_total{service="open-webui"}[5m])

# Response time p95
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{service="open-webui"}[5m]))

# Active WebSocket connections
open_webui_websocket_connections_active

# Chat completions per minute
rate(open_webui_chat_completions_total[1m]) * 60
```

---

## Code References

- `Dockerfile:1-194` - Container configuration and health checks
- `backend/open_webui/env.py:257-329` - Database configuration
- `backend/open_webui/config.py:2959-3302` - Web search configuration
- `backend/open_webui/config.py:923-951` - Storage provider configuration
- `backend/open_webui/main.py:2348-2356` - Health endpoints
- `docker-compose.websearch.yaml:1-72` - Web search stack reference
- `docker-compose.staging.yaml:1-80` - PostgreSQL configuration reference

## Related Research

- External Helm charts: https://github.com/open-webui/helm-charts
- Open WebUI Documentation: https://docs.openwebui.com/

## Open Questions

1. **GPU Support**: If running Ollama locally in GKE, need to add GPU node pool and NVIDIA device plugin
2. **Multi-region**: For HA across regions, need to consider database replication and global load balancing
3. **Cost Optimization**: Autopilot vs Standard cluster pricing, spot instances for non-critical workloads
4. **Backup Strategy**: PostgreSQL backup (Cloud SQL option) and PVC snapshots
