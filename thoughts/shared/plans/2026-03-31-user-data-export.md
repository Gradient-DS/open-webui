# User Data Export (DPIA/GDPR Data Portability) — Implementation Plan

## Overview

Self-service "Download My Data" feature enabling users to export all their personal data as a ZIP archive. Serves GDPR Art. 20 (right to data portability) and user trust. The export runs asynchronously in the background and notifies the user via Socket.IO toast when ready for download.

## Current State Analysis

**What exists:**

- Chat export: user self-service via `GET /api/v1/chats/all` (JSON) — `DataControls.svelte:105-110`
- Admin DB export: `GET /api/v1/utils/db/export` (full database JSON) — gated by `ENABLE_ADMIN_EXPORT`
- GDPR archival: `POST /api/v1/archives/user/{user_id}` — captures profile + chats only
- Per-resource admin exports: models, tools, functions, knowledge bases, feedbacks

**What's missing:**

- No unified "export all my data" endpoint
- No self-service export for: files, memories, notes, settings, prompts, tools, models, feedbacks, tags, folders
- No standard-compatible format with schema definition
- GDPR archive only captures profile + chats

### Key Discoveries:

- Background task pattern: `FastAPI BackgroundTasks` + Socket.IO notification — established at `routers/files.py:297-306` with `services/files/events.py`
- Temp file serving: `CACHE_DIR` + `/cache/{path:path}` route at `main.py:2919-2930`
- Feature flag pattern: env var → `config.py` → `main.py` features dict → frontend store — e.g., `ENABLE_ADMIN_EXPORT` at `config.py:1679`
- User permission pattern: `USER_PERMISSIONS_CHAT_EXPORT` at `config.py:1438`, placed in `DEFAULT_USER_PERMISSIONS['chat']['export']` at `config.py:1530`
- Toast + download: `svelte-sonner` with `NotificationToast` component, `file-saver` for downloads

## Desired End State

A user can click "Download My Data" in Settings > Data Controls. The system generates a ZIP file containing all their personal data asynchronously, notifies them via toast when ready, and provides a time-limited download link. Cloud-synced files include metadata only; locally uploaded files are included.

### Verification:

- User can trigger export from Settings > Data Controls
- Export runs in background without blocking the UI
- User receives toast notification when export is ready
- Downloaded ZIP contains all user data types listed below
- Cloud-synced KB files include metadata only, local files include actual content
- Export respects `ENABLE_DATA_EXPORT` feature flag
- Old exports are automatically cleaned up after 24 hours
- Feature flag wired through Helm chart

## What We're NOT Doing

