# Data Retention TTL System — Implementation Plan

## Overview

Add a configurable data retention/TTL system that automatically soft-deletes stale data based on user inactivity (`last_active_at`) and data staleness (`updated_at`). Disabled by default (`DATA_RETENTION_TTL_DAYS=0`) — customers opt-in via env var, Helm chart, or admin UI. Builds entirely on existing infrastructure: soft-delete patterns, `DeletionService`, cleanup worker, `ArchiveService`, and the `PersistentConfig` pipeline.

## Current State Analysis

### Existing Infrastructure (Ready to Reuse)

- **Soft-delete** on `chat` and `knowledge` tables (`deleted_at` column) — all reads filter `deleted_at.is_(None)`
- **Cleanup worker** (`services/deletion/cleanup_worker.py`) processes soft-deleted entities every 60s: KB cascade, chat cascade, orphaned files
- **`DeletionService.delete_user()`** (`services/deletion/service.py:383`) — full two-phase user cascade (soft-delete chats/KBs → hard-delete rest)
- **`ArchiveService.create_archive()`** (`services/archival/service.py:104`) — snapshots user data before deletion
- **`periodic_archive_cleanup()`** (`main.py:700`) — daily async task pattern (while True + sleep 24h)
- **`User.last_active_at`** (`models/users.py:78`) — tracked on every JWT auth, API key auth, and WebSocket heartbeat
- **`PersistentConfig`** pattern for runtime-adjustable settings with Helm → env → DB → admin UI pipeline

### What's Missing

- No `get_inactive_users()` query method on Users model
- No `get_stale_chats()` or `get_stale_knowledge()` query methods
- No TTL configuration variables
- No periodic retention cleanup task
- No admin UI for retention settings

### Key Discoveries

- `Chats.soft_delete_by_user_id()` (`models/chats.py:1616`) and `Knowledges.soft_delete_by_user_id()` (`models/knowledge.py:768`) both do bulk `UPDATE SET deleted_at = now()` and return count
- The cleanup worker already handles the expensive cascade (vector DB, storage, file cleanup) for soft-deleted entities
- `periodic_archive_cleanup()` is the exact pattern to follow — `while True` loop with `asyncio.sleep(24 * 60 * 60)` before each run
- Admin config endpoints follow GET/POST pattern in `routers/configs.py` with Pydantic form models
- Archive config UI is in `Database.svelte`, retention section is separate from archive list
- Admin role check does NOT count as "active" — only the three `update_last_active_by_id()` call sites do (JWT auth, API key auth, WebSocket heartbeat)

## Desired End State

A daily background task scans for:

1. **Inactive users** (no login for N days) → archive + full cascade delete
2. **Stale chats** (no update for N days, user still active) → soft-delete
3. **Stale knowledge bases** (no update for N days, user still active, local type only) → soft-delete

All thresholds are configurable via env vars, Helm values, and admin UI. The system is disabled by default. When enabled, it logs all actions and respects existing archive settings.

### Verification

- Set `DATA_RETENTION_TTL_DAYS=1` in `.env`, restart, verify the periodic task runs and logs "Data retention cleanup: ..." with stats
- Create a test user, set `last_active_at` to >1 day ago via SQL, verify user is archived + deleted on next cleanup cycle
- Create a test chat, set `updated_at` to >1 day ago, verify it gets soft-deleted
- Verify admin UI shows retention config and changes persist across restarts
- Verify `0` (disabled) stops all automated cleanup

## What We're NOT Doing

- **Email warnings before deletion** — requires Graph API integration, deferred to future phase
- **Per-user TTL exemptions** — could add `ttl_exempt` flag later, out of scope
- **Audit trail table** — existing archive system covers user deletions; cleanup worker logs cover chat/KB deletions
- **Channel data TTL** — channels are collaborative, different lifecycle
- **Cloud KB TTL** — already handled by the 30-day suspension lifecycle
- **File-level TTL** — files cascade from their parent entity (chat or KB)

## Implementation Approach

Five phases, each independently testable. The backend is fully functional after Phase 3; Phase 4 adds admin UI; Phase 5 adds i18n and Helm.

---

## Phase 1: Backend Configuration

### Overview

Add 5 new `PersistentConfig` settings following the exact pattern of the archival/2FA configs.

### Changes Required

#### 1. Config definitions

**File**: `backend/open_webui/config.py`
**Location**: After the User Archival section (after line 1731)

