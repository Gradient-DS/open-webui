import asyncio
import logging
import time

from fastapi import BackgroundTasks

from open_webui.models.soev_pending_sync import PendingSyncs
from open_webui.soev import acting
from open_webui.soev.availability import configured

log = logging.getLogger(__name__)
_last_checked: dict[str, float] = {}


async def run(owner_id: str, kind: str, resource_id: str) -> bool:
    from open_webui.soev import assistant_files, folder_files

    acting.require_owner(owner_id)
    sync = {'assistant_files': assistant_files.sync, 'folder_files': folder_files.sync}[kind]
    waiting = await sync(resource_id)
    if waiting:
        await PendingSyncs.mark(owner_id, kind, resource_id)
    return waiting


async def settle(owner_id: str, markers: list) -> None:
    acting.require_owner(owner_id)
    from open_webui.soev import knowledge_sharing

    async with knowledge_sharing._locks.setdefault(owner_id, asyncio.Lock()):
        for marker in markers:
            try:
                if not await run(owner_id, marker.kind, marker.resource_id):
                    await PendingSyncs.forget(marker)
            except Exception as error:
                log.warning('Pending %s sync failed (%s)', marker.kind, type(error).__name__)


async def on_request(owner_id: str, background_tasks: BackgroundTasks) -> None:
    if not configured():
        return
    acting.require_owner(owner_id)
    now = time.monotonic()
    if now - _last_checked.get(owner_id, float('-inf')) < 30:
        return
    _last_checked[owner_id] = now
    markers = await PendingSyncs.get_by_owner(owner_id)
    if markers:
        background_tasks.add_task(settle, owner_id, markers)
