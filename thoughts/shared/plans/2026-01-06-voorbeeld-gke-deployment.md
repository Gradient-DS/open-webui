# Open WebUI GKE Deployment Plan - voorbeeld.soev.ai

## Overview

Deploy Open WebUI on GKE (Google Kubernetes Engine) with PostgreSQL, SearXNG web search, and Playwright for a test environment at `voorbeeld.soev.ai`.

## Configuration Summary

| Setting | Value |
|---------|-------|
| GCP Project ID | `soev-ai-001` |
| Domain | `voorbeeld.soev.ai` |
| GCP Region | `europe-west4` (Netherlands) |
| GKE Mode | Standard (zonal) |
| Node Type | `e2-standard-2` (2 vCPU, 8 GB RAM) |
| Node Count | 1 (minimal test environment) |
| Estimated Cost | ~$76/month |
| LLM Provider | OpenAI-compatible endpoint (configure post-deploy) |
| DNS Provider | Strato (manual A record) |
| Let's Encrypt Email | `lex@gradient-ds.com` |

## Current State

- GCP project exists
- Domain `soev.ai` managed in Strato
- No existing Kubernetes infrastructure
- Research completed: `thoughts/shared/research/2026-01-06-gke-deployment-open-source-stack.md`

## Desired End State

- Open WebUI accessible at `https://voorbeeld.soev.ai`
- PostgreSQL database with persistent storage
- SearXNG web search enabled
- Playwright for JavaScript rendering
- TLS certificate via Let's Encrypt (auto-renewed)
- Basic health monitoring via GKE console

## What We're NOT Doing

- Full monitoring stack (Prometheus/Grafana/Loki) - can add later
- High availability (single node, single replica)
- GPU support / local Ollama
- Multi-region deployment
- Automated backups (manual for now)
- Redis for session storage (not needed for single replica)

---

## Phase 1: GCP Project Setup & Prerequisites

### Overview
Prepare the GCP project with required APIs and tools.

### Prerequisites (on your local machine)
- Google Cloud SDK (`gcloud`) installed
- `kubectl` installed
- `helm` installed (v3+)
- `gke-gcloud-auth-plugin` installed (required for kubectl auth):
  ```bash
  gcloud components install gke-gcloud-auth-plugin
  # Ensure PATH includes: /opt/homebrew/share/google-cloud-sdk/bin (macOS)
  ```

### Steps

#### 1.1 Authenticate and set project
```bash
# Login to GCP
gcloud auth login

# Set your project
gcloud config set project soev-ai-001

# Set default region/zone
gcloud config set compute/region europe-west4
gcloud config set compute/zone europe-west4-a
```

#### 1.2 Enable required APIs
```bash
gcloud services enable container.googleapis.com
gcloud services enable compute.googleapis.com
gcloud services enable dns.googleapis.com
```

#### 1.3 Enable OS Login (required by org policy)
```bash
gcloud compute project-info add-metadata --project=soev-ai-001 --metadata=enable-oslogin=TRUE
```

#### 1.4 Verify configuration
```bash
# Should show your project and region
gcloud config list
```

### Success Criteria

#### Automated Verification:
```bash
# All should return enabled
gcloud services list --enabled --filter="name:container.googleapis.com"
gcloud services list --enabled --filter="name:compute.googleapis.com"
```

#### Manual Verification:
- [ ] `gcloud config list` shows correct project ID
- [ ] `gcloud config list` shows `europe-west4` as region

**⏸️ CHECKPOINT: Confirm prerequisites are ready before proceeding.**

---

## Phase 2: Create GKE Cluster

### Overview
Create a minimal GKE Standard cluster with one node and dual-stack (IPv4+IPv6) support.

### Steps

#### 2.1 Create custom VPC for dual-stack
```bash
gcloud compute networks create demo-vpc --subnet-mode=custom
```

#### 2.2 Create the cluster
```bash
gcloud container clusters create demo-cluster \
  --zone=europe-west4-a \
  --num-nodes=1 \
  --machine-type=e2-standard-2 \
  --disk-size=50GB \
  --enable-ip-alias \
  --enable-dataplane-v2 \
  --stack-type=ipv4-ipv6 \
  --ipv6-access-type=external \
  --network=demo-vpc \
  --create-subnetwork name=demo-subnet \
  --no-enable-autoscaling \
  --release-channel=regular
```

