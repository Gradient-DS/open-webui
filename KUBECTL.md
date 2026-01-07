# Kubernetes Commands Reference

Quick reference for managing Open WebUI on GKE.

## Cluster Access

```bash
# Get cluster credentials (run once per session/machine)
gcloud container clusters get-credentials demo-cluster --zone=europe-west4-a

# Verify cluster access
kubectl cluster-info
kubectl get nodes
```

## Namespace: open-webui

All commands below assume namespace `open-webui`. Add `-n open-webui` to each command or set default:

```bash
# Set default namespace (optional)
kubectl config set-context --current --namespace=open-webui
```

## Viewing Resources

```bash
# All pods in namespace
kubectl get pods -n open-webui

# All resources in namespace
kubectl get all -n open-webui

# Pods with more details
kubectl get pods -n open-webui -o wide

# Watch pods in real-time
kubectl get pods -n open-webui -w

# Specific app
kubectl get pods -n open-webui -l app=open-webui
kubectl get pods -n open-webui -l app=postgres
kubectl get pods -n open-webui -l app=searxng
kubectl get pods -n open-webui -l app=playwright
```

## Logs

```bash
# Logs for a deployment (current pods)
kubectl logs -n open-webui -l app=open-webui --tail=100

# Logs for specific pod
kubectl logs -n open-webui <pod-name> --tail=100

# Follow logs in real-time
kubectl logs -n open-webui -l app=open-webui -f

# Previous container logs (after crash)
kubectl logs -n open-webui <pod-name> --previous
```

## Debugging

```bash
# Describe pod (events, status, conditions)
kubectl describe pod -n open-webui -l app=open-webui

# Execute command in pod
kubectl exec -n open-webui deployment/open-webui -- <command>

# Interactive shell
kubectl exec -n open-webui -it deployment/open-webui -- /bin/bash

# Check environment variables
kubectl exec -n open-webui deployment/open-webui -- printenv | sort

# Check specific env var
kubectl exec -n open-webui deployment/open-webui -- printenv DATABASE_URL

# Test database connection
kubectl exec -n open-webui postgres-0 -- pg_isready -U openwebui
```

## Secrets Management

```bash
# List secrets
kubectl get secrets -n open-webui

# View secret keys (not values)
kubectl get secret open-webui-secrets -n open-webui -o jsonpath='{.data}' | jq 'keys'

# Decode a secret value
kubectl get secret open-webui-secrets -n open-webui -o jsonpath='{.data.<KEY_NAME>}' | base64 -d; echo

# Example: decode DATABASE_PASSWORD
kubectl get secret open-webui-secrets -n open-webui -o jsonpath='{.data.DATABASE_PASSWORD}' | base64 -d; echo

# Delete and recreate secret
kubectl delete secret open-webui-secrets -n open-webui

kubectl create secret generic open-webui-secrets \
  --namespace open-webui \
  --from-literal=WEBUI_SECRET_KEY="<your-secret-key>" \
  --from-literal=DATABASE_PASSWORD="<your-db-password>" \
  --from-literal=OPENWEBUI_ADMIN_PASSWORD='<your-admin-password>' \
  --from-literal=OPENAI_API_KEY="<your-openai-key>" \
  --from-literal=RAG_OPENAI_API_KEY="<your-rag-key>"
```

## ConfigMap Management

```bash
# View ConfigMap
kubectl get configmap open-webui-config -n open-webui -o yaml

# Edit ConfigMap directly
kubectl edit configmap open-webui-config -n open-webui

# Apply updated ConfigMap from file
kubectl apply -f k8s/open-webui.yaml
```

## Deployments & Rollouts

```bash
# Restart deployment (picks up new ConfigMap/Secret)
kubectl rollout restart deployment/open-webui -n open-webui

# Watch rollout status
kubectl rollout status deployment/open-webui -n open-webui

# Rollout history
kubectl rollout history deployment/open-webui -n open-webui

# Rollback to previous version
kubectl rollout undo deployment/open-webui -n open-webui

# Scale deployment
kubectl scale deployment/open-webui -n open-webui --replicas=2
```

## Applying Manifests

```bash
# Apply single file
kubectl apply -f k8s/open-webui.yaml

# Apply all files in directory
kubectl apply -f k8s/

# Apply with dry-run (preview changes)
kubectl apply -f k8s/open-webui.yaml --dry-run=client

# Delete resources from file
kubectl delete -f k8s/open-webui.yaml
```

