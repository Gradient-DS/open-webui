"""TOPdesk Background Sync Scheduler.

Instantiates a generic SyncScheduler with TOPdesk config.
Exposes start_scheduler/stop_scheduler for main.py lifespan compatibility.
"""

from open_webui.services.sync.scheduler import SyncScheduler
from open_webui.config import (
    ENABLE_TOPDESK_SYNC,
    TOPDESK_SYNC_INTERVAL_MINUTES,
)

_scheduler = SyncScheduler(
    provider_type='topdesk',
    meta_key='topdesk_sync',
    enable_config=ENABLE_TOPDESK_SYNC,
    interval_config=TOPDESK_SYNC_INTERVAL_MINUTES,
)

start_scheduler = _scheduler.start
stop_scheduler = _scheduler.stop
