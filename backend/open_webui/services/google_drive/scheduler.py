"""Google Drive Background Sync Scheduler.

Instantiates a generic SyncScheduler with Google Drive config.
Exposes start_scheduler/stop_scheduler for main.py lifespan compatibility.
"""

from open_webui.services.sync.scheduler import SyncScheduler

_scheduler = SyncScheduler(
    provider_type='google_drive',
    meta_key='google_drive_sync',
    enable_key='google_drive.enable_sync',
    interval_key='google_drive.sync_interval_minutes',
)

start_scheduler = _scheduler.start
stop_scheduler = _scheduler.stop