## Services & Networking

```bash
# List services
kubectl get svc -n open-webui

# List ingress
kubectl get ingress -n open-webui

# Describe ingress (shows IP, rules)
kubectl describe ingress -n open-webui

# Port forward for local testing (bypass ingress)
kubectl port-forward -n open-webui svc/open-webui 8080:8080
# Then access: http://localhost:8080
```

## Certificates (cert-manager)

```bash
# Check certificate status
kubectl get certificate -n open-webui

# Describe certificate (shows Ready status, expiry)
kubectl describe certificate open-webui-tls -n open-webui

# Check cert-manager logs
kubectl logs -n cert-manager -l app=cert-manager --tail=50

# List certificate challenges (during issuance)
kubectl get challenges -n open-webui
```

## Persistent Storage

```bash
# List PVCs
kubectl get pvc -n open-webui

# Describe PVC
kubectl describe pvc open-webui-pvc -n open-webui
kubectl describe pvc postgres-pvc -n open-webui
```

## Health Checks

```bash
# Test health endpoint via kubectl
kubectl exec -n open-webui deployment/open-webui -- wget -q -O- http://localhost:8080/health

# Test via ingress
curl -s https://voorbeeld.soev.ai/health

# Check all pods health
kubectl get pods -n open-webui -o custom-columns=NAME:.metadata.name,STATUS:.status.phase,READY:.status.containerStatuses[0].ready
```

## Common Workflows

### Update Configuration

```bash
# 1. Edit the manifest
vim k8s/open-webui.yaml

# 2. Apply changes
kubectl apply -f k8s/open-webui.yaml

# 3. Restart to pick up changes
kubectl rollout restart deployment/open-webui -n open-webui

# 4. Watch rollout
kubectl rollout status deployment/open-webui -n open-webui
```

### Update Secret Value

```bash
# 1. Get current password (if needed)
kubectl get secret open-webui-secrets -n open-webui -o jsonpath='{.data.DATABASE_PASSWORD}' | base64 -d; echo

# 2. Delete old secret
kubectl delete secret open-webui-secrets -n open-webui

# 3. Create new secret with updated values
kubectl create secret generic open-webui-secrets \
  --namespace open-webui \
  --from-literal=WEBUI_SECRET_KEY="<value>" \
  --from-literal=DATABASE_PASSWORD="<value>" \
  --from-literal=OPENWEBUI_ADMIN_PASSWORD='<value>' \
  --from-literal=OPENAI_API_KEY="<value>" \
  --from-literal=RAG_OPENAI_API_KEY="<value>"

# 4. Restart deployment
kubectl rollout restart deployment/open-webui -n open-webui
```

### Troubleshoot Crashing Pod

```bash
# 1. Check pod status
kubectl get pods -n open-webui -l app=open-webui

# 2. Check events
kubectl describe pod -n open-webui -l app=open-webui | tail -30

# 3. Check logs
kubectl logs -n open-webui -l app=open-webui --tail=100

# 4. Check previous container logs (if restarting)
kubectl logs -n open-webui <pod-name> --previous

# 5. Check environment variables
kubectl exec -n open-webui deployment/open-webui -- printenv | sort
```

### Force Delete Stuck Pod

```bash
# Graceful delete
kubectl delete pod -n open-webui <pod-name>

# Force delete (if stuck in Terminating)
kubectl delete pod -n open-webui <pod-name> --grace-period=0 --force
```

## Cleanup

```bash
# Delete application namespace (removes everything in it)
kubectl delete namespace open-webui

# Delete cluster
gcloud container clusters delete demo-cluster --zone=europe-west4-a

# Delete static IP
gcloud compute addresses delete demo-ip --region=europe-west4

# Delete VPC
gcloud compute networks delete demo-vpc --quiet
```

## Useful Aliases

Add to your `~/.zshrc` or `~/.bashrc`:

```bash
alias k='kubectl'
alias kgp='kubectl get pods'
alias kgpa='kubectl get pods -A'
alias klog='kubectl logs'
alias kexec='kubectl exec -it'
alias kns='kubectl config set-context --current --namespace'

# Open WebUI specific
alias owui='kubectl -n open-webui'
alias owui-logs='kubectl logs -n open-webui -l app=open-webui -f'
alias owui-restart='kubectl rollout restart deployment/open-webui -n open-webui'
```
