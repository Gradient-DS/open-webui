"""OneDrive Background Sync Scheduler.

Instantiates a generic SyncScheduler with OneDrive config.
Exposes start_scheduler/stop_scheduler for main.py lifespan compatibility.
"""

from open_webui.services.sync.scheduler import SyncScheduler

_scheduler = SyncScheduler(
    provider_type='onedrive',
    meta_key='onedrive_sync',
    enable_key='onedrive.enable_sync',
    interval_key='onedrive.sync_interval_minutes',
)

start_scheduler = _scheduler.start
stop_scheduler = _scheduler.stop