```python
####################################
# Data Retention TTL
####################################

DATA_RETENTION_TTL_DAYS = PersistentConfig(
    'DATA_RETENTION_TTL_DAYS',
    'admin.data_retention_ttl_days',
    int(os.environ.get('DATA_RETENTION_TTL_DAYS', '0')),  # 0 = disabled
)

USER_INACTIVITY_TTL_DAYS = PersistentConfig(
    'USER_INACTIVITY_TTL_DAYS',
    'admin.user_inactivity_ttl_days',
    int(os.environ.get('USER_INACTIVITY_TTL_DAYS', '730')),  # 2 years (CNIL precedent)
)

CHAT_RETENTION_TTL_DAYS = PersistentConfig(
    'CHAT_RETENTION_TTL_DAYS',
    'admin.chat_retention_ttl_days',
    int(os.environ.get('CHAT_RETENTION_TTL_DAYS', '0')),  # 0 = inherit from master
)

KNOWLEDGE_RETENTION_TTL_DAYS = PersistentConfig(
    'KNOWLEDGE_RETENTION_TTL_DAYS',
    'admin.knowledge_retention_ttl_days',
    int(os.environ.get('KNOWLEDGE_RETENTION_TTL_DAYS', '0')),  # 0 = inherit from master
)

DATA_RETENTION_WARNING_DAYS = PersistentConfig(
    'DATA_RETENTION_WARNING_DAYS',
    'admin.data_retention_warning_days',
    int(os.environ.get('DATA_RETENTION_WARNING_DAYS', '30')),
)
```

#### 2. Import in main.py

**File**: `backend/open_webui/main.py`
**Location**: Add to the config imports (around line 384, after `TWO_FA_GRACE_PERIOD_DAYS`)

```python
    # Data Retention TTL
    DATA_RETENTION_TTL_DAYS,
    USER_INACTIVITY_TTL_DAYS,
    CHAT_RETENTION_TTL_DAYS,
    KNOWLEDGE_RETENTION_TTL_DAYS,
    DATA_RETENTION_WARNING_DAYS,
```

#### 3. Wire to app.state.config

**File**: `backend/open_webui/main.py`
**Location**: After the 2FA assignments (after line 1280)

```python
########################################
#
# DATA RETENTION TTL
#
########################################

app.state.config.DATA_RETENTION_TTL_DAYS = DATA_RETENTION_TTL_DAYS
app.state.config.USER_INACTIVITY_TTL_DAYS = USER_INACTIVITY_TTL_DAYS
app.state.config.CHAT_RETENTION_TTL_DAYS = CHAT_RETENTION_TTL_DAYS
app.state.config.KNOWLEDGE_RETENTION_TTL_DAYS = KNOWLEDGE_RETENTION_TTL_DAYS
app.state.config.DATA_RETENTION_WARNING_DAYS = DATA_RETENTION_WARNING_DAYS
```

#### 4. Expose in /api/config (for authenticated users)

**File**: `backend/open_webui/main.py`
**Location**: Inside the `features` dict in `get_app_config()`, after the `enable_2fa` line (~line 2312)

```python
                    'data_retention_ttl_days': app.state.config.DATA_RETENTION_TTL_DAYS,
```

Only expose the master TTL flag so the frontend knows retention is active. The per-entity values are admin-only.

#### 5. Helm values

**File**: `helm/open-webui-tenant/values.yaml`
**Location**: After the User Archival section (after line 391)

```yaml
# Data Retention TTL (DPIA / GDPR storage limitation)
# Set dataRetentionTtlDays > 0 to enable automated data cleanup
dataRetentionTtlDays: '0' # 0 = disabled (customers opt-in)
userInactivityTtlDays: '730' # 2 years of no login → archive + delete user
chatRetentionTtlDays: '0' # 0 = inherit from master TTL
knowledgeRetentionTtlDays: '0' # 0 = inherit from master TTL
dataRetentionWarningDays: '30' # Days before TTL to flag (future: email warning)
```

#### 6. Helm configmap

**File**: `helm/open-webui-tenant/templates/open-webui/configmap.yaml`
**Location**: Before the `{{- end }}` closing (before line 389), after the 2FA section

```yaml
# Data Retention TTL
DATA_RETENTION_TTL_DAYS: { { .Values.openWebui.config.dataRetentionTtlDays | default "0" | quote } }
USER_INACTIVITY_TTL_DAYS:
  { { .Values.openWebui.config.userInactivityTtlDays | default "730" | quote } }
CHAT_RETENTION_TTL_DAYS: { { .Values.openWebui.config.chatRetentionTtlDays | default "0" | quote } }
KNOWLEDGE_RETENTION_TTL_DAYS:
  { { .Values.openWebui.config.knowledgeRetentionTtlDays | default "0" | quote } }
DATA_RETENTION_WARNING_DAYS:
  { { .Values.openWebui.config.dataRetentionWarningDays | default "30" | quote } }
```

