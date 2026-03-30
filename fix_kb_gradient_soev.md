# Fix Knowledge Base Permissions — gradient.soev.ai

**Issue**: Sync workers mirror OneDrive/Google Drive sharing permissions into Open WebUI access grants, making private KBs visible to all team members as read-only.

**Prerequisite**: Deploy the code fix for `_sync_permissions()` BEFORE running cleanup, otherwise the next sync cycle (≤15 minutes) will recreate all grants.

## 1. Review current grants

```bash
kubectl exec -it sts/gradient-postgres -n open-webui-gradient -- psql -U openwebui -d openwebui -c "
SELECT ag.principal_type, ag.principal_id, ag.permission, k.name, k.type, u.name as granted_to
FROM access_grant ag
JOIN knowledge k ON ag.resource_id = k.id
LEFT JOIN \"user\" u ON ag.principal_id = u.id
WHERE ag.resource_type = 'knowledge'
ORDER BY k.name, ag.permission;"
```

## 2. Delete all access grants on external KBs

External KBs (OneDrive, Google Drive) should be owner-only. Owner access is implicit (no grant needed).

```bash
kubectl exec -it sts/gradient-postgres -n open-webui-gradient -- psql -U openwebui -d openwebui -c "
DELETE FROM access_grant
WHERE resource_type = 'knowledge'
AND resource_id IN (
    SELECT id FROM knowledge WHERE type IN ('onedrive', 'google_drive')
);"
```

## 3. Verify cleanup

```bash
kubectl exec -it sts/gradient-postgres -n open-webui-gradient -- psql -U openwebui -d openwebui -c "
SELECT count(*) FROM access_grant ag
JOIN knowledge k ON ag.resource_id = k.id
WHERE ag.resource_type = 'knowledge' AND k.type IN ('onedrive', 'google_drive');"
```

Expected result: `0`.