Note: Using zonal cluster (single zone) to qualify for free tier management fee. Dual-stack requires Dataplane V2 and a custom VPC.

#### 2.3 Get cluster credentials
```bash
gcloud container clusters get-credentials demo-cluster --zone=europe-west4-a
```

#### 2.4 Verify cluster access
```bash
kubectl get nodes
kubectl cluster-info
```

### Success Criteria

#### Automated Verification:
```bash
# Should show 1 node in Ready state
kubectl get nodes

# Should show cluster endpoint
kubectl cluster-info
```

#### Manual Verification:
- [ ] GKE Console shows cluster "demo-cluster" as healthy
- [ ] Node shows status "Ready" with correct machine type
- [ ] `kubectl get nodes` returns without authentication errors

**⏸️ CHECKPOINT: Confirm cluster is healthy before proceeding.**

---

## Phase 3: Create Static IP & Install Ingress Controller

### Overview
Reserve a static IP and install ingress-nginx for routing external traffic.

### Steps

#### 3.1 Reserve a static external IP
```bash
gcloud compute addresses create demo-ip \
  --region=europe-west4

# Get the IP address (note this down for DNS)
gcloud compute addresses describe demo-ip --region=europe-west4 --format="get(address)"
```

#### 3.2 Add Helm repositories
```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo add jetstack https://charts.jetstack.io
helm repo update
```

#### 3.3 Install ingress-nginx with dual-stack
```bash
# Get the static IPv4 into a variable
STATIC_IP=$(gcloud compute addresses describe demo-ip --region=europe-west4 --format="get(address)")

helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx \
  --create-namespace \
  --set controller.service.loadBalancerIP=$STATIC_IP \
  --set controller.service.externalTrafficPolicy=Local \
  --set controller.service.ipFamilyPolicy=PreferDualStack \
  --set controller.service.ipFamilies="{IPv4,IPv6}"
```

Note: The IPv6 address will be automatically assigned from the subnet's external IPv6 prefix.

#### 3.4 Wait for ingress controller to be ready
```bash
kubectl wait --namespace ingress-nginx \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller \
  --timeout=120s
```

### Success Criteria

#### Automated Verification:
```bash
# Should show the static IPv4
gcloud compute addresses describe demo-ip --region=europe-west4 --format="get(address)"

# Should show ingress-nginx pods running
kubectl get pods -n ingress-nginx

# Should show LoadBalancer with both IPv4 and IPv6 EXTERNAL-IPs
kubectl get svc -n ingress-nginx
kubectl get svc -n ingress-nginx ingress-nginx-controller -o jsonpath='{.status.loadBalancer.ingress}'
```

#### Manual Verification:
- [ ] Static IPv4 is reserved (note down: `____.____.____.____`)
- [ ] IPv6 is assigned (note down: `____:____:____:____:____:____:____:____`)
- [ ] ingress-nginx-controller pod is Running
- [ ] LoadBalancer service shows both IPv4 and IPv6 EXTERNAL-IPs

**⏸️ CHECKPOINT: Note down both IPs for DNS configuration in Phase 10 (A record for IPv4, AAAA record for IPv6).**

---

## Phase 4: Install cert-manager for TLS

### Overview
Install cert-manager and configure Let's Encrypt for automatic TLS certificates.

### Steps

#### 4.1 Install cert-manager
```bash
helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager \
  --create-namespace \
  --set crds.enabled=true
```

#### 4.2 Wait for cert-manager to be ready
```bash
kubectl wait --namespace cert-manager \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/instance=cert-manager \
  --timeout=120s
```

#### 4.3 Create ClusterIssuer for Let's Encrypt

Create file `k8s/cluster-issuer.yaml`:
```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    email: lex@gradient-ds.com
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
    email: lex@gradient-ds.com
    server: https://acme-staging-v02.api.letsencrypt.org/directory
    privateKeySecretRef:
      name: letsencrypt-staging-account-key
    solvers:
    - http01:
        ingress:
          class: nginx
```