### Success Criteria

#### Automated Verification

- [x] Backend starts without errors: `open-webui dev`
- [ ] Config values are readable: `curl -s localhost:8080/api/config | python3 -c "import sys,json; print(json.load(sys.stdin)['features']['data_retention_ttl_days'])"` → `0`
- [x] Helm template renders: `helm template test helm/open-webui-tenant/ | grep DATA_RETENTION_TTL_DAYS`
- [x] `npm run build` succeeds

#### Manual Verification

- [ ] Set `DATA_RETENTION_TTL_DAYS=730` in `.env`, restart, verify `/api/config` returns `730`
- [ ] Change back to `0`, restart, verify it returns `0`

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding.

---

## Phase 2: Model Query Methods + TTL Resolution

### Overview

Add query methods to find inactive users, stale chats, and stale KBs. Add a helper to resolve effective TTL per entity type.

### Changes Required

#### 1. Users model — `get_inactive_users()`

**File**: `backend/open_webui/models/users.py`
**Location**: After `get_num_users_active_today()` (~line 544)

```python
def get_inactive_users(
    self,
    inactive_since: int,
    limit: int = 50,
    exclude_roles: Optional[list[str]] = None,
    db: Optional[Session] = None,
) -> list[UserModel]:
    """Find users whose last_active_at is before the given timestamp.

    Args:
        inactive_since: epoch timestamp — users active before this are inactive
        limit: max users to return per batch
        exclude_roles: roles to skip (e.g., ['admin'] to protect admin accounts)
    """
    with get_db_context(db) as db:
        query = db.query(User).filter(
            User.last_active_at < inactive_since,
        )
        if exclude_roles:
            query = query.filter(User.role.notin_(exclude_roles))
        return [
            UserModel.model_validate(user)
            for user in query.order_by(User.last_active_at.asc())
            .limit(limit)
            .all()
        ]
```

#### 2. Chats model — `get_stale_chats()`

**File**: `backend/open_webui/models/chats.py`
**Location**: After `get_pending_deletions()` (~line 1602)

```python
def get_stale_chats(
    self,
    stale_before: int,
    limit: int = 100,
    exclude_user_ids: Optional[list[str]] = None,
) -> list[ChatModel]:
    """Find non-deleted chats whose updated_at is before the given timestamp."""
    with get_db() as db:
        query = (
            db.query(Chat)
            .filter(Chat.deleted_at.is_(None))
            .filter(Chat.updated_at < stale_before)
        )
        if exclude_user_ids:
            query = query.filter(Chat.user_id.notin_(exclude_user_ids))
        return [
            ChatModel.model_validate(chat)
            for chat in query.order_by(Chat.updated_at.asc())
            .limit(limit)
            .all()
        ]
```

#### 3. Knowledge model — `get_stale_knowledge()`

**File**: `backend/open_webui/models/knowledge.py`
**Location**: After `get_pending_deletions()` (~line 754)

```python
def get_stale_knowledge(
    self,
    stale_before: int,
    limit: int = 50,
    exclude_user_ids: Optional[list[str]] = None,
) -> list[KnowledgeModel]:
    """Find non-deleted local KBs whose updated_at is before the given timestamp.
    Only targets 'local' type — cloud KBs have their own suspension lifecycle."""
    with get_db() as db:
        query = (
            db.query(Knowledge)
            .filter(Knowledge.deleted_at.is_(None))
            .filter(Knowledge.updated_at < stale_before)
            .filter(Knowledge.type == 'local')  # Cloud KBs have suspension TTL
        )
        if exclude_user_ids:
            query = query.filter(Knowledge.user_id.notin_(exclude_user_ids))
        return [
            KnowledgeModel.model_validate(kb)
            for kb in query.order_by(Knowledge.updated_at.asc())
            .limit(limit)
            .all()
        ]
```

#### 4. TTL Resolution Helper

**File**: `backend/open_webui/services/retention/__init__.py` (new file, empty)
**File**: `backend/open_webui/services/retention/config.py` (new file)

```python
"""TTL resolution logic for the data retention system."""

import time


def get_effective_ttl_days(
    master_ttl: int,
    entity_ttl: int,
) -> int:
    """Resolve effective TTL for an entity type.

    Args:
        master_ttl: DATA_RETENTION_TTL_DAYS (0 = system disabled)
        entity_ttl: per-entity override (0 = inherit master)

    Returns:
        Effective TTL in days. 0 means disabled (no cleanup).
    """
    if master_ttl <= 0:
        return 0  # System disabled
    if entity_ttl > 0:
        return entity_ttl  # Entity-specific override
    return master_ttl  # Inherit from master


def get_cutoff_timestamp(ttl_days: int) -> int:
    """Convert TTL days to a cutoff epoch timestamp.

    Returns the epoch timestamp before which data is considered stale.
    """
    return int(time.time()) - (ttl_days * 86400)


def is_retention_enabled(master_ttl: int) -> bool:
    """Check if the retention system is enabled."""
    return master_ttl > 0
```