- Exporting vector embeddings (regeneratable from source files)
- Exporting auth credentials, API keys, TOTP secrets, OAuth tokens (security-sensitive)
- Exporting cloud-synced file contents (OneDrive/Google Drive — already in user's cloud storage)
- Standard format conversion (e.g., MBOX for chats) — our JSON format with a schema version field
- Admin-initiated export on behalf of user (existing archival system covers this use case)
- Channel messages export (can be added later)
- Import counterpart for full data export (complex, out of scope)
- Per-group permission for data export (use global feature flag for now — simpler, and this is a user right)

## Implementation Approach

Follow established patterns:

1. Feature flag: `ENABLE_DATA_EXPORT` as plain `bool` env var (like `ENABLE_ADMIN_EXPORT`), not PersistentConfig — this is a platform capability, not a runtime toggle
2. Background task: `FastAPI BackgroundTasks` pattern from file upload processing
3. Notification: Socket.IO event to user room, like `emit_file_status`
4. File serving: Write ZIP to `CACHE_DIR/exports/`, serve via `/cache/` route
5. Cleanup: Periodic asyncio task like `periodic_archive_cleanup`

## Phase 1: Backend Config & Feature Flag

### Overview

Add the `ENABLE_DATA_EXPORT` environment variable, wire it through config, main.py, Helm chart, and frontend store.

### Changes Required:

#### 1. Config variable

**File**: `backend/open_webui/config.py`
**Changes**: Add `ENABLE_DATA_EXPORT` near `ENABLE_ADMIN_EXPORT` (line ~1679)

```python
####################################
# User Data Export
####################################

ENABLE_DATA_EXPORT = os.environ.get('ENABLE_DATA_EXPORT', 'True').lower() == 'true'

# How long export ZIPs are kept before cleanup (hours)
DATA_EXPORT_RETENTION_HOURS = int(os.environ.get('DATA_EXPORT_RETENTION_HOURS', '24'))
```

#### 2. Wire to frontend config

**File**: `backend/open_webui/main.py`
**Changes**: Add to the features dict near line 2347

```python
'enable_data_export': ENABLE_DATA_EXPORT,
```

Import `ENABLE_DATA_EXPORT` from `config` at the top of the file (alongside existing `ENABLE_ADMIN_EXPORT` import).

#### 3. Frontend type

**File**: `src/lib/stores/index.ts`
**Changes**: Add to the `features` type near line 293

```typescript
enable_data_export: boolean;
```

#### 4. Helm values

**File**: `helm/open-webui-tenant/values.yaml`
**Changes**: Add near `enableAdminExport` (line ~307)

```yaml
enableDataExport: 'true'
dataExportRetentionHours: '24'
```

#### 5. Helm configmap

**File**: `helm/open-webui-tenant/templates/open-webui/configmap.yaml`
**Changes**: Add near the admin export section (line ~224)

```yaml
ENABLE_DATA_EXPORT: { { .Values.openWebui.config.enableDataExport | quote } }
DATA_EXPORT_RETENTION_HOURS: { { .Values.openWebui.config.dataExportRetentionHours | quote } }
```

### Success Criteria:

#### Automated Verification:

- [x] `npm run build` succeeds (frontend compiles with new type)
- [ ] Backend starts without errors: `open-webui dev`
- [ ] `ENABLE_DATA_EXPORT` appears in `/api/v1/config` response features

#### Manual Verification:

- [ ] Helm template renders correctly: `helm template` shows new env vars

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding.

---

## Phase 2: Export Service & Events

### Overview

Create the core export service that collects all user data and writes it to a ZIP file, plus the Socket.IO event emitter.

### Changes Required:

#### 1. Export events

**File**: `backend/open_webui/services/export/__init__.py` (empty)
**File**: `backend/open_webui/services/export/events.py`
**Changes**: New file, following the pattern from `services/files/events.py`

```python
import logging
from typing import Optional

log = logging.getLogger(__name__)


async def emit_export_status(
    user_id: str,
    status: str,
    export_path: Optional[str] = None,
    error: Optional[str] = None,
):
    """
    Emit data export status via Socket.IO.

    Args:
        user_id: User who requested the export
        status: 'processing', 'completed', or 'failed'
        export_path: The download path if completed (relative to /cache/)
        error: Error message if status is 'failed'
    """
    try:
        from open_webui.socket.main import sio

        await sio.emit(
            'export:status',
            {
                'status': status,
                'export_path': export_path,
                'error': error,
            },
            room=f'user:{user_id}',
        )
    except Exception as e:
        log.debug(f'Failed to emit export status event: {e}')
```

#### 2. Export service

**File**: `backend/open_webui/services/export/service.py`
**Changes**: New file with the core export logic

```python
import asyncio
import json
import logging
import time
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

from open_webui.config import CACHE_DIR, DATA_EXPORT_RETENTION_HOURS
from open_webui.models.chats import Chats, ChatResponse
from open_webui.models.feedbacks import Feedbacks
from open_webui.models.files import Files
from open_webui.models.folders import Folders
from open_webui.models.functions import Functions
from open_webui.models.knowledge import Knowledges
from open_webui.models.memories import Memories
from open_webui.models.models import Models
from open_webui.models.notes import Notes
from open_webui.models.prompts import Prompts
from open_webui.models.tags import Tags
from open_webui.models.tools import Tools
from open_webui.models.users import Users
from open_webui.services.export.events import emit_export_status
from open_webui.storage.provider import Storage

log = logging.getLogger(__name__)

EXPORT_DIR = CACHE_DIR / 'exports'
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

EXPORT_SCHEMA_VERSION = '1.0'


class ExportService:
    @staticmethod
    def get_export_dir(user_id: str) -> Path:
        """Get or create the export directory for a user."""
        user_dir = EXPORT_DIR / user_id
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir

    @staticmethod
    def get_active_export(user_id: str) -> dict | None:
        """Check if there's an existing export for this user."""
        user_dir = EXPORT_DIR / user_id
        if not user_dir.exists():
            return None

        for f in user_dir.glob('export-*.zip'):
            return {
                'filename': f.name,
                'path': f'exports/{user_id}/{f.name}',
                'created_at': f.stat().st_mtime,
                'size': f.stat().st_size,
            }
        return None

    @staticmethod
    def _serialize(obj: Any) -> Any:
        """JSON-safe serialization for model objects."""
        if hasattr(obj, 'model_dump'):
            return obj.model_dump()
        if hasattr(obj, '__dict__'):
            return obj.__dict__
        return obj

    @staticmethod
    def collect_user_data(user_id: str) -> dict:
        """
        Collect all exportable data for a user.
        Returns a dict of data category -> list/dict of records.
        """
        data = {}

        # User profile
        user = Users.get_user_by_id(user_id)
        if user:
            profile = user.model_dump()
            # Remove sensitive fields
            for field in ['settings', 'oauth', 'scim']:
                profile.pop(field, None)
            data['profile'] = profile

            # User settings (without sensitive internals)
            if user.settings:
                settings = user.settings.model_dump() if hasattr(user.settings, 'model_dump') else user.settings
                # Remove tool/function valve settings (internal state)
                data['settings'] = settings

        # Chats
        chats = Chats.get_chats_by_user_id(user_id)
        data['chats'] = [ExportService._serialize(c) for c in chats]

        # Memories
        memories = Memories.get_memories_by_user_id(user_id)
        data['memories'] = [ExportService._serialize(m) for m in memories]

        # Notes
        notes = Notes.get_notes_by_user_id(user_id)
        data['notes'] = [ExportService._serialize(n) for n in notes]

        # Prompts
        prompts = Prompts.get_prompts_by_user_id(user_id)
        data['prompts'] = [ExportService._serialize(p) for p in prompts]

        # Tools
        tools = Tools.get_tools_by_user_id(user_id)
        data['tools'] = [ExportService._serialize(t) for t in tools]

        # Custom models
        models = Models.get_models_by_user_id(user_id)
        data['models'] = [ExportService._serialize(m) for m in models]

        # Feedbacks
        feedbacks = Feedbacks.get_feedbacks_by_user_id(user_id)
        data['feedbacks'] = [ExportService._serialize(f) for f in feedbacks]

        # Tags
        tags = Tags.get_tags_by_user_id(user_id)
        data['tags'] = [ExportService._serialize(t) for t in tags]

        # Folders
        folders = Folders.get_folders_by_user_id(user_id)
        data['folders'] = [ExportService._serialize(f) for f in folders]

        # Files metadata
        files = Files.get_files_by_user_id(user_id)
        data['files'] = [ExportService._serialize(f) for f in files]

        # Knowledge bases metadata
        knowledge_bases = Knowledges.get_knowledge_bases_by_user_id(user_id)
        data['knowledge_bases'] = [ExportService._serialize(kb) for kb in knowledge_bases]

        return data

    @staticmethod
    def _get_local_file_ids(data: dict) -> list[str]:
        """
        Determine which files should be included as actual content.
        Only include files from local knowledge bases (not cloud-synced).
        """
        # Build set of cloud-synced KB IDs
        cloud_kb_ids = set()
        for kb in data.get('knowledge_bases', []):
            kb_type = kb.get('type', 'local')
            if kb_type in ('onedrive', 'google_drive'):
                cloud_kb_ids.add(kb.get('id'))

        # Build set of file IDs that belong to cloud KBs
        # (knowledge_file junction — we need to check which files belong to cloud KBs)
        cloud_file_ids = set()
        for kb in data.get('knowledge_bases', []):
            if kb.get('id') in cloud_kb_ids:
                # Files referenced in KB data.file_ids
                kb_data = kb.get('data', {})
                if kb_data and isinstance(kb_data, dict):
                    file_ids = kb_data.get('file_ids', [])
                    cloud_file_ids.update(file_ids)

        # Return file IDs that are NOT in cloud KBs
        local_file_ids = []
        for f in data.get('files', []):
            if f.get('id') not in cloud_file_ids:
                local_file_ids.append(f.get('id'))

        return local_file_ids

    @staticmethod
    def build_export_zip(user_id: str, data: dict) -> Path:
        """
        Build a ZIP file containing all user data.
        Returns the path to the generated ZIP.
        """
        export_dir = ExportService.get_export_dir(user_id)

        # Clean up any previous exports for this user
        for old_file in export_dir.glob('export-*.zip'):
            old_file.unlink()

        timestamp = int(time.time())
        zip_path = export_dir / f'export-{timestamp}.zip'

        local_file_ids = ExportService._get_local_file_ids(data)

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Manifest
            manifest = {
                'schema_version': EXPORT_SCHEMA_VERSION,
                'exported_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'user_id': user_id,
                'user_email': data.get('profile', {}).get('email', ''),
                'user_name': data.get('profile', {}).get('name', ''),
                'contents': {
                    'chats': len(data.get('chats', [])),
                    'memories': len(data.get('memories', [])),
                    'notes': len(data.get('notes', [])),
                    'prompts': len(data.get('prompts', [])),
                    'tools': len(data.get('tools', [])),
                    'models': len(data.get('models', [])),
                    'feedbacks': len(data.get('feedbacks', [])),
                    'tags': len(data.get('tags', [])),
                    'folders': len(data.get('folders', [])),
                    'files': len(data.get('files', [])),
                    'files_included': len(local_file_ids),
                    'knowledge_bases': len(data.get('knowledge_bases', [])),
                },
            }
            zf.writestr('manifest.json', json.dumps(manifest, indent=2, default=str))

            # Individual data files
            for key in [
                'profile', 'settings', 'chats', 'memories', 'notes',
                'prompts', 'tools', 'models', 'feedbacks', 'tags',
                'folders',
            ]:
                if key in data:
                    zf.writestr(
                        f'{key}.json',
                        json.dumps(data[key], indent=2, default=str, ensure_ascii=False),
                    )

            # Files metadata (always included)
            zf.writestr(
                'files/metadata.json',
                json.dumps(data.get('files', []), indent=2, default=str, ensure_ascii=False),
            )

            # Knowledge bases metadata (always included)
            zf.writestr(
                'knowledge/metadata.json',
                json.dumps(data.get('knowledge_bases', []), indent=2, default=str, ensure_ascii=False),
            )

            # Actual file contents for local (non-cloud) files
            for file_id in local_file_ids:
                try:
                    file_record = Files.get_file_by_id(file_id)
                    if file_record and file_record.path:
                        local_path = Storage.get_file(file_record.path)
                        if local_path and Path(local_path).exists():
                            # Use original filename from meta, fallback to file_id
                            meta = file_record.meta or {}
                            original_name = meta.get('name', file_record.filename or file_id)
                            zf.write(local_path, f'files/uploads/{file_id}_{original_name}')
                except Exception as e:
                    log.warning(f'Failed to include file {file_id} in export: {e}')

        return zip_path

    @staticmethod
    def generate_export(user_id: str):
        """
        Synchronous entry point for background task.
        Collects data, builds ZIP, notifies user.
        """
        try:
            # Notify: processing
            asyncio.run(emit_export_status(user_id, 'processing'))

            # Collect all data
            data = ExportService.collect_user_data(user_id)

            # Build ZIP
            zip_path = ExportService.build_export_zip(user_id, data)

            # Notify: completed
            relative_path = f'exports/{user_id}/{zip_path.name}'
            asyncio.run(
                emit_export_status(user_id, 'completed', export_path=relative_path)
            )

            log.info(f'Data export completed for user {user_id}: {zip_path}')

        except Exception as e:
            log.error(f'Data export failed for user {user_id}: {e}')
            asyncio.run(emit_export_status(user_id, 'failed', error=str(e)))

    @staticmethod
    def cleanup_expired_exports():
        """Delete export ZIPs older than DATA_EXPORT_RETENTION_HOURS."""
        if not EXPORT_DIR.exists():
            return {'deleted': 0}

        cutoff = time.time() - (DATA_EXPORT_RETENTION_HOURS * 3600)
        deleted = 0

        for user_dir in EXPORT_DIR.iterdir():
            if not user_dir.is_dir():
                continue
            for zip_file in user_dir.glob('export-*.zip'):
                if zip_file.stat().st_mtime < cutoff:
                    zip_file.unlink()
                    deleted += 1
            # Remove empty user dirs
            if not any(user_dir.iterdir()):
                user_dir.rmdir()

        return {'deleted': deleted}
```

**Implementation notes:**

- `generate_export` is synchronous because `BackgroundTasks` runs sync functions in the thread pool (same pattern as `process_uploaded_file` in `files.py`)
- Uses `asyncio.run()` to emit Socket.IO events from sync context (same pattern as `files.py:146`)
- Some model query methods (e.g., `get_prompts_by_user_id`, `get_tools_by_user_id`, `get_feedbacks_by_user_id`) may need to be added or verified — Phase 2 implementation should check which exist and add missing ones

### Success Criteria:

#### Automated Verification:

- [ ] Backend starts without import errors
- [ ] `ExportService.collect_user_data(user_id)` returns data for a test user
- [ ] `ExportService.build_export_zip(user_id, data)` produces a valid ZIP

#### Manual Verification:

- [ ] Generated ZIP contains expected structure (manifest.json, data files, files/uploads/)
- [ ] Cloud-synced KB files are metadata-only, local files include content

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding.

---

## Phase 3: API Router & Background Task

### Overview

Create the export router with endpoints to trigger, check status, and download exports. Wire into main.py with periodic cleanup.

### Changes Required:

#### 1. Export router

**File**: `backend/open_webui/routers/export.py`
**Changes**: New file

```python
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from open_webui.config import ENABLE_DATA_EXPORT
from open_webui.services.export.service import ExportService
from open_webui.utils.auth import get_verified_user

log = logging.getLogger(__name__)

router = APIRouter()


class ExportStatusResponse(BaseModel):
    status: str  # 'none', 'ready'
    export_path: str | None = None
    created_at: float | None = None
    size: int | None = None


@router.post('/data')
async def trigger_data_export(
    request: Request,
    background_tasks: BackgroundTasks,
    user=Depends(get_verified_user),
):
    """Trigger an async data export for the current user."""
    if not ENABLE_DATA_EXPORT:
        raise HTTPException(status_code=403, detail='Data export is disabled')

    # Check if there's already an export in progress or ready
    existing = ExportService.get_active_export(user.id)
    if existing:
        return {
            'status': 'ready',
            'message': 'An export is already available for download',
            'export_path': existing['path'],
        }

    # Start background export
    background_tasks.add_task(ExportService.generate_export, user.id)

    return {
        'status': 'processing',
        'message': 'Export started. You will be notified when it is ready.',
    }


@router.get('/data/status')
async def get_export_status(
    request: Request,
    user=Depends(get_verified_user),
) -> ExportStatusResponse:
    """Check the status of the user's data export."""
    if not ENABLE_DATA_EXPORT:
        raise HTTPException(status_code=403, detail='Data export is disabled')

    existing = ExportService.get_active_export(user.id)
    if existing:
        return ExportStatusResponse(
            status='ready',
            export_path=existing['path'],
            created_at=existing['created_at'],
            size=existing['size'],
        )

    return ExportStatusResponse(status='none')


@router.delete('/data')
async def delete_export(
    request: Request,
    user=Depends(get_verified_user),
):
    """Delete the user's existing export file."""
    if not ENABLE_DATA_EXPORT:
        raise HTTPException(status_code=403, detail='Data export is disabled')

    from open_webui.services.export.service import EXPORT_DIR

    user_dir = EXPORT_DIR / user.id
    if user_dir.exists():
        for f in user_dir.glob('export-*.zip'):
            f.unlink()

    return {'status': 'deleted'}
```

#### 2. Mount router in main.py

**File**: `backend/open_webui/main.py`
**Changes**: Add near the archives router mount (line ~1733)

```python
from open_webui.routers import export as export_router

app.include_router(export_router.router, prefix='/api/v1/export', tags=['export'])
```

#### 3. Periodic cleanup task

**File**: `backend/open_webui/main.py`
**Changes**: Add a periodic cleanup function near `periodic_archive_cleanup` (line ~699) and start it in lifespan (line ~765)

```python
async def periodic_export_cleanup():
    """Periodic task to delete expired data exports (runs every 6 hours)"""
    from open_webui.services.export.service import ExportService

    while True:
        try:
            await asyncio.sleep(6 * 60 * 60)  # Every 6 hours
            stats = ExportService.cleanup_expired_exports()
            if stats['deleted'] > 0:
                log.info(f'Export cleanup: deleted {stats["deleted"]} expired exports')
        except Exception as e:
            log.error(f'Error in export cleanup: {e}')
```

In the lifespan startup (near line 765):

```python
asyncio.create_task(periodic_export_cleanup())
```

#### 4. Import config in main.py

**File**: `backend/open_webui/main.py`
**Changes**: Add `ENABLE_DATA_EXPORT` to the config imports and to the features dict

```python
# In imports (near ENABLE_ADMIN_EXPORT import):
from open_webui.config import ENABLE_DATA_EXPORT

# In features dict (near line 2347):
'enable_data_export': ENABLE_DATA_EXPORT,
```

### Success Criteria:

#### Automated Verification:

- [ ] `npm run build` succeeds
- [ ] Backend starts without errors
- [ ] `POST /api/v1/export/data` returns 200 with `status: 'processing'`
- [ ] `GET /api/v1/export/data/status` returns `status: 'none'` initially, then `status: 'ready'` after processing

#### Manual Verification:

- [ ] After triggering export, the ZIP appears in `data/cache/exports/{user_id}/`
- [ ] The `/cache/exports/{user_id}/export-{ts}.zip` URL serves the file for download
- [ ] Setting `ENABLE_DATA_EXPORT=false` returns 403 on all export endpoints

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding.

---

## Phase 4: Frontend UI

### Overview

Add the "Download My Data" section to DataControls.svelte with Socket.IO listener, toast notification, and download trigger.

### Changes Required:

#### 1. Export API client

**File**: `src/lib/apis/export/index.ts`
**Changes**: New file

```typescript
import { WEBUI_API_BASE_URL } from '$lib/constants';

export const triggerDataExport = async (token: string) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/export/data`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) throw await res.json();
	return res.json();
};

