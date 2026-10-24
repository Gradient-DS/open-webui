import asyncio
import copy
import logging
from contextlib import asynccontextmanager, nullcontext
from types import SimpleNamespace

from fastapi import HTTPException

from open_webui.models.assistant_share_grants import AssistantShareGrants
from open_webui.soev import acting, assistant_files, folder_sharing, knowledge_sharing, pending_sync
from open_webui.soev.availability import configured

log = logging.getLogger(__name__)


def entries(model) -> list:
    return model.meta.knowledge or []


def check_edit(user_id: str, model, form) -> None:
    if not configured() or user_id == model.user_id:
        return
    if 'knowledge' not in form.meta.model_fields_set:
        form.meta.knowledge = model.meta.knowledge
    grants = form.access_grants
    current = [
        {'principal_type': g.principal_type, 'principal_id': g.principal_id, 'permission': g.permission}
        for g in model.access_grants
    ]

    def canonical(rows):
        return sorted((g['principal_type'], g['principal_id'], g['permission']) for g in rows)

    if (grants is not None and canonical(grants) != canonical(current)) or entries(form) != entries(model):
        raise HTTPException(403, 'Access prohibited')
    form.access_grants = None


async def remote_cleanup_needed(model) -> bool:
    if any(row.assistant_id == model.id for row in await AssistantShareGrants.get_by_owner(model.user_id)):
        return True
    from open_webui.soev import ingest

    return await knowledge_sharing.remote_cleanup_needed(ingest.assistant_collection_key(model.id))


@asynccontextmanager
async def changing(owner_id: str, model_id: str, planned):
    if not configured():
        yield
        return
    acting.require_owner(owner_id)
    async with knowledge_sharing._locks.setdefault(owner_id, asyncio.Lock()):
        folders = await folder_sharing.view(owner_id)
        before = await knowledge_sharing.assistants_view(owner_id)
        after = copy.deepcopy(before)
        if planned is None:
            after.pop(model_id, None)
        else:
            after[model_id] = planned
        await knowledge_sharing.reconcile(owner_id, folders, after, widen=False)
        await assistant_files.narrow(model_id, planned)
        try:
            yield
        finally:
            try:
                await knowledge_sharing.reconcile(
                    owner_id, folders, await knowledge_sharing.assistants_view(owner_id), widen=True
                )
                await pending_sync.run(owner_id, 'assistant_files', model_id)
            except Exception as error:
                log.warning('Assistant knowledge sync failed (%s)', type(error).__name__)


def change(user_id: str, model_id: str, form=None, before=None):
    owner_id = before.user_id if before is not None else user_id
    if not configured() or owner_id != user_id:
        return nullcontext()
    planned = (
        None
        if form is None
        else SimpleNamespace(
            id=model_id,
            user_id=owner_id,
            meta=form.meta,
            access_grants=(
                form.access_grants
                if form.access_grants is not None
                else before.access_grants
                if before is not None
                else []
            ),
        )
    )
    return changing(owner_id, model_id, planned)


@asynccontextmanager
async def changing_all(owner_id: str, after: dict):
    acting.require_owner(owner_id)
    async with knowledge_sharing._locks.setdefault(owner_id, asyncio.Lock()):
        folders = await folder_sharing.view(owner_id)
        before = await knowledge_sharing.assistants_view(owner_id)
        await knowledge_sharing.reconcile(owner_id, folders, after, widen=False)
        for model_id in before:
            await assistant_files.narrow(model_id, after.get(model_id))
        try:
            yield
        finally:
            await knowledge_sharing.reconcile(
                owner_id, folders, await knowledge_sharing.assistants_view(owner_id), widen=True
            )
            for model_id in set(before) | set(after):
                await pending_sync.run(owner_id, 'assistant_files', model_id)


def check_import(user_id: str, data: list[dict], existing_models: dict) -> None:
    from open_webui.models.models import ModelForm

    if not configured():
        return
    for item in data:
        existing = existing_models.get(item.get('id'))
        if existing is not None and existing.user_id != user_id:
            merged = {**existing.model_dump(), **item}
            merged['meta'] = {**existing.meta.model_dump(), **(item.get('meta') or {})}
            check_edit(user_id, existing, ModelForm(**merged))