### Success Criteria

#### Automated Verification

- [x] Backend starts without errors: `open-webui dev`
- [x] `npm run build` succeeds

#### Manual Verification

- [ ] Verify query methods work by temporarily calling them from a test script or the Python shell

**Implementation Note**: After completing this phase, pause for confirmation before proceeding.

---

## Phase 3: Retention Service + Periodic Task

### Overview

The core logic: a new `DataRetentionService` and a daily periodic task that orchestrates the three cleanup phases.

### Changes Required

#### 1. Retention Service

**File**: `backend/open_webui/services/retention/service.py` (new file)

```python
"""Data retention service — automated cleanup based on configurable TTL."""

import logging
import time
from dataclasses import dataclass, field

from open_webui.models.users import Users
from open_webui.models.chats import Chats
from open_webui.models.knowledge import Knowledges
from open_webui.services.retention.config import (
    get_effective_ttl_days,
    get_cutoff_timestamp,
    is_retention_enabled,
)

log = logging.getLogger(__name__)


@dataclass
class RetentionReport:
    users_deleted: int = 0
    users_archived: int = 0
    chats_deleted: int = 0
    knowledge_deleted: int = 0
    errors: list[str] = field(default_factory=list)


class DataRetentionService:
    """Orchestrates automated data cleanup based on TTL configuration."""

    @staticmethod
    def run_cleanup(
        master_ttl: int,
        user_inactivity_ttl: int,
        chat_ttl: int,
        knowledge_ttl: int,
        enable_archival: bool = True,
        archive_retention_days: int = 1095,
    ) -> RetentionReport:
        """Run all retention cleanup phases.

        Args:
            master_ttl: DATA_RETENTION_TTL_DAYS (0 = disabled)
            user_inactivity_ttl: USER_INACTIVITY_TTL_DAYS
            chat_ttl: CHAT_RETENTION_TTL_DAYS (0 = inherit master)
            knowledge_ttl: KNOWLEDGE_RETENTION_TTL_DAYS (0 = inherit master)
            enable_archival: whether to archive users before deletion
            archive_retention_days: retention for auto-created archives
        """
        report = RetentionReport()

        if not is_retention_enabled(master_ttl):
            return report

        # Phase 1: Inactive users
        effective_user_ttl = get_effective_ttl_days(master_ttl, user_inactivity_ttl)
        if effective_user_ttl > 0:
            DataRetentionService._cleanup_inactive_users(
                effective_user_ttl, enable_archival, archive_retention_days, report
            )

        # Collect user IDs that were just deleted to exclude from entity cleanup
        # (their data is already cascading via DeletionService)
        # No need — deleted users' chats/KBs are already soft-deleted by DeletionService

        # Phase 2: Stale chats (only for still-active users)
        effective_chat_ttl = get_effective_ttl_days(master_ttl, chat_ttl)
        if effective_chat_ttl > 0:
            DataRetentionService._cleanup_stale_chats(effective_chat_ttl, report)

        # Phase 3: Stale knowledge bases (only local type, active users)
        effective_kb_ttl = get_effective_ttl_days(master_ttl, knowledge_ttl)
        if effective_kb_ttl > 0:
            DataRetentionService._cleanup_stale_knowledge(effective_kb_ttl, report)

        return report

    @staticmethod
    def _cleanup_inactive_users(
        ttl_days: int,
        enable_archival: bool,
        archive_retention_days: int,
        report: RetentionReport,
    ) -> None:
        """Phase 1: Find and delete inactive users."""
        from open_webui.services.deletion.service import DeletionService
        from open_webui.services.archival.service import ArchiveService

        cutoff = get_cutoff_timestamp(ttl_days)
        users = Users.get_inactive_users(
            inactive_since=cutoff,
            limit=50,
            exclude_roles=['admin'],  # Never auto-delete admins
        )

        for user in users:
            try:
                # Archive before deletion if enabled
                if enable_archival:
                    try:
                        result = ArchiveService.create_archive(
                            user_id=user.id,
                            archived_by='system:retention',
                            reason=f'Automated retention cleanup — user inactive for {ttl_days}+ days',
                            retention_days=archive_retention_days,
                        )
                        if result.success:
                            report.users_archived += 1
                            log.info(
                                f'Retention: archived user {user.id} ({user.email}) '
                                f'before deletion (inactive since {user.last_active_at})'
                            )
                    except Exception as e:
                        log.warning(
                            f'Retention: failed to archive user {user.id}, '
                            f'proceeding with deletion: {e}'
                        )

                # Delete user (cascade: soft-delete chats/KBs, hard-delete rest)
                deletion_report = DeletionService.delete_user(user.id)
                if deletion_report.success:
                    report.users_deleted += 1
                    log.info(
                        f'Retention: deleted inactive user {user.id} ({user.email}) '
                        f'— last active: {user.last_active_at}'
                    )
                else:
                    error_msg = (
                        f'Retention: failed to delete user {user.id}: '
                        f'{deletion_report.errors}'
                    )
                    log.error(error_msg)
                    report.errors.append(error_msg)

            except Exception as e:
                error_msg = f'Retention: error processing user {user.id}: {e}'
                log.error(error_msg)
                report.errors.append(error_msg)

    @staticmethod
    def _cleanup_stale_chats(ttl_days: int, report: RetentionReport) -> None:
        """Phase 2: Soft-delete stale chats. Cleanup worker handles cascade."""
        cutoff = get_cutoff_timestamp(ttl_days)
        stale_chats = Chats.get_stale_chats(stale_before=cutoff, limit=500)

        for chat in stale_chats:
            try:
                Chats.soft_delete_by_id(chat.id)
                report.chats_deleted += 1
            except Exception as e:
                error_msg = f'Retention: failed to soft-delete chat {chat.id}: {e}'
                log.error(error_msg)
                report.errors.append(error_msg)

        if stale_chats:
            log.info(
                f'Retention: soft-deleted {report.chats_deleted} stale chats '
                f'(older than {ttl_days} days)'
            )

    @staticmethod
    def _cleanup_stale_knowledge(ttl_days: int, report: RetentionReport) -> None:
        """Phase 3: Soft-delete stale local KBs. Cleanup worker handles cascade."""
        cutoff = get_cutoff_timestamp(ttl_days)
        stale_kbs = Knowledges.get_stale_knowledge(stale_before=cutoff, limit=50)

        for kb in stale_kbs:
            try:
                Knowledges.soft_delete_by_id(kb.id)
                report.knowledge_deleted += 1
            except Exception as e:
                error_msg = f'Retention: failed to soft-delete KB {kb.id}: {e}'
                log.error(error_msg)
                report.errors.append(error_msg)

        if stale_kbs:
            log.info(
                f'Retention: soft-deleted {report.knowledge_deleted} stale knowledge bases '
                f'(older than {ttl_days} days)'
            )
```