```bash
kubectl apply -f k8s/cluster-issuer.yaml
```

### Success Criteria

#### Automated Verification:
```bash
# Should show 3 pods running
kubectl get pods -n cert-manager

# Should show both issuers
kubectl get clusterissuers
```

#### Manual Verification:
- [ ] cert-manager pods are all Running (cert-manager, cainjector, webhook)
- [ ] ClusterIssuers show Ready=True

**⏸️ CHECKPOINT: Confirm cert-manager is ready before proceeding.**

---

## Phase 5: Create Namespace and Secrets

### Overview
Create the open-webui namespace and configure secrets for sensitive data.

### Steps

#### 5.1 Create namespace
```bash
kubectl create namespace open-webui
```

#### 5.2 Generate secrets
```bash
# Generate random keys
WEBUI_SECRET=$(openssl rand -hex 32)
DB_PASSWORD=$(openssl rand -base64 24 | tr -d '=/+')

# Display them (save these somewhere secure!)
echo "WEBUI_SECRET_KEY: $WEBUI_SECRET"
echo "DATABASE_PASSWORD: $DB_PASSWORD"
```

#### 5.3 Create Kubernetes secret
```bash
kubectl create secret generic open-webui-secrets \
  --namespace open-webui \
  --from-literal=WEBUI_SECRET_KEY=$WEBUI_SECRET \
  --from-literal=DATABASE_PASSWORD=$DB_PASSWORD \
  --from-literal=OPENAI_API_KEY="placeholder"
```

**Note**: The OpenAI API key is set to "placeholder" for now. You'll configure the actual API endpoint through the Open WebUI admin interface after deployment.

### Success Criteria

#### Automated Verification:
```bash
# Should show namespace
kubectl get namespace open-webui

# Should show secret exists
kubectl get secret open-webui-secrets -n open-webui
```

#### Manual Verification:
- [ ] WEBUI_SECRET_KEY and DATABASE_PASSWORD saved securely (password manager, etc.)

**⏸️ CHECKPOINT: Confirm secrets are created before proceeding.**

---

## Phase 6: Deploy PostgreSQL

### Overview
Deploy PostgreSQL with persistent storage for Open WebUI data.

### Steps

#### 6.1 Create PostgreSQL manifests

Create file `k8s/postgres.yaml`:
```yaml
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
      storage: 10Gi
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
        - name: PGDATA
          value: /var/lib/postgresql/data/pgdata
        volumeMounts:
        - name: postgres-storage
          mountPath: /var/lib/postgresql/data
        resources:
          requests:
            memory: "256Mi"
            cpu: "100m"
          limits:
            memory: "512Mi"
            cpu: "500m"
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

#### 6.2 Apply PostgreSQL manifests
```bash
kubectl apply -f k8s/postgres.yaml
```

#### 6.3 Wait for PostgreSQL to be ready
```bash
kubectl wait --namespace open-webui \
  --for=condition=ready pod \
  --selector=app=postgres \
  --timeout=180s
```

### Success Criteria

#### Automated Verification:
```bash
# Should show postgres pod Running
kubectl get pods -n open-webui -l app=postgres

# Should show PVC Bound
kubectl get pvc -n open-webui

# Test database connection
kubectl exec -n open-webui postgres-0 -- pg_isready -U openwebui
```

#### Manual Verification:
- [ ] PostgreSQL pod is Running
- [ ] PVC shows status "Bound"
- [ ] `pg_isready` returns "accepting connections"

**⏸️ CHECKPOINT: Confirm PostgreSQL is healthy before proceeding.**

---

## Phase 7: Deploy Supporting Services (SearXNG & Playwright)

### Overview
Deploy SearXNG for web search and Playwright for JavaScript rendering.

### Steps

#### 7.1 Create SearXNG manifests

Create file `k8s/searxng.yaml`:
```yaml
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
      secret_key: "change-this-to-random-string"
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
            memory: "128Mi"
            cpu: "50m"
          limits:
            memory: "256Mi"
            cpu: "200m"
        readinessProbe:
          httpGet:
            path: /healthz
            port: 8080
          initialDelaySeconds: 10
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

#### 7.2 Create Playwright manifests

