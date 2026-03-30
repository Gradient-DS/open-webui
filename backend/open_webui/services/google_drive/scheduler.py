"""Google Drive Background Sync Scheduler.

Instantiates a generic SyncScheduler with Google Drive config.
Exposes start_scheduler/stop_scheduler for main.py lifespan compatibility.
"""

from open_webui.services.sync.scheduler import SyncScheduler
from open_webui.config import (
    ENABLE_GOOGLE_DRIVE_SYNC,
    GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES,
)

_scheduler = SyncScheduler(
    provider_type='google_drive',
    meta_key='google_drive_sync',
    enable_config=ENABLE_GOOGLE_DRIVE_SYNC,
    interval_config=GOOGLE_DRIVE_SYNC_INTERVAL_MINUTES,
)

start_scheduler = _scheduler.start
stop_scheduler = _scheduler.stop
