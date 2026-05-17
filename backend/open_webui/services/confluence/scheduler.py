"""Confluence Background Sync Scheduler.

Instantiates a generic SyncScheduler with Confluence config.
Exposes start_scheduler/stop_scheduler for main.py lifespan compatibility.
"""

from open_webui.services.sync.scheduler import SyncScheduler
from open_webui.config import (
    ENABLE_CONFLUENCE_SYNC,
    CONFLUENCE_SYNC_INTERVAL_MINUTES,
)

_scheduler = SyncScheduler(
    provider_type='confluence',
    meta_key='confluence_sync',
    enable_config=ENABLE_CONFLUENCE_SYNC,
    interval_config=CONFLUENCE_SYNC_INTERVAL_MINUTES,
)

start_scheduler = _scheduler.start
stop_scheduler = _scheduler.stop
