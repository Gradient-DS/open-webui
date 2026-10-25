"""Read grants derived together from an owner's folders and assistants."""

import asyncio
from contextlib import nullcontext

from sqlalchemy import select

from open_webui.internal.db import get_async_db_context
from open_webui.models.access_grants import AccessGrants
from open_webui.models.assistant_share_grants import AssistantShareGrants
from open_webui.models.config import Config
from open_webui.models.files import Files
from open_webui.models.folder_share_grants import FolderShareGrants
from open_webui.models.knowledge import Knowledges
from open_webui.models.models import Model, Models
from open_webui.models.notes import Notes
from open_webui.models.users import Users
from open_webui.socket.main import note_access_change
from open_webui.soev import acting
from open_webui.utils.access_control import filter_allowed_access_grants

SHARED = {'collection': 'knowledge', 'note': 'note'}
PUBLIC_PERMISSION = {'knowledge': 'sharing.public_knowledge', 'note': 'sharing.public_notes'}
Principal = tuple[str, str]
Resource = tuple[str, str]
_locks: dict[str, asyncio.Lock] = {}


def grant_dicts(grants: list) -> list[dict]:
    return [g if isinstance(g, dict) else g.model_dump() for g in grants]


def audience(grants: list[dict], owner_id: str) -> set[Principal]:
    return {
        (grant['principal_type'], grant['principal_id'])
        for grant in grants
        if grant.get('principal_type') in ('user', 'group')
        and grant.get('principal_id')
        and grant.get('permission') in ('read', 'write')
        and (grant['principal_type'], grant['principal_id']) != ('user', owner_id)
    }


def desired(entries: list | None, principals: set[Principal]) -> dict[Resource, set[Principal]]:
    return {
        (SHARED[entry['type']], entry['id']): set(principals)
        for entry in entries or []
        if isinstance(entry, dict) and entry.get('type') in SHARED and isinstance(entry.get('id'), str)
    }


async def assistants_view(owner_id: str | None) -> dict:
    async with get_async_db_context() as db:
        query = select(Model.id)
        if owner_id is not None:
            query = query.filter_by(user_id=owner_id)
        ids = (await db.execute(query)).scalars().all()
        return {model.id: model for model in await Models.get_models_by_ids(list(ids), db=db)}


async def _grants(resource: Resource, owner_id: str) -> list[dict] | None:
    """The item's grants, or None when a folder share cannot share it: gone, another owner's, or a cloud KB."""
    resource_type, resource_id = resource
    if resource_type == 'knowledge':
        knowledge = await Knowledges.get_knowledge_by_id(resource_id)
        if knowledge is None or knowledge.user_id != owner_id or knowledge.type != 'local':
            return None
        grants = knowledge.access_grants
    else:
        note = await Notes.get_note_by_id(resource_id)
        if note is None or note.user_id != owner_id:
            return None
        grants = await AccessGrants.get_grants_by_resource('note', resource_id)
    return [
        {'principal_type': grant.principal_type, 'principal_id': grant.principal_id, 'permission': grant.permission}
        for grant in grants
    ]


async def permitted(owner, resource_type: str, principals: set[Principal]) -> set[Principal]:
    """`principals` without everyone when the owner may not share this kind of item with everyone directly."""
    if ('user', '*') not in principals:
        return principals
    everyone = [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}]
    kept = await filter_allowed_access_grants(
        await Config.get('user.permissions'), owner.id, owner.role, everyone, PUBLIC_PERMISSION[resource_type]
    )
    return principals if kept else principals - {('user', '*')}