Create file `k8s/playwright.yaml`:
```yaml
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
        image: mcr.microsoft.com/playwright:v1.49.0-noble
        command: ["npx", "-y", "playwright@1.49.0", "run-server", "--port", "3000", "--host", "0.0.0.0"]
        ports:
        - containerPort: 3000
        resources:
          requests:
            memory: "256Mi"
            cpu: "100m"
          limits:
            memory: "1Gi"
            cpu: "500m"
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

#### 7.3 Apply manifests
```bash
kubectl apply -f k8s/searxng.yaml
kubectl apply -f k8s/playwright.yaml
```

#### 7.4 Wait for pods to be ready
```bash
kubectl wait --namespace open-webui \
  --for=condition=ready pod \
  --selector=app=searxng \
  --timeout=120s

kubectl wait --namespace open-webui \
  --for=condition=ready pod \
  --selector=app=playwright \
  --timeout=120s
```

### Success Criteria

#### Automated Verification:
```bash
# Should show both pods Running
kubectl get pods -n open-webui -l app=searxng
kubectl get pods -n open-webui -l app=playwright

# Test SearXNG health
kubectl exec -n open-webui deployment/searxng -- wget -q -O- http://localhost:8080/healthz
```

#### Manual Verification:
- [ ] SearXNG pod is Running
- [ ] Playwright pod is Running
- [ ] SearXNG healthz returns OK

**⏸️ CHECKPOINT: Confirm supporting services are healthy before proceeding.**

---

## Phase 8: Deploy Open WebUI

### Overview
Deploy the main Open WebUI application.

### Steps

#### 8.1 Create Open WebUI manifests

Create file `k8s/open-webui.yaml`:
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: open-webui-config
  namespace: open-webui
data:
  ENV: "prod"
  WEBUI_AUTH: "True"
  # Database
  DATABASE_URL: "postgresql://openwebui:$(DATABASE_PASSWORD)@postgres:5432/openwebui"
  # Web Search
  ENABLE_RAG_WEB_SEARCH: "True"
  RAG_WEB_SEARCH_ENGINE: "searxng"
  SEARXNG_QUERY_URL: "http://searxng:8080/search?q=<query>"
  RAG_WEB_SEARCH_RESULT_COUNT: "5"
  # Web Loader (Playwright for JS rendering)
  RAG_WEB_LOADER_URL_BLACKLIST: ""
  # Features
  ENABLE_SIGNUP: "True"
  DEFAULT_USER_ROLE: "user"
  # OpenAI-compatible endpoint (configure your actual endpoint)
  OPENAI_API_BASE_URL: "https://api.openai.com/v1"
  # Disable Ollama since we're using OpenAI-compatible API
  ENABLE_OLLAMA_API: "False"
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
      storage: 10Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: open-webui
  namespace: open-webui
spec:
  replicas: 1
  selector:
    matchLabels:
      app: open-webui
  template:
    metadata:
      labels:
        app: open-webui
    spec:
      containers:
      - name: open-webui
        image: ghcr.io/gradient-ds/open-webui:main
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
        - name: DATABASE_URL
          value: "postgresql://openwebui:$(DATABASE_PASSWORD)@postgres:5432/openwebui"
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
            memory: "512Mi"
            cpu: "200m"
          limits:
            memory: "2Gi"
            cpu: "1000m"
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

#### 8.2 Apply Open WebUI manifests
```bash
kubectl apply -f k8s/open-webui.yaml
```

#### 8.3 Wait for Open WebUI to be ready (may take 2-3 minutes on first start)
```bash
kubectl wait --namespace open-webui \
  --for=condition=ready pod \
  --selector=app=open-webui \
  --timeout=300s
```

#### 8.4 Check logs if needed
```bash
kubectl logs -n open-webui -l app=open-webui --tail=50
```

### Success Criteria

#### Automated Verification:
```bash
# Should show pod Running
kubectl get pods -n open-webui -l app=open-webui

# Should show PVC Bound
kubectl get pvc -n open-webui