#### 2. Periodic Task

**File**: `backend/open_webui/main.py`
**Location**: After `periodic_archive_cleanup()` (after line 713)

```python
async def periodic_data_retention_cleanup():
    """Periodic task to enforce data retention TTL (runs daily)"""
    from open_webui.services.retention.service import DataRetentionService

    while True:
        try:
            # Wait 24 hours before first run and between runs
            await asyncio.sleep(24 * 60 * 60)

            master_ttl = app.state.config.DATA_RETENTION_TTL_DAYS
            if master_ttl <= 0:
                continue  # Retention disabled, skip

            report = DataRetentionService.run_cleanup(
                master_ttl=master_ttl,
                user_inactivity_ttl=app.state.config.USER_INACTIVITY_TTL_DAYS,
                chat_ttl=app.state.config.CHAT_RETENTION_TTL_DAYS,
                knowledge_ttl=app.state.config.KNOWLEDGE_RETENTION_TTL_DAYS,
                enable_archival=app.state.config.ENABLE_USER_ARCHIVAL,
                archive_retention_days=app.state.config.DEFAULT_ARCHIVE_RETENTION_DAYS,
            )

            total = (
                report.users_deleted + report.chats_deleted + report.knowledge_deleted
            )
            if total > 0 or report.errors:
                log.info(
                    f'Data retention cleanup: {report.users_deleted} users deleted '
                    f'({report.users_archived} archived), '
                    f'{report.chats_deleted} chats soft-deleted, '
                    f'{report.knowledge_deleted} KBs soft-deleted, '
                    f'{len(report.errors)} errors'
                )
        except Exception as e:
            log.error(f'Error in data retention cleanup: {e}')
```

#### 3. Start the periodic task in lifespan

**File**: `backend/open_webui/main.py`
**Location**: After `asyncio.create_task(periodic_archive_cleanup())` (after line 766)

```python
    asyncio.create_task(periodic_data_retention_cleanup())
```

### Success Criteria

#### Automated Verification

- [x] Backend starts without errors: `open-webui dev`
- [x] `npm run build` succeeds
- [x] Grep confirms the task is created: `grep -n "periodic_data_retention_cleanup" backend/open_webui/main.py`