async def reconcile(owner_id: str, folders, assistants: dict, *, widen: bool) -> None:
    from open_webui.soev import folder_sharing

    acting.require_owner(owner_id)
    owner = await Users.get_user_by_id(owner_id)
    sources = {None: folder_sharing.desired(folders, owner_id)}
    sources.update(
        {
            model_id: desired(model.meta.knowledge, audience(grant_dicts(model.access_grants), owner_id))
            for model_id, model in assistants.items()
        }
    )
    recorded = {None: {}}
    for row in await FolderShareGrants.get_by_owner(owner_id):
        recorded[None].setdefault((row.resource_type, row.resource_id), set()).add(
            (row.principal_type, row.principal_id)
        )
    for row in await AssistantShareGrants.get_by_owner(owner_id):
        recorded.setdefault(row.assistant_id, {}).setdefault((row.resource_type, row.resource_id), set()).add(
            (row.principal_type, row.principal_id)
        )
    resources = set().union(*(set(items) for items in [*sources.values(), *recorded.values()]))
    for resource in sorted(resources):
        wants = {
            source: await permitted(owner, resource[0], items.get(resource, set())) if owner else set()
            for source, items in sources.items()
        }
        mine = set().union(*(items.get(resource, set()) for items in recorded.values()))
        current = await _grants(resource, owner_id)
        want = set().union(*wants.values())
        if current is not None:
            holders = {(g['principal_type'], g['principal_id']) for g in current}
            writers = {(g['principal_type'], g['principal_id']) for g in current if g['permission'] == 'write'}
            remove = mine - want - writers
            add = want - holders if widen else set()
            # Record before publishing; a failed remote write must remain revocable on the next edit.
            managed = mine | add
            for source, principals in wants.items():
                keep = principals & managed
                if source is None:
                    await FolderShareGrants.record(owner_id, *resource, keep)
                else:
                    await AssistantShareGrants.record(owner_id, source, *resource, keep)
            if remove or add:
                grants = [
                    g
                    for g in current
                    if g['permission'] != 'read' or (g['principal_type'], g['principal_id']) not in remove
                ]
                grants += [{'principal_type': pt, 'principal_id': pid, 'permission': 'read'} for pt, pid in sorted(add)]
                evicting = note_access_change(resource[1]) if resource[0] == 'note' else nullcontext()
                async with evicting:
                    await AccessGrants.set_access_grants(*resource, grants)
        for source, items in recorded.items():
            forget = items.get(resource, set()) - (wants.get(source, set()) if current is not None else set())
            if source is None:
                await FolderShareGrants.forget(*resource, forget)
            else:
                await AssistantShareGrants.forget(source, *resource, forget)


async def left_out(owner_id: str, entries: list | None) -> list[dict]:
    """The folder's items a share does not share, each with why: `cloud` (a synced knowledge base, never shared) or
    `owner` (another person's item, theirs to share). Read as the owner; items gone are skipped."""
    from open_webui.utils.access_control.files import has_access_to_file

    items = []
    for entry in entries or []:
        if not isinstance(entry, dict) or not isinstance(entry.get('id'), str):
            continue
        kind, item_id, name, reason = entry.get('type'), entry['id'], entry.get('name'), None
        if kind == 'collection':
            knowledge = await Knowledges.get_knowledge_by_id(item_id)
            if knowledge is not None:
                reason = 'cloud' if knowledge.type != 'local' else 'owner' if knowledge.user_id != owner_id else None
                name = knowledge.name
        elif kind == 'note':
            note = await Notes.get_note_by_id(item_id)
            if note is not None and (
                note.user_id == owner_id
                or await AccessGrants.has_access(
                    user_id=owner_id, resource_type='note', resource_id=item_id, permission='read'
                )
            ):
                reason, name = ('owner' if note.user_id != owner_id else None), note.title
        elif kind == 'file':
            file = await Files.get_file_by_id(item_id)
            if file is not None and (
                file.user_id == owner_id
                or await has_access_to_file(item_id, 'read', await Users.get_user_by_id(owner_id))
            ):
                reason, name = ('owner' if file.user_id != owner_id else None), name or file.filename
        if reason:
            items.append({'type': kind, 'id': item_id, 'name': name or item_id, 'reason': reason})
    return items


async def remote_cleanup_needed(key: str, *, with_files: bool = False) -> bool:
    from urllib.parse import quote

    from open_webui.soev import identity
    from open_webui.soev.client import SoevApiError

    # Internal local collections explicitly include OWUI's service reader. Inventory never asserts their owner.
    client = identity.build_client().as_service()
    path = '/v1/collections/' + quote(key, safe='')
    try:
        await client.get(path)
    except SoevApiError as error:
        if error.status == 404:
            return False
        raise
    if not with_files:
        return True
    async for _ in client.pages(path + '/documents'):
        return True
    return False