# Test health endpoint
kubectl exec -n open-webui deployment/open-webui -- wget -q -O- http://localhost:8080/health
```

#### Manual Verification:
- [x] Open WebUI pod is Running
- [x] Health endpoint returns OK (returns `{"status":true}`)
- [x] No error logs in `kubectl logs`

**⏸️ CHECKPOINT: Confirm Open WebUI is healthy before proceeding.**

---

## Phase 9: Configure Ingress & TLS

### Overview
Create the Ingress resource to expose Open WebUI with automatic TLS.

### Steps

#### 9.1 Create Ingress manifest

Create file `k8s/ingress.yaml`:
```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: open-webui-ingress
  namespace: open-webui
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/proxy-body-size: "100m"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "600"
    nginx.ingress.kubernetes.io/proxy-http-version: "1.1"
    nginx.ingress.kubernetes.io/proxy-buffering: "off"
spec:
  ingressClassName: nginx
  tls:
  - hosts:
    - voorbeeld.soev.ai
    secretName: open-webui-tls
  rules:
  - host: voorbeeld.soev.ai
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

#### 9.2 Apply Ingress
```bash
kubectl apply -f k8s/ingress.yaml
```

#### 9.3 Check certificate status
```bash
# Wait a moment for cert-manager to process
sleep 30

# Check certificate status
kubectl get certificate -n open-webui
kubectl describe certificate open-webui-tls -n open-webui
```

### Success Criteria

#### Automated Verification:
```bash
# Should show ingress with ADDRESS
kubectl get ingress -n open-webui

# Should show certificate (may be pending until DNS is configured)
kubectl get certificate -n open-webui
```

#### Manual Verification:
- [x] Ingress shows the static IP as ADDRESS (34.34.42.221 + IPv6)
- [x] Certificate resource exists (Ready=True, DNS was pre-configured)

**⏸️ CHECKPOINT: Note the ingress IP matches your static IP before DNS configuration.**

---

## Phase 10: Configure DNS

### Overview
Point `voorbeeld.soev.ai` to the GKE static IPs (IPv4 + IPv6) in Strato.

### Steps

#### 10.1 Get your static IPs
```bash
# IPv4
gcloud compute addresses describe demo-ip --region=europe-west4 --format="get(address)"

# IPv6 (from LoadBalancer)
kubectl get svc -n ingress-nginx ingress-nginx-controller -o jsonpath='{.status.loadBalancer.ingress[1].ip}'
```

#### 10.2 Configure DNS in Strato

1. Log in to Strato admin panel
2. Navigate to DNS settings for `soev.ai`
3. Create an **A record** (IPv4):
   - **Name**: `voorbeeld`
   - **Type**: `A`
   - **Value**: `<your-ipv4-address>`
   - **TTL**: `300` (5 minutes, for faster propagation during testing)
4. Create an **AAAA record** (IPv6):
   - **Name**: `voorbeeld`
   - **Type**: `AAAA`
   - **Value**: `<your-ipv6-address>`
   - **TTL**: `300`

#### 10.3 Verify DNS propagation
```bash
# May take 5-30 minutes for DNS to propagate
# Check IPv4
dig voorbeeld.soev.ai A +short
# Check IPv6
dig voorbeeld.soev.ai AAAA +short
```

#### 10.4 Wait for TLS certificate
Once DNS is working, cert-manager will automatically obtain the certificate:
```bash
# Watch certificate status
kubectl get certificate -n open-webui -w

# Or check certificate details
kubectl describe certificate open-webui-tls -n open-webui
```

### Success Criteria

#### Automated Verification:
```bash
# Should resolve to your static IPs
dig voorbeeld.soev.ai A +short
dig voorbeeld.soev.ai AAAA +short

# Should show Ready=True
kubectl get certificate -n open-webui
```

#### Manual Verification:
- [x] `dig voorbeeld.soev.ai A +short` returns the correct IPv4 (34.34.42.221)
- [x] `dig voorbeeld.soev.ai AAAA +short` returns the correct IPv6 (2600:1900:4060:116d:8000::)
- [x] Certificate shows `Ready: True`
- [ ] `https://voorbeeld.soev.ai` loads without certificate errors (via IPv4 and IPv6)

**⏸️ CHECKPOINT: Confirm DNS and TLS are working before final verification.**

---

## Phase 11: Final Verification & First User Setup

### Overview
Verify the complete deployment and create the admin user.

