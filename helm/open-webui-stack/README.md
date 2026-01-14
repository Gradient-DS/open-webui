# Open WebUI Stack Helm Chart

A unified Helm chart for deploying Open WebUI with Gradient services for production.

## Components

| Service | Image | Description |
|---------|-------|-------------|
| Open WebUI | `ghcr.io/gradient-ds/open-webui` | Main chat application |
| PostgreSQL | `postgres:16-alpine` | Application database |
| Weaviate | `semitechnologies/weaviate` | Vector database for RAG |
| Gradient Gateway | `ghcr.io/gradient-ds/gradient-gateway` | Facade for search + document processing |
| SearXNG | `searxng/searxng` | Meta search engine |
| Crawl4AI | `unclecode/crawl4ai` | Web content extraction |
| Reranker | `ghcr.io/gradient-ds/reranker` | Semantic reranking service |

## Architecture

```
                          ┌─────────────┐
                          │   Ingress   │
                          └──────┬──────┘
                                 │
                          ┌──────▼──────┐
                          │  Open WebUI │
                          └──────┬──────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        │                        │                        │
  ┌─────▼─────┐           ┌──────▼──────┐          ┌──────▼──────┐
  │ PostgreSQL│           │  Weaviate   │          │   Gateway   │
  └───────────┘           └─────────────┘          └──────┬──────┘
                                                          │
                                              ┌───────────┼───────────┐
                                              │           │           │
                                        ┌─────▼─────┐ ┌───▼───┐ ┌─────▼─────┐
                                        │  SearXNG  │ │Crawl4AI│ │ Reranker  │
                                        └───────────┘ └───────┘ └───────────┘
```

## Prerequisites

1. Kubernetes cluster (1.24+)
2. Helm 3.x
3. kubectl configured

## Installation

### Quick Start

```bash
# Add namespace
kubectl create namespace open-webui

# Install with default values
helm install open-webui ./helm/open-webui-stack -n open-webui
```

### Production Installation

```bash
# Create secrets file (values-secrets.yaml)
cat <<EOF > values-secrets.yaml
secrets:
  webuiSecretKey: "$(openssl rand -hex 32)"
  adminPassword: "your-secure-password"
  openaiApiKey: "your-openai-key"
  ragOpenaiApiKey: "your-rag-openai-key"
  postgresPassword: "$(openssl rand -hex 32)"
  searxngSecretKey: "$(openssl rand -hex 16)"
EOF

# Create production values (values-prod.yaml)
cat <<EOF > values-prod.yaml
openWebui:
  config:
    adminEmail: "admin@example.com"
    adminName: "Admin"
    webuiName: "Your Company AI"
    corsAllowOrigin: "https://ai.example.com"

ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: ai.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: open-webui-tls
      hosts:
        - ai.example.com
EOF

# Install
helm install open-webui ./helm/open-webui-stack \
  -n open-webui \
  -f values-prod.yaml \
  -f values-secrets.yaml
```

## Configuration

### Open WebUI

| Parameter | Description | Default |
|-----------|-------------|---------|
| `openWebui.enabled` | Enable Open WebUI | `true` |
| `openWebui.image.repository` | Image repository | `ghcr.io/gradient-ds/open-webui` |
| `openWebui.image.tag` | Image tag | `main` |
| `openWebui.config.webuiName` | Application name | `soev.ai` |
| `openWebui.config.defaultLocale` | Default language | `nl-NL` |
| `openWebui.persistence.size` | Storage size | `10Gi` |

### Gateway

| Parameter | Description | Default |
|-----------|-------------|---------|
| `gateway.enabled` | Enable Gateway | `true` |
| `gateway.image.repository` | Image repository | `ghcr.io/gradient-ds/gradient-gateway` |
| `gateway.image.tag` | Image tag | `latest` |

### Reranker

| Parameter | Description | Default |
|-----------|-------------|---------|
| `reranker.enabled` | Enable Reranker | `true` |
| `reranker.image.repository` | Image repository | `ghcr.io/gradient-ds/reranker` |
| `reranker.config.model` | Reranker model | `cross-encoder/ms-marco-MiniLM-L6-v2` |

### Secrets

| Parameter | Description | Required |
|-----------|-------------|----------|
| `secrets.webuiSecretKey` | Session encryption key | Yes (auto-generated if empty) |
| `secrets.adminPassword` | Admin password | Yes |
| `secrets.openaiApiKey` | OpenAI API key | Yes |
| `secrets.ragOpenaiApiKey` | RAG embedding API key | Yes |
| `secrets.postgresPassword` | PostgreSQL password | Yes (auto-generated if empty) |

## Upgrade

```bash
helm upgrade open-webui ./helm/open-webui-stack \
  -n open-webui \
  -f values-prod.yaml \
  -f values-secrets.yaml
```

## Uninstall

```bash
helm uninstall open-webui -n open-webui

# Optional: delete PVCs
kubectl delete pvc -l app.kubernetes.io/instance=open-webui -n open-webui
```

## Images That Need CI/CD

The following images are built from this monorepo and need GitHub Actions workflows:

| Image | Source | Workflow |
|-------|--------|----------|
| `ghcr.io/gradient-ds/open-webui` | `open-webui/` | `open-webui/.github/workflows/docker-build-soev.yaml` |
| `ghcr.io/gradient-ds/gradient-gateway` | `genai-utils/api/Dockerfile.gateway` | `genai-utils/.github/workflows/build-services.yml` |
| `ghcr.io/gradient-ds/reranker` | `genai-utils/services/reranker/Dockerfile` | `genai-utils/.github/workflows/build-services.yml` |

Official images (no build needed):
- `searxng/searxng:latest`
- `unclecode/crawl4ai:latest`
- `postgres:16-alpine`
- `semitechnologies/weaviate:1.28.4`

## Troubleshooting

### Check pod status
```bash
kubectl get pods -n open-webui
```

### View logs
```bash
kubectl logs -n open-webui deployment/open-webui-stack-open-webui
kubectl logs -n open-webui deployment/open-webui-stack-gateway
```

### Access services directly
```bash
# Open WebUI
kubectl port-forward -n open-webui svc/open-webui-stack-open-webui 8080:8080

# Weaviate
kubectl port-forward -n open-webui svc/open-webui-stack-weaviate 8081:8080
```
