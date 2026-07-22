"""Confluence Background Sync Scheduler.

Instantiates a generic SyncScheduler with Confluence config.
Exposes start_scheduler/stop_scheduler for main.py lifespan compatibility.
"""

from open_webui.services.sync.scheduler import SyncScheduler

_scheduler = SyncScheduler(
    provider_type='confluence',
    meta_key='confluence_sync',
    enable_key='confluence.enable_sync',
    interval_key='confluence.sync_interval_minutes',
)

start_scheduler = _scheduler.start
stop_scheduler = _scheduler.stop
