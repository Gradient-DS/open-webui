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

## Bootstrap FluxCD

```bash
# Install Flux CLI
brew install fluxcd/tap/flux

# Verify cluster compatibility
flux check --pre

# Bootstrap Flux with GitHub
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

## Uninstalling Flux

```bash
flux uninstall --namespace=flux-system
```