#### Manual Verification

- [ ] Set `DATA_RETENTION_TTL_DAYS=1` in `.env` and temporarily reduce the sleep to 10 seconds for testing
- [ ] Create a test user, manually set `last_active_at` to >1 day ago
- [ ] Verify the user gets archived (if `ENABLE_USER_ARCHIVAL=true`) and deleted
- [ ] Create a stale chat (set `updated_at` to >1 day ago), verify it gets soft-deleted
- [ ] Check logs for "Data retention cleanup:" messages
- [ ] Restore the 24-hour sleep interval

**Implementation Note**: After completing this phase and all verification passes, pause here for confirmation. The system is now functionally complete. Phases 4-5 add admin UI and polish.

---

## Phase 4: Admin UI — Data Retention Config

### Overview

Add a "Data Retention" section to the Database admin tab with a config endpoint and Svelte UI.

### Changes Required

#### 1. Backend config endpoint

**File**: `backend/open_webui/routers/configs.py`
**Location**: After the 2FA config section (after line 916)

```python
####################################
# Data Retention Config
####################################


class DataRetentionConfigForm(BaseModel):
    DATA_RETENTION_TTL_DAYS: int
    USER_INACTIVITY_TTL_DAYS: int
    CHAT_RETENTION_TTL_DAYS: int
    KNOWLEDGE_RETENTION_TTL_DAYS: int
    DATA_RETENTION_WARNING_DAYS: int


@router.get('/data-retention')
async def get_data_retention_config(request: Request, user=Depends(get_admin_user)):
    return {
        'DATA_RETENTION_TTL_DAYS': request.app.state.config.DATA_RETENTION_TTL_DAYS,
        'USER_INACTIVITY_TTL_DAYS': request.app.state.config.USER_INACTIVITY_TTL_DAYS,
        'CHAT_RETENTION_TTL_DAYS': request.app.state.config.CHAT_RETENTION_TTL_DAYS,
        'KNOWLEDGE_RETENTION_TTL_DAYS': request.app.state.config.KNOWLEDGE_RETENTION_TTL_DAYS,
        'DATA_RETENTION_WARNING_DAYS': request.app.state.config.DATA_RETENTION_WARNING_DAYS,
    }


@router.post('/data-retention')
async def set_data_retention_config(
    request: Request,
    form_data: DataRetentionConfigForm,
    user=Depends(get_admin_user),
):
    request.app.state.config.DATA_RETENTION_TTL_DAYS = form_data.DATA_RETENTION_TTL_DAYS
    request.app.state.config.USER_INACTIVITY_TTL_DAYS = form_data.USER_INACTIVITY_TTL_DAYS
    request.app.state.config.CHAT_RETENTION_TTL_DAYS = form_data.CHAT_RETENTION_TTL_DAYS
    request.app.state.config.KNOWLEDGE_RETENTION_TTL_DAYS = form_data.KNOWLEDGE_RETENTION_TTL_DAYS
    request.app.state.config.DATA_RETENTION_WARNING_DAYS = form_data.DATA_RETENTION_WARNING_DAYS
    return {
        'DATA_RETENTION_TTL_DAYS': request.app.state.config.DATA_RETENTION_TTL_DAYS,
        'USER_INACTIVITY_TTL_DAYS': request.app.state.config.USER_INACTIVITY_TTL_DAYS,
        'CHAT_RETENTION_TTL_DAYS': request.app.state.config.CHAT_RETENTION_TTL_DAYS,
        'KNOWLEDGE_RETENTION_TTL_DAYS': request.app.state.config.KNOWLEDGE_RETENTION_TTL_DAYS,
        'DATA_RETENTION_WARNING_DAYS': request.app.state.config.DATA_RETENTION_WARNING_DAYS,
    }
```

#### 2. Frontend API client

**File**: `src/lib/apis/configs/index.ts`
**Location**: After the `set2FAConfig` function (after line 984)