export const getExportStatus = async (token: string) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/export/data/status`, {
		method: 'GET',
		headers: {
			Authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) throw await res.json();
	return res.json();
};

export const deleteExport = async (token: string) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/export/data`, {
		method: 'DELETE',
		headers: {
			Authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) throw await res.json();
	return res.json();
};
```

#### 2. DataControls.svelte — Add "Download My Data" section

**File**: `src/lib/components/chat/Settings/DataControls.svelte`
**Changes**: Add export section, Socket.IO listener, and state management

Add imports:

```typescript
import { triggerDataExport, getExportStatus, deleteExport } from '$lib/apis/export';
import { config, socket } from '$lib/stores';
import { WEBUI_BASE_URL } from '$lib/constants';
```

Add state variables:

```typescript
let exportStatus: 'none' | 'processing' | 'ready' = 'none';
let exportPath: string | null = null;
let exportRequesting = false;
```

Add `onMount` logic to check existing export status and register Socket.IO listener:

```typescript
onMount(async () => {
	if ($config?.features?.enable_data_export) {
		try {
			const status = await getExportStatus(localStorage.token);
			exportStatus = status.status;
			exportPath = status.export_path || null;
		} catch (e) {
			console.error('Failed to check export status:', e);
		}
	}

	const handleExportStatus = (data: any) => {
		if (data.status === 'completed') {
			exportStatus = 'ready';
			exportPath = data.export_path;
			exportRequesting = false;
			toast.success($i18n.t('Your data export is ready for download.'));
		} else if (data.status === 'failed') {
			exportStatus = 'none';
			exportRequesting = false;
			toast.error($i18n.t('Data export failed: {{error}}', { error: data.error }));
		} else if (data.status === 'processing') {
			exportStatus = 'processing';
		}
	};

	$socket?.on('export:status', handleExportStatus);

	return () => {
		$socket?.off('export:status', handleExportStatus);
	};
});
```

Add handler functions:

```typescript
const requestDataExport = async () => {
	exportRequesting = true;
	try {
		const res = await triggerDataExport(localStorage.token);
		if (res.status === 'ready') {
			exportStatus = 'ready';
			exportPath = res.export_path;
			exportRequesting = false;
		} else {
			exportStatus = 'processing';
			toast.success($i18n.t('Data export started. You will be notified when it is ready.'));
		}
	} catch (e) {
		exportRequesting = false;
		toast.error($i18n.t('Failed to start data export.'));
	}
};

