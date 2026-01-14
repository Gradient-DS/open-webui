# LibreChat to Open WebUI Migration

This Helm chart includes a migration job to import data from LibreChat to Open WebUI.

## Prerequisites

1. A LibreChat backup created with `create-librechat-backup-docker.sh`
2. The backup archive extracted and accessible to the Kubernetes cluster
3. Open WebUI already deployed with the Helm chart

## What Gets Migrated

- **Users**: Email/password users (OAuth users skipped, all set to "user" role)
- **Files**: Local uploads and images
- **Conversations**: Full chat history with messages
- **Prompts**: PromptGroups flattened to single prompts
- **Agents**: Converted to Open WebUI models (prefixed with `agent-`)

## Migration Steps

### Step 1: Create and Transfer Backup

On your LibreChat server:

```bash
# Create backup
cd /path/to/librechat
./create-librechat-backup-docker.sh

# Transfer to local machine
scp librechat-backup-YYYYMMDD-HHMMSS.tar.gz user@local:/tmp/
```

### Step 2: Create PVC for Backup Data

```yaml
# backup-pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: librechat-backup-pvc
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 10Gi
```

```bash
kubectl apply -f backup-pvc.yaml
```

### Step 3: Copy Backup to PVC

```bash
# Create a temporary pod to copy data
kubectl run backup-loader --image=busybox --restart=Never \
  --overrides='{"spec":{"containers":[{"name":"backup-loader","image":"busybox","command":["sleep","3600"],"volumeMounts":[{"name":"backup","mountPath":"/backup"}]}],"volumes":[{"name":"backup","persistentVolumeClaim":{"claimName":"librechat-backup-pvc"}}]}}'

# Wait for pod to be ready
kubectl wait --for=condition=Ready pod/backup-loader

# Copy and extract backup
kubectl cp librechat-backup-YYYYMMDD-HHMMSS.tar.gz backup-loader:/backup/
kubectl exec backup-loader -- tar -xzf /backup/librechat-backup-YYYYMMDD-HHMMSS.tar.gz -C /backup/

# Verify extraction
kubectl exec backup-loader -- ls -la /backup/

# Clean up loader pod
kubectl delete pod backup-loader
```

### Step 4: Run Dry-Run Migration

First, run a dry-run to preview what will be migrated:

```bash
# Create migration values file
cat > migration-values.yaml << EOF
migration:
  enabled: true
  dryRun: true
  backupDir: "librechat-backup-YYYYMMDD-HHMMSS"
  backupPvc: "librechat-backup-pvc"
  defaultModel: "gpt-4o"
EOF

# Upgrade release with migration enabled
helm upgrade <release-name> ./open-webui-stack -f migration-values.yaml

# Watch migration job logs
kubectl logs -f job/<release-name>-migration
```

### Step 5: Run Actual Migration

After verifying the dry-run output:

```bash
# Delete the dry-run job first
kubectl delete job <release-name>-migration

# Update values for actual migration
cat > migration-values.yaml << EOF
migration:
  enabled: true
  dryRun: false
  backupDir: "librechat-backup-YYYYMMDD-HHMMSS"
  backupPvc: "librechat-backup-pvc"
  defaultModel: "gpt-4o"
EOF

# Run migration
helm upgrade <release-name> ./open-webui-stack -f migration-values.yaml

# Watch logs
kubectl logs -f job/<release-name>-migration
```

### Step 6: Verify and Cleanup

```bash
# Check job status
kubectl get job <release-name>-migration

# If successful, disable migration for future upgrades
cat > migration-values.yaml << EOF
migration:
  enabled: false
EOF

helm upgrade <release-name> ./open-webui-stack -f migration-values.yaml

# Clean up backup PVC
kubectl delete pvc librechat-backup-pvc
```

## Configuration Options

| Value | Default | Description |
|-------|---------|-------------|
| `migration.enabled` | `false` | Enable migration job |
| `migration.dryRun` | `true` | Preview mode (no changes) |
| `migration.backupDir` | `"librechat-backup"` | Name of extracted backup directory |
| `migration.backupPvc` | `""` | PVC with extracted backup |
| `migration.backupArchivePvc` | `""` | PVC with backup.tar.gz (auto-extracted) |
| `migration.preferImportPassword` | `false` | Update passwords for existing users |
| `migration.defaultModel` | `"gpt-4o"` | Fallback model for unmapped agents |

## Troubleshooting

### Job fails with "Backup directory not found"

Verify the backup directory name matches exactly:

```bash
kubectl exec <any-pod-with-backup-pvc> -- ls -la /backup/
```

### Database connection errors

Check that PostgreSQL is running and secrets are correct:

```bash
kubectl get pods -l app.kubernetes.io/component=postgres
kubectl get secret <release-name>-secrets -o yaml
```

### Users not imported

The migration skips:
- Users without email addresses
- OAuth-only users (no password)
- Duplicate emails (unless `preferImportPassword: true`)

### Files not migrated

The migration skips:
- Files stored in S3/external storage
- Files missing from the backup archive

Check the backup contains the files directory:

```bash
kubectl exec backup-loader -- ls -la /backup/librechat-backup-*/files/
```
