"""TOPdesk Background Sync Scheduler.

Instantiates a generic SyncScheduler with TOPdesk config.
Exposes start_scheduler/stop_scheduler for main.py lifespan compatibility.
"""

from open_webui.services.sync.scheduler import SyncScheduler

_scheduler = SyncScheduler(
    provider_type='topdesk',
    meta_key='topdesk_sync',
    enable_key='topdesk.enable_sync',
    interval_key='topdesk.sync_interval_minutes',
)

start_scheduler = _scheduler.start
stop_scheduler = _scheduler.stop