const downloadDataExport = () => {
	if (exportPath) {
		const a = document.createElement('a');
		a.href = `${WEBUI_BASE_URL}/cache/${exportPath}`;
		a.download = `my-data-export.zip`;
		a.click();
	}
};

const deleteDataExport = async () => {
	try {
		await deleteExport(localStorage.token);
		exportStatus = 'none';
		exportPath = null;
	} catch (e) {
		toast.error($i18n.t('Failed to delete export.'));
	}
};
```

Add UI section in the template, after the Files section (after line ~300):

```svelte
{#if $config?.features?.enable_data_export}
	<div>
		<div class="mb-1 text-sm font-medium">{$i18n.t('Data Export')}</div>
		<div class="text-xs text-gray-500 dark:text-gray-400 mb-2">
			{$i18n.t(
				'Download all your data including chats, notes, memories, prompts, tools, models, and locally uploaded files.'
			)}
		</div>

		<div>
			{#if exportStatus === 'none'}
				<div class="py-0.5 flex w-full justify-between">
					<div class="self-center text-xs">{$i18n.t('Download My Data')}</div>
					<button
						class="p-1 px-3 text-xs flex rounded-sm transition"
						on:click={requestDataExport}
						disabled={exportRequesting}
						type="button"
					>
						<span class="self-center">
							{#if exportRequesting}
								{$i18n.t('Starting...')}
							{:else}
								{$i18n.t('Export')}
							{/if}
						</span>
					</button>
				</div>
			{:else if exportStatus === 'processing'}
				<div class="py-0.5 flex w-full justify-between">
					<div class="self-center text-xs">{$i18n.t('Export in progress...')}</div>
					<div class="p-1 px-3 text-xs flex">
						<svg class="animate-spin h-4 w-4" viewBox="0 0 24 24">
							<circle
								class="opacity-25"
								cx="12"
								cy="12"
								r="10"
								stroke="currentColor"
								stroke-width="4"
								fill="none"
							/>
							<path
								class="opacity-75"
								fill="currentColor"
								d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
							/>
						</svg>
					</div>
				</div>
			{:else if exportStatus === 'ready'}
				<div class="py-0.5 flex w-full justify-between">
					<div class="self-center text-xs">{$i18n.t('Export ready')}</div>
					<div class="flex gap-1">
						<button
							class="p-1 px-3 text-xs flex rounded-sm transition"
							on:click={downloadDataExport}
							type="button"
						>
							<span class="self-center">{$i18n.t('Download')}</span>
						</button>
						<button
							class="p-1 px-3 text-xs flex rounded-sm transition text-red-500"
							on:click={deleteDataExport}
							type="button"
						>
							<span class="self-center">{$i18n.t('Delete')}</span>
						</button>
					</div>
				</div>
			{/if}
		</div>
	</div>
{/if}
```

### Success Criteria:

#### Automated Verification:

- [ ] `npm run build` succeeds
- [ ] No new TypeScript errors from `npm run check`

#### Manual Verification:

- [ ] "Data Export" section appears in Settings > Data Controls when `ENABLE_DATA_EXPORT=true`
- [ ] "Data Export" section hidden when `ENABLE_DATA_EXPORT=false`
- [ ] Clicking "Export" shows "Starting..." briefly, then switches to "Export in progress..." spinner
- [ ] Toast notification appears when export completes
- [ ] "Download" button downloads the ZIP file
- [ ] "Delete" button removes the export and returns to initial state
- [ ] Reopening Settings after export shows "Export ready" state (persists via status check)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding.

---

## Phase 5: i18n Translations

### Overview

Add English and Dutch translations for all new user-facing strings.

### Changes Required:

#### 1. English translations

**File**: `src/lib/i18n/locales/en-US/translation.json`
**Changes**: Add entries (alphabetically sorted, empty string = use key itself)

```json
"Data Export": "",
"Data export failed: {{error}}": "",
"Data export started. You will be notified when it is ready.": "",
"Delete": "",
"Download": "",
"Download all your data including chats, notes, memories, prompts, tools, models, and locally uploaded files.": "",
"Download My Data": "",
"Export in progress...": "",
"Export ready": "",
"Failed to delete export.": "",
"Failed to start data export.": "",
"Starting...": "",
"Your data export is ready for download.": ""
```

Note: Many of these keys may already exist (e.g., "Delete", "Download", "Export"). Only add missing ones.

#### 2. Dutch translations

**File**: `src/lib/i18n/locales/nl-NL/translation.json`
**Changes**: Add entries (alphabetically sorted)

```json
"Data Export": "Gegevensexport",
"Data export failed: {{error}}": "Gegevensexport mislukt: {{error}}",
"Data export started. You will be notified when it is ready.": "Gegevensexport gestart. U ontvangt een melding wanneer deze klaar is.",
"Download all your data including chats, notes, memories, prompts, tools, models, and locally uploaded files.": "Download al uw gegevens inclusief chats, notities, herinneringen, prompts, tools, modellen en lokaal geüploade bestanden.",
"Download My Data": "Mijn gegevens downloaden",
"Export in progress...": "Export wordt verwerkt...",
"Export ready": "Export gereed",
"Failed to delete export.": "Export verwijderen mislukt.",
"Failed to start data export.": "Gegevensexport starten mislukt.",
"Starting...": "Starten...",
"Your data export is ready for download.": "Uw gegevensexport is klaar om te downloaden."
```

### Success Criteria:

#### Automated Verification:

- [ ] `npm run build` succeeds
- [ ] Translation JSON files are valid JSON

#### Manual Verification:

- [ ] UI shows Dutch strings when language is set to nl-NL
- [ ] All export-related text is translated

---

## Phase 6: Verify Missing Model Methods

### Overview

Verify that all model query methods used by the export service exist. Add any missing `get_*_by_user_id` methods.

### Changes Required:

The export service calls these methods — each must be verified/added:

| Method                                               | Model File            | Expected |
| ---------------------------------------------------- | --------------------- | -------- |
| `Users.get_user_by_id(user_id)`                      | `models/users.py`     | Exists   |
| `Chats.get_chats_by_user_id(user_id)`                | `models/chats.py`     | Exists   |
| `Memories.get_memories_by_user_id(user_id)`          | `models/memories.py`  | Verify   |
| `Notes.get_notes_by_user_id(user_id)`                | `models/notes.py`     | Verify   |
| `Prompts.get_prompts_by_user_id(user_id)`            | `models/prompts.py`   | Verify   |
| `Tools.get_tools_by_user_id(user_id)`                | `models/tools.py`     | Verify   |
| `Models.get_models_by_user_id(user_id)`              | `models/models.py`    | Verify   |
| `Feedbacks.get_feedbacks_by_user_id(user_id)`        | `models/feedbacks.py` | Verify   |
| `Tags.get_tags_by_user_id(user_id)`                  | `models/tags.py`      | Verify   |
| `Folders.get_folders_by_user_id(user_id)`            | `models/folders.py`   | Verify   |
| `Files.get_files_by_user_id(user_id)`                | `models/files.py`     | Verify   |
| `Knowledges.get_knowledge_bases_by_user_id(user_id)` | `models/knowledge.py` | Verify   |
| `Files.get_file_by_id(file_id)`                      | `models/files.py`     | Exists   |

During implementation, check each model file. Where a `get_*_by_user_id` method doesn't exist, add it following the pattern of existing query methods in that file. These are simple `SELECT WHERE user_id = :user_id` queries.

### Success Criteria:

#### Automated Verification:

- [ ] All model methods called by `ExportService.collect_user_data()` exist and return data
- [ ] Backend starts without errors

---

## Testing Strategy

### Unit Tests:

- `ExportService.collect_user_data()` returns expected structure for a user with data
- `ExportService.build_export_zip()` produces a valid ZIP with correct structure
- `ExportService._get_local_file_ids()` correctly filters out cloud-synced files
- `ExportService.cleanup_expired_exports()` deletes old files, keeps fresh ones
- API endpoints return correct status codes when feature is disabled

### Integration Tests:

- Full flow: trigger export → wait for completion → download ZIP → verify contents
- Export for user with no data produces valid (minimal) ZIP
- Export for user with cloud KBs only includes metadata, not files

### Manual Testing Steps:

1. Create a user with chats, notes, memories, prompts, a local KB with files, and a cloud-synced KB
2. Trigger export from Settings > Data Controls
3. Verify toast notification appears when ready
4. Download and inspect ZIP contents
5. Verify cloud KB files are metadata-only
6. Verify local KB files include actual content
7. Verify export works with `ENABLE_DATA_EXPORT=true` and is blocked with `false`
8. Wait 24+ hours (or temporarily set `DATA_EXPORT_RETENTION_HOURS=0`) and verify cleanup removes old exports

## Performance Considerations

- **Large users**: Users with many chats or large files could produce large ZIPs. The background task approach handles this — the user isn't blocked.
- **Disk space**: Exports are written to `CACHE_DIR/exports/`. The 24-hour cleanup prevents accumulation. Only one export per user at a time (old ones cleaned up before new ones).
- **Memory**: Files are written to ZIP on disk (not in-memory `BytesIO`), so memory usage stays bounded regardless of export size.
- **Concurrent exports**: Each user gets their own directory. Multiple users can export simultaneously without conflicts.
- **Rate limiting**: The "one active export" check prevents spam — if an export exists, the user is told to download or delete it first.

## Migration Notes

No database migration needed — this feature uses the filesystem (`CACHE_DIR`) and existing database models. The only new state is the ZIP file on disk, which is ephemeral.

## References

- Research: `thoughts/shared/research/2026-03-31-user-data-export-current-state.md`
- File upload background task pattern: `backend/open_webui/routers/files.py:297-306`
- Socket.IO event emitter pattern: `backend/open_webui/services/files/events.py`
- Cache file serving: `backend/open_webui/main.py:2919-2930`
- Feature flag pattern: `backend/open_webui/config.py:1679` (`ENABLE_ADMIN_EXPORT`)
- DataControls UI: `src/lib/components/chat/Settings/DataControls.svelte`
- Periodic cleanup pattern: `backend/open_webui/main.py:699-712`