```typescript
export const getDataRetentionConfig = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/data-retention`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const setDataRetentionConfig = async (token: string, config: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/data-retention`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify(config)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
```

#### 3. Database.svelte — Data Retention Section

**File**: `src/lib/components/admin/Settings/Database.svelte`

Add imports at the top (after the archive imports around line 18):

```typescript
import { getDataRetentionConfig, setDataRetentionConfig } from '$lib/apis/configs';
```

Add state after the archive config state (~line 36):

```typescript
let retentionConfig = {
	DATA_RETENTION_TTL_DAYS: 0,
	USER_INACTIVITY_TTL_DAYS: 730,
	CHAT_RETENTION_TTL_DAYS: 0,
	KNOWLEDGE_RETENTION_TTL_DAYS: 0,
	DATA_RETENTION_WARNING_DAYS: 30
};
```

Add load function (alongside `loadArchiveConfig`):

```typescript
const loadRetentionConfig = async () => {
	try {
		const res = await getDataRetentionConfig(localStorage.token);
		if (res) {
			retentionConfig = res;
		}
	} catch (err) {
		console.error('Failed to load retention config:', err);
	}
};

const handleSaveRetentionConfig = async () => {
	try {
		await setDataRetentionConfig(localStorage.token, retentionConfig);
		toast.success($i18n.t('Data retention settings saved'));
	} catch (err) {
		toast.error($i18n.t('Failed to save data retention settings'));
	}
};
```

Add `loadRetentionConfig()` to the `onMount` callback.

Add UI section before the User Archives section (before line 276):

```svelte
<!-- Data Retention Section -->
<hr class="border-gray-50 dark:border-gray-850/30 my-2" />

<div>
	<div class="flex items-center justify-between mb-1">
		<div class="text-sm font-medium">{$i18n.t('Data Retention')}</div>
	</div>
	<div class="text-xs text-gray-500 mb-3">
		{$i18n.t(
			'Automatically delete data after a configurable retention period. Set to 0 to disable.'
		)}
	</div>

	<div class="mb-3 space-y-2">
		<div class="flex w-full justify-between items-center">
			<div class="self-center text-xs">{$i18n.t('Master TTL (days)')}</div>
			<input
				type="number"
				class="w-20 rounded py-1 px-2 text-xs bg-gray-50 dark:bg-gray-850 dark:text-gray-300"
				bind:value={retentionConfig.DATA_RETENTION_TTL_DAYS}
				min="0"
			/>
		</div>

		{#if retentionConfig.DATA_RETENTION_TTL_DAYS > 0}
			<div class="flex w-full justify-between items-center">
				<div class="self-center text-xs">{$i18n.t('User Inactivity (days)')}</div>
				<input
					type="number"
					class="w-20 rounded py-1 px-2 text-xs bg-gray-50 dark:bg-gray-850 dark:text-gray-300"
					bind:value={retentionConfig.USER_INACTIVITY_TTL_DAYS}
					min="0"
				/>
			</div>

			<div class="flex w-full justify-between items-center">
				<div class="self-center text-xs">{$i18n.t('Chat Retention (days)')}</div>
				<input
					type="number"
					class="w-20 rounded py-1 px-2 text-xs bg-gray-50 dark:bg-gray-850 dark:text-gray-300"
					bind:value={retentionConfig.CHAT_RETENTION_TTL_DAYS}
					min="0"
				/>
			</div>

			<div class="flex w-full justify-between items-center">
				<div class="self-center text-xs">{$i18n.t('Knowledge Base Retention (days)')}</div>
				<input
					type="number"
					class="w-20 rounded py-1 px-2 text-xs bg-gray-50 dark:bg-gray-850 dark:text-gray-300"
					bind:value={retentionConfig.KNOWLEDGE_RETENTION_TTL_DAYS}
					min="0"
				/>
			</div>

			<div class="flex w-full justify-between items-center">
				<div class="self-center text-xs">{$i18n.t('Warning Period (days)')}</div>
				<input
					type="number"
					class="w-20 rounded py-1 px-2 text-xs bg-gray-50 dark:bg-gray-850 dark:text-gray-300"
					bind:value={retentionConfig.DATA_RETENTION_WARNING_DAYS}
					min="0"
				/>
			</div>

			<div class="text-xs text-gray-400 mt-1">
				{$i18n.t('Set per-entity values to 0 to inherit from Master TTL.')}
			</div>
		{/if}

		<div class="flex justify-end mt-2">
			<button
				class="px-3 py-1.5 text-xs font-medium rounded bg-emerald-600 hover:bg-emerald-700 text-white transition"
				on:click={handleSaveRetentionConfig}
			>
				{$i18n.t('Save')}
			</button>
		</div>
	</div>
</div>
```

### Success Criteria

#### Automated Verification

- [x] Backend starts without errors: `open-webui dev`
- [x] `npm run build` succeeds
- [ ] API returns config: `curl -s -H "Authorization: Bearer $TOKEN" localhost:8080/api/v1/configs/data-retention`

#### Manual Verification

- [ ] Navigate to Admin → Settings → Database
- [ ] Data Retention section visible with Master TTL input
- [ ] Set Master TTL to `730`, verify per-entity fields appear
- [ ] Save, refresh page, verify values persist
- [ ] Set Master TTL back to `0`, verify per-entity fields hide

**Implementation Note**: After completing this phase, pause for confirmation before proceeding.

---

## Phase 5: i18n + Documentation

### Overview

Add English and Dutch translations for all new UI strings.

### Changes Required

#### 1. English translations

**File**: `src/lib/i18n/locales/en-US/translation.json`

Add these keys (alphabetically sorted per convention):

```json
"Automatically delete data after a configurable retention period. Set to 0 to disable.": "",
"Chat Retention (days)": "",
"Data Retention": "",
"Data retention settings saved": "",
"Failed to save data retention settings": "",
"Knowledge Base Retention (days)": "",
"Master TTL (days)": "",
"Set per-entity values to 0 to inherit from Master TTL.": "",
"User Inactivity (days)": "",
"Warning Period (days)": ""
```

(Empty string values = key is the display text, per project convention.)

#### 2. Dutch translations

**File**: `src/lib/i18n/locales/nl-NL/translation.json`

```json
"Automatically delete data after a configurable retention period. Set to 0 to disable.": "Verwijder data automatisch na een configureerbare bewaartermijn. Stel in op 0 om uit te schakelen.",
"Chat Retention (days)": "Chatbewaartermijn (dagen)",
"Data Retention": "Dataretentie",
"Data retention settings saved": "Dataretentie-instellingen opgeslagen",
"Failed to save data retention settings": "Opslaan van dataretentie-instellingen mislukt",
"Knowledge Base Retention (days)": "Kennisbank bewaartermijn (dagen)",
"Master TTL (days)": "Algemene bewaartermijn (dagen)",
"Set per-entity values to 0 to inherit from Master TTL.": "Stel individuele waarden in op 0 om de algemene bewaartermijn over te nemen.",
"User Inactivity (days)": "Gebruikersinactiviteit (dagen)",
"Warning Period (days)": "Waarschuwingsperiode (dagen)"
```

### Success Criteria

#### Automated Verification

- [x] `npm run build` succeeds
- [x] Translation JSON is valid: `python3 -c "import json; json.load(open('src/lib/i18n/locales/en-US/translation.json'))"`
- [x] Translation JSON is valid: `python3 -c "import json; json.load(open('src/lib/i18n/locales/nl-NL/translation.json'))"`

#### Manual Verification

- [ ] Switch to Dutch locale, verify all Data Retention strings are translated
- [ ] No untranslated English strings visible in the retention section

---

## Testing Strategy

### Manual Testing Steps

1. **Disabled mode**: Set `DATA_RETENTION_TTL_DAYS=0`, verify no cleanup runs (check logs)
2. **User inactivity**: Enable retention, create test user, backdated `last_active_at`, verify archive + deletion
3. **Admin protection**: Verify admin users are never auto-deleted regardless of inactivity
4. **Chat staleness**: Create old chat (backdate `updated_at`), verify soft-delete
5. **KB staleness**: Create old local KB (backdate `updated_at`), verify soft-delete
6. **Cloud KB exclusion**: Verify OneDrive/Google Drive KBs are NOT affected by KB TTL
7. **Config persistence**: Change TTL via admin UI, restart, verify values persist
8. **Helm template**: Render template with custom values, verify env vars

### Edge Cases

- User with `last_active_at = NULL` (should be treated as inactive since account creation date, but safely skip if NULL to avoid deleting users who predate the `last_active_at` column)
- Batch limits: verify the 50/100/500 limits prevent overloading the cleanup worker
- Concurrent cleanup: verify the retention task doesn't conflict with the existing cleanup worker (it shouldn't — retention only sets `deleted_at`, worker handles cascade)

## Performance Considerations

- The retention task runs daily with batch limits (50 users, 500 chats, 50 KBs per cycle). Even on large deployments, each run processes a bounded amount of work.
- User deletion is the most expensive operation (full cascade). 50 per day is conservative.
- Chat and KB soft-deletes are single UPDATE statements. The existing cleanup worker processes the expensive cascade (vector DB, storage) at its own pace (every 60 seconds).
- The `last_active_at`, `updated_at`, and `deleted_at` columns are all indexed, so the cutoff queries are efficient.

## References

- Research document: `thoughts/shared/research/2026-03-31-data-ttl-dpia-retention-policy.md`
- Existing archive cleanup pattern: `backend/open_webui/main.py:700-713`
- Existing cleanup worker: `backend/open_webui/services/deletion/cleanup_worker.py`
- DeletionService: `backend/open_webui/services/deletion/service.py`
- ArchiveService: `backend/open_webui/services/archival/service.py`
- PersistentConfig class: `backend/open_webui/config.py:169-216`
- Admin config pattern (2FA): `backend/open_webui/routers/configs.py:883-916`
