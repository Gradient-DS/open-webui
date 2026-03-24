"""OneDrive Background Sync Scheduler.

Instantiates a generic SyncScheduler with OneDrive config.
Exposes start_scheduler/stop_scheduler for main.py lifespan compatibility.
"""

from open_webui.services.sync.scheduler import SyncScheduler
from open_webui.config import (
    ENABLE_ONEDRIVE_SYNC,
    ONEDRIVE_SYNC_INTERVAL_MINUTES,
)

_scheduler = SyncScheduler(
    provider_type="onedrive",
    meta_key="onedrive_sync",
    enable_config=ENABLE_ONEDRIVE_SYNC,
    interval_config=ONEDRIVE_SYNC_INTERVAL_MINUTES,
)

start_scheduler = _scheduler.start
stop_scheduler = _scheduler.stop
