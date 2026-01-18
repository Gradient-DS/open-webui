---
date: 2026-01-18T14:30:00+01:00
researcher: Claude
git_commit: 12fee92cd50173380d4050daea864a8853e957f2
branch: feat/onedrive
repository: open-webui
topic: "OneDrive Sync Interval Not Working - Is Auto-Sync Implemented?"
tags: [research, codebase, onedrive, sync, scheduler]
status: complete
last_updated: 2026-01-18
last_updated_by: Claude
---

# Research: OneDrive Sync Interval Not Working - Is Auto-Sync Implemented?

**Date**: 2026-01-18T14:30:00+01:00
**Researcher**: Claude
**Git Commit**: 12fee92cd50173380d4050daea864a8853e957f2
**Branch**: feat/onedrive
**Repository**: open-webui

## Research Question

The flag `ONEDRIVE_SYNC_INTERVAL_MINUTES=15` does not appear to trigger automatic re-syncing. Files synced 22+ minutes ago have not been re-synced despite backend restarts. Is automatic periodic syncing implemented?

## Summary

**No, automatic periodic syncing is NOT implemented.** While the configuration and scheduler infrastructure exist, there are two critical issues:

1. **The scheduler is not started**: `start_scheduler()` is exported but never called from `main.py`
2. **The scheduler cannot execute syncs**: Even if started, the scheduler only **logs** which knowledge bases are due for sync - it does NOT actually trigger syncs due to OAuth token expiration limitations

The `ONEDRIVE_SYNC_INTERVAL_MINUTES` setting is currently only used to:
- Determine when a knowledge base is "due" for sync (for logging/UI purposes)
- Control the sleep interval of the scheduler loop (which isn't even running)

## Detailed Findings

### 1. Configuration is Properly Defined

**File:** `backend/open_webui/config.py:2507-2510`

```python
ONEDRIVE_SYNC_INTERVAL_MINUTES = PersistentConfig(
    "ONEDRIVE_SYNC_INTERVAL_MINUTES",
    "onedrive.sync_interval_minutes",
    int(os.getenv("ONEDRIVE_SYNC_INTERVAL_MINUTES", "60")),
)
```

The configuration is properly defined with a default of 60 minutes and can be overridden via environment variable.

### 2. Scheduler Infrastructure Exists But is Disabled

**File:** `backend/open_webui/services/onedrive/scheduler.py`

The scheduler has the right structure:

```python
async def run_scheduled_syncs():
    """Run scheduled sync checks for all configured Knowledge bases."""
    log.info("OneDrive sync scheduler started")

    while True:
        try:
            await _check_and_report_due_syncs()
        except Exception as e:
            log.exception(f"Scheduler error: {e}")

        # Wait for next interval
        interval_seconds = ONEDRIVE_SYNC_INTERVAL_MINUTES.value * 60
        log.debug(f"Scheduler sleeping for {interval_seconds} seconds")
        await asyncio.sleep(interval_seconds)
```

**Issue #1**: `start_scheduler()` is never called from `main.py`. The function exists and is exported, but no code invokes it during application startup.

### 3. Scheduler Only Monitors, Does Not Execute

**File:** `backend/open_webui/services/onedrive/scheduler.py:53-94`

Even if the scheduler were running, `_check_and_report_due_syncs()` only logs which knowledge bases are due:

```python
if due_for_sync:
    log.info(
        f"Found {len(due_for_sync)} knowledge base(s) due for OneDrive sync: "
        f"{[kb['name'] for kb in due_for_sync]}"
    )
    # Note: Cannot execute sync without valid access token
    # This would require implementing token refresh mechanism
    log.debug(
        "Scheduled sync execution requires token refresh implementation. "
        "Users should manually trigger sync for now."
    )
```

**Issue #2**: The code explicitly states it cannot execute syncs automatically because OAuth tokens expire after ~1 hour.

### 4. Syncs Only Trigger Manually

**File:** `backend/open_webui/routers/onedrive_sync.py:40-80`

The only way to trigger a sync is via the REST API:

```python
@router.post("/sync")
async def start_sync(
    request: SyncFolderRequest,
    background_tasks: BackgroundTasks,
    user: UserModel = Depends(get_verified_user),
):
    # ...
    background_tasks.add_task(
        sync_folder_to_knowledge,
        knowledge_id=request.knowledge_id,
        access_token=request.access_token,  # Fresh token from frontend
        # ...
    )
```

The `access_token` must be provided fresh from the frontend each time, as delegated OAuth tokens expire.

## Code References

- `backend/open_webui/config.py:2507-2510` - ONEDRIVE_SYNC_INTERVAL_MINUTES definition
- `backend/open_webui/services/onedrive/scheduler.py:32-50` - run_scheduled_syncs() loop
- `backend/open_webui/services/onedrive/scheduler.py:53-94` - _check_and_report_due_syncs() (monitoring only)
- `backend/open_webui/services/onedrive/scheduler.py:97-112` - start_scheduler() (never called)
- `backend/open_webui/routers/onedrive_sync.py:40-80` - Manual sync endpoint
- `backend/open_webui/main.py:1477-1480` - Router registration (no scheduler start)

## Architecture Insights

### Why Automatic Sync is Not Implemented

Microsoft's delegated OAuth tokens (obtained via MSAL in the browser) have a ~1 hour expiration. To implement automatic periodic syncing, the system would need:

1. **Application-level OAuth credentials** (client credentials flow) - but this requires admin consent and doesn't support user-specific folders well
2. **Refresh token storage and management** - store and rotate refresh tokens server-side
3. **Azure AD app configuration** for offline_access scope

The current implementation uses delegated tokens obtained fresh from the frontend, which is simpler but prevents background automation.

### Current Data Flow

```
Manual Trigger Only:
Frontend (MSAL token) → POST /api/v1/onedrive/sync → Background Task → Sync Worker
                                                           ↓
                                                    Socket.IO Progress Updates
```

## Recommendations

To implement automatic periodic syncing:

### Option 1: Store Refresh Tokens (Recommended)

1. Request `offline_access` scope during OAuth consent
2. Store encrypted refresh tokens in the database per knowledge base
3. Implement token refresh in the scheduler before executing sync
4. Update `_check_and_report_due_syncs()` to call `sync_folder_to_knowledge()` after token refresh

### Option 2: Application Credentials (Limited)

1. Configure Azure AD app with application permissions
2. Use client credentials flow for background sync
3. Limitation: Only works for organizational data, not personal OneDrive

### Quick Fix: Start the Scheduler (Monitoring Only)

To at least see which knowledge bases are due for sync in the logs:

```python
# In main.py startup:
if app.state.config.ENABLE_ONEDRIVE_SYNC:
    from open_webui.services.onedrive import start_scheduler
    start_scheduler()
```

This won't execute syncs but will log which bases need attention.

## Open Questions

1. Should we implement refresh token storage for automatic syncing?
2. Is there a security concern with storing refresh tokens server-side?
3. Should the UI show "due for sync" status to prompt users to manually trigger?
4. What's the expected user experience - fully automatic or prompted manual sync?