### Steps

#### 11.1 Access Open WebUI
Open `https://voorbeeld.soev.ai` in your browser.

#### 11.2 Create admin account
1. Click "Sign up" (first user becomes admin)
2. Enter your email and password
3. Log in with the new account

#### 11.3 Configure LLM connection
1. Go to **Admin Panel** → **Settings** → **Connections**
2. Add your OpenAI-compatible API endpoint and key
3. Test the connection by selecting a model

#### 11.4 Test web search
1. Start a new chat
2. Ask a question that requires web search (e.g., "What's the current weather in Amsterdam?")
3. Verify web search results are included

#### 11.5 Check all pods are healthy
```bash
kubectl get pods -n open-webui
kubectl get pods -n ingress-nginx
kubectl get pods -n cert-manager
```

### Success Criteria

#### Automated Verification:
```bash
# All pods should be Running
kubectl get pods -A | grep -E "(open-webui|ingress-nginx|cert-manager)"

# Test HTTPS endpoint
curl -s -o /dev/null -w "%{http_code}" https://voorbeeld.soev.ai/health
# Should return 200
```

#### Manual Verification:
- [ ] `https://voorbeeld.soev.ai` loads with valid TLS certificate
- [ ] Can create admin account
- [ ] Can send a chat message and receive a response
- [ ] Web search works (shows search results in response)
- [ ] No certificate warnings in browser

---

## Troubleshooting

### Certificate not issuing
```bash
# Check cert-manager logs
kubectl logs -n cert-manager -l app=cert-manager --tail=100

# Check certificate events
kubectl describe certificate open-webui-tls -n open-webui

# Check challenges
kubectl get challenges -n open-webui
```

### Open WebUI not starting
```bash
# Check pod logs
kubectl logs -n open-webui -l app=open-webui --tail=100

# Check events
kubectl describe pod -n open-webui -l app=open-webui
```

### Database connection issues
```bash
# Check PostgreSQL is running
kubectl get pods -n open-webui -l app=postgres

# Check PostgreSQL logs
kubectl logs -n open-webui postgres-0 --tail=50

# Test connection from Open WebUI pod
kubectl exec -n open-webui deployment/open-webui -- \
  python -c "import psycopg2; print(psycopg2.connect('postgresql://openwebui:$DATABASE_PASSWORD@postgres:5432/openwebui'))"
```

### Ingress not getting IP
```bash
# Check ingress-nginx logs
kubectl logs -n ingress-nginx -l app.kubernetes.io/component=controller --tail=100

# Check LoadBalancer service
kubectl get svc -n ingress-nginx
```

---

## File Structure

After completing this plan, you'll have:
```
k8s/
├── cluster-issuer.yaml      # Let's Encrypt ClusterIssuers
├── postgres.yaml            # PostgreSQL StatefulSet + Service
├── searxng.yaml             # SearXNG Deployment + Service
├── playwright.yaml          # Playwright Deployment + Service
├── open-webui.yaml          # Open WebUI Deployment + Service
└── ingress.yaml             # Ingress with TLS
```

---

## Cleanup (if needed)

To remove the entire deployment:
```bash
# Delete application resources
kubectl delete namespace open-webui

# Delete cluster
gcloud container clusters delete demo-cluster --zone=europe-west4-a

# Delete static IP
gcloud compute addresses delete demo-ip --region=europe-west4

# Delete custom VPC (will also delete subnet)
gcloud compute networks delete demo-vpc --quiet

# Remove DNS records in Strato (A and AAAA)
```

---

## Future Enhancements (Out of Scope)

- [ ] Add Prometheus + Grafana monitoring
- [ ] Configure PostgreSQL backups
- [ ] Add HorizontalPodAutoscaler
- [ ] Multi-replica deployment with Redis
- [ ] Add network policies for security
- [ ] Configure pod security standards
- [ ] Add RabbitMQ + KEDA for event-driven autoscaling (scale workers based on queue depth, scale-to-zero)

---

## References

- Research document: `thoughts/shared/research/2026-01-06-gke-deployment-open-source-stack.md`
- Open WebUI Docker config: `docker-compose.yaml`
- Open WebUI environment: `backend/open_webui/config.py`
