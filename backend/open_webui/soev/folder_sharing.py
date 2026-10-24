"""[Gradient] Sharing a folder shares its knowledge: everyone a folder is shared with gets a read grant on the local
knowledge bases and notes in it that its owner owns, through the same access paths a direct share takes.

Files go to the folder's own collection instead (`soev/folder_files.py`). The grants are derived per owner from all
their folders. `FolderShareGrants` records the ones this added, so
unsharing removes only those and a grant the item already had stays. Narrowing runs before a folder change commits
and refuses it when it fails; widening runs after (spec D7). Cloud knowledge bases are never shared (D5), and items
of other owners are not shared either: only an item's owner may share it (D4).
"""

import asyncio
import copy
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from open_webui.models.access_grants import AccessGrants
from open_webui.models.folders import Folders
from open_webui.soev import acting, folder_files, knowledge_sharing, pending_sync
from open_webui.soev.availability import configured
from open_webui.soev.client import SoevApiError

log = logging.getLogger(__name__)

# A folder entry's type, and the resource type its grants have.
SHARED = knowledge_sharing.SHARED
PUBLIC_PERMISSION = {'knowledge': 'sharing.public_knowledge', 'note': 'sharing.public_notes'}

Principal = tuple[str, str]
Resource = tuple[str, str]

_locks = knowledge_sharing._locks
permitted = knowledge_sharing.permitted
left_out = knowledge_sharing.left_out


@dataclass
class View:
    """An owner's folders: each one's parent, `data.files` entries and access grants."""

    parents: dict[str, str | None]
    files: dict[str, list]
    grants: dict[str, list[dict]]

    def drop(self, folder_ids: list[str]) -> None:
        for folder_id in folder_ids:
            self.parents.pop(folder_id, None)
            self.files.pop(folder_id, None)
            self.grants.pop(folder_id, None)


async def view(owner_id: str) -> View:
    folders = await Folders.get_folders_by_user_id(owner_id)
    grants = await AccessGrants.get_grants_by_resources('folder', [folder.id for folder in folders])
    return View(
        parents={folder.id: folder.parent_id for folder in folders},
        files={folder.id: list((folder.data or {}).get('files') or []) for folder in folders},
        grants={folder_id: [grant.model_dump() for grant in rows] for folder_id, rows in grants.items()},
    )


def audience(folders: View, folder_id: str, owner_id: str) -> set[Principal]:
    """Who reads the folder through a grant on it or on an ancestor of the same owner (`has_folder_access`); a
    write grant counts as read."""
    principals, seen, current = set(), set(), folder_id
    while current in folders.parents and current not in seen:
        seen.add(current)
        for grant in folders.grants.get(current, []):
            if (
                grant.get('principal_type') in ('user', 'group')
                and grant.get('principal_id')
                and grant.get('permission') in ('read', 'write')
            ):
                principals.add((grant['principal_type'], grant['principal_id']))
        current = folders.parents[current]
    principals.discard(('user', owner_id))
    return principals


def desired(folders: View, owner_id: str) -> dict[Resource, set[Principal]]:
    """Each knowledge base and note in the owner's folders, with the audiences of the folders holding it."""
    wanted: dict[Resource, set[Principal]] = {}
    for folder_id, entries in folders.files.items():
        principals = audience(folders, folder_id, owner_id)
        if not principals:
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get('type') in SHARED and isinstance(entry.get('id'), str):
                wanted.setdefault((SHARED[entry['type']], entry['id']), set()).update(principals)
    return wanted


async def reconcile(owner_id: str, folders: View, *, widen: bool) -> None:
    await knowledge_sharing.reconcile(owner_id, folders, await knowledge_sharing.assistants_view(owner_id), widen=widen)


def changed(before: View, after: View, owner_id: str) -> list[str]:
    """The folders holding files on either side whose audience or files differ, gone and new ones included."""
    folders = []
    for folder_id in sorted(set(before.parents) | set(after.parents)):
        files = folder_files.file_ids(before.files.get(folder_id)), folder_files.file_ids(after.files.get(folder_id))
        audiences = audience(before, folder_id, owner_id), audience(after, folder_id, owner_id)
        if any(files) and (files[0] != files[1] or audiences[0] != audiences[1]):
            folders.append(folder_id)
    return folders


async def _widen(owner_id: str, before: View) -> None:
    try:
        after = await view(owner_id)
        for folder_id in changed(before, after, owner_id):
            await pending_sync.run(owner_id, 'folder_files', folder_id)
        await reconcile(owner_id, after, widen=True)
    except Exception as error:
        code = error.code if isinstance(error, SoevApiError) else type(error).__name__
        log.warning('Folder knowledge of %s not shared (%s)', owner_id, code)


@asynccontextmanager
async def changing(owner_id: str, edit: Callable[[View], None] | None = None):
    """Around a change to the owner's folders. `edit` applies the change to a view of them before it commits, and
    the grants it takes away are removed first (a failure refuses the change). After the block, the owner's
    items are brought in step with the folders as committed."""
    if not configured():
        yield
        return
    acting.require_owner(owner_id)
    async with _locks.setdefault(owner_id, asyncio.Lock()):
        before = await view(owner_id)
        if edit is not None:
            planned = copy.deepcopy(before)
            edit(planned)
            await reconcile(owner_id, planned, widen=False)
            for folder_id in changed(before, planned, owner_id):
                principals = audience(planned, folder_id, owner_id)
                files = folder_files.file_ids(planned.files.get(folder_id))
                await folder_files.narrow(owner_id, folder_id, principals, files)
        try:
            yield
        finally:
            await _widen(owner_id, before)


def adds_entries(before: list | None, after: list | None) -> bool:
    """Whether `after` holds a folder entry `before` lacks."""
    known = {(entry.get('type'), entry.get('id')) for entry in before or [] if isinstance(entry, dict)}
    return any(
        not isinstance(entry, dict) or (entry.get('type'), entry.get('id')) not in known for entry in after or []
    )


async def refuse_remote_delete(owner_id: str, folder_ids: list[str]) -> None:
    from fastapi import HTTPException

    from open_webui.models.folder_share_grants import FolderShareGrants
    from open_webui.soev import ingest

    folders = await view(owner_id)
    resources = set().union(*(set(knowledge_sharing.desired(folders.files.get(fid), set())) for fid in folder_ids))
    recorded = await FolderShareGrants.get_by_owner(owner_id)
    if any((row.resource_type, row.resource_id) in resources for row in recorded):
        raise HTTPException(403, 'Access prohibited')
    for folder_id in folder_ids:
        if await knowledge_sharing.remote_cleanup_needed(ingest.folder_collection_key(folder_id), with_files=True):
            raise HTTPException(403, 'Access prohibited')


async def check_delete(requester_id: str, owner_id: str, folder_ids: list[str]) -> None:
    if configured() and requester_id != owner_id:
        await refuse_remote_delete(owner_id, folder_ids)
