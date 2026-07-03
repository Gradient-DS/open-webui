"""Provider-agnostic shared-KB lifecycle helpers.

A *shared* KB is a single, admin-provisioned, public-read knowledge base that a
cloud-sync provider keeps in sync (Confluence today). Exactly one
live shared KB exists per provider; it is discovered by ``type`` +
``meta[<meta_key>].shared == True`` rather than by name, so an admin renaming it
does not orphan the link.

These helpers carry the provider-neutral core extracted from the original
Confluence implementation: find / provision / status / delete plus the
``is_managed_shared_kb`` predicate used to protect any shared KB from deletion
or hard-cleanup through the wrong code path. Provider-specific concerns
(selection-item shape, OAuth owner resolution, auth-mode reporting) stay in the
provider's own router and are threaded in via the parameters here.

The contract matches the Confluence semantics exactly: ``insert_new_knowledge``
with ``access_grants=[]`` (bypassing the user knowledge router's non-local-type
guards), followed by a public ``user:*:read`` grant set directly via
``AccessGrants``. An empty ``owner_id`` yields a system-owned KB (``user_id=''``)
that syncs with a global service credential.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from open_webui.models.access_grants import AccessGrants
from open_webui.models.knowledge import SUSPENSION_TTL_DAYS, KnowledgeForm, KnowledgeModel, Knowledges

log = logging.getLogger(__name__)

# Meta keys under which a managed shared KB stores its sync state. Any KB whose
# meta carries one of these with ``shared == True`` is admin-managed and must
# not be deleted/reset/hard-cleaned through the generic knowledge code paths.
SHARED_SYNC_META_KEYS = ['confluence_sync']


async def find_shared_kb(provider_type: str, meta_key: str) -> Optional[KnowledgeModel]:
    """Return the live shared KB for a provider, or None.

    Discovered by ``type == provider_type`` + ``meta[meta_key].shared == True``,
    not by name, so renaming it does not orphan the link. Soft-deleted KBs are
    skipped so a deleted-then-reprovisioned KB never shadows the live one (which
    would make status/sync target the wrong row).
    """
    for kb in await Knowledges.get_knowledge_bases_by_type(provider_type):
        if getattr(kb, 'deleted_at', None):
            continue
        # Guard against legacy/corrupt rows where meta[meta_key] is not a dict
        # (e.g. a stray string/list) — calling .get on it would raise.
        sync_info = (kb.meta or {}).get(meta_key)
        if isinstance(sync_info, dict) and sync_info.get('shared'):
            return kb
    return None


async def provision_shared_kb(
    provider_type: str,
    meta_key: str,
    name: str,
    description: str,
    owner_id: str,
    selected_items: list[dict],
    extra_meta: Optional[dict[str, Any]] = None,
    items_key: str = 'items',
) -> KnowledgeModel:
    """Create or update the single shared, public-read KB for a provider.

    Stamps ``shared`` plus ``selected_items`` (under ``items_key``) and any
    ``extra_meta`` into the KB meta, then grants ``user:*:read`` directly via
    ``AccessGrants`` — bypassing the non-local-type guards in the user
    knowledge router by not going through it, leaving those guards intact for
    normal user KBs.

    ``owner_id`` empty → system-owned KB (``user_id=''``); the worker then uses
    a global service credential. ``extra_meta`` carries provider-specific meta
    (e.g. Confluence's ``auth_mode``); ``selected_items`` is the admin's opt-in
    selection persisted for the picker to re-render on reload.

    The selection is stored under ``items_key`` (default ``'items'``).
    Confluence passes ``items_key='spaces'`` to keep its persisted meta
    byte-identical with the pre-extraction implementation.

    Reserved keys (``shared``, ``items_key``, ``sources``, ``status``) always
    win over ``extra_meta`` on both the create and update paths: ``extra_meta``
    is applied first, then the reserved keys are stamped on top, so a provider
    accidentally passing a reserved key cannot clobber the managed defaults.
    """
    extra_meta = dict(extra_meta or {})

    kb = await find_shared_kb(provider_type, meta_key)
    if kb:
        # Reassign the owner if the admin changed the setting.
        if kb.user_id != owner_id:
            await Knowledges.update_knowledge_user_id_by_id(kb.id, owner_id)
        meta = kb.meta or {}
        sync_info = meta.get(meta_key, {})
        sync_info.update(extra_meta)
        sync_info['shared'] = True
        sync_info[items_key] = selected_items
        sync_info.pop('sync_all_spaces', None)  # legacy flag — superseded by explicit selection
        meta[meta_key] = sync_info
        await Knowledges.update_knowledge_meta_by_id(kb.id, meta)
    else:
        kb = await Knowledges.insert_new_knowledge(
            owner_id,
            KnowledgeForm(
                name=name,
                description=description,
                type=provider_type,
                access_grants=[],
            ),
        )
        if not kb:
            raise RuntimeError(f'Failed to create the shared {provider_type} knowledge base.')
        sync_info = {
            **extra_meta,
            'shared': True,
            items_key: selected_items,
            'sources': [],
            'status': 'idle',
        }
        await Knowledges.update_knowledge_meta_by_id(kb.id, {meta_key: sync_info})

    # Public read grant — set directly on the model, not via the user router, so
    # the non-local-type access guards stay in force for regular KBs.
    await AccessGrants.set_access_grants(
        'knowledge',
        kb.id,
        [{'principal_type': 'user', 'principal_id': '*', 'permission': 'read'}],
    )

    log.info('Shared %s KB provisioned: %s (owner=%r)', provider_type, kb.id, owner_id or '<system>')
    return kb


async def shared_kb_status(provider_type: str, meta_key: str, items_key: str = 'items') -> dict:
    """Compose the provider-neutral shared-KB status payload.

    Returns ``provisioned`` and ``knowledge_id`` always; when a KB exists, also
    its ``status``/progress/``file_count``/``last_sync_at``/``last_result``/
    ``suspended_at``/``suspended_reason``/``days_remaining``/``owner_id`` and the
    persisted selection under ``items_key``. The provider router merges this
    with its own fields (auth mode, owner-connected, etc.).

    ``suspended_reason`` / ``days_remaining`` let the Cloud Sync tab explain a
    suspended KB (why + how long before auto-delete) and that managed shared
    KBs are never auto-deleted. ``days_remaining`` is computed from
    ``suspended_at`` + the 30-day TTL.
    """
    kb = await find_shared_kb(provider_type, meta_key)
    status: dict = {
        'provisioned': kb is not None,
        'knowledge_id': kb.id if kb else None,
    }
    if kb:
        sync_info = (kb.meta or {}).get(meta_key, {})
        suspended_at = sync_info.get('suspended_at')
        days_remaining = (
            max(0, SUSPENSION_TTL_DAYS - ((int(time.time()) - suspended_at) // 86400)) if suspended_at else None
        )
        status.update(
            {
                'owner_id': kb.user_id,
                'status': sync_info.get('status', 'idle'),
                'last_sync_at': sync_info.get('last_sync_at'),
                'last_result': sync_info.get('last_result'),
                'suspended_at': suspended_at,
                'suspended_reason': sync_info.get('suspended_reason') if suspended_at else None,
                'days_remaining': days_remaining,
                'file_count': (await Knowledges.get_file_counts_by_knowledge_ids([kb.id])).get(kb.id, 0),
                # Live progress (files done / total) — lets the Cloud Sync tab
                # show a percentage on the Sync button while a sync runs.
                'progress_current': sync_info.get('progress_current', 0),
                'progress_total': sync_info.get('progress_total', 0),
                # The admin-selected items — used to pre-fill the Cloud Sync
                # tab's checklist on reload.
                items_key: sync_info.get(items_key, []),
            }
        )
    return status


async def delete_shared_kb(provider_type: str, meta_key: str) -> Optional[str]:
    """Soft-delete the live shared KB for a provider.

    Returns the deleted KB id, or None if no shared KB was provisioned. The
    cleanup worker purges files and vectors afterwards.
    """
    kb = await find_shared_kb(provider_type, meta_key)
    if not kb:
        return None
    await Knowledges.soft_delete_by_id(kb.id)
    log.info('Shared %s KB deleted: %s', provider_type, kb.id)
    return kb.id


def is_managed_shared_kb(kb: KnowledgeModel) -> bool:
    """True if ``kb`` is an admin-managed shared KB of any sync provider.

    Checks every key in ``SHARED_SYNC_META_KEYS`` for a ``shared == True`` flag,
    so the knowledge-router deletion guard and the cleanup worker's hard-delete
    skip cover Confluence and any future shared-KB provider uniformly.

    Both call sites (``routers/knowledge.py``, ``cleanup_worker.py``) pass a
    ``KnowledgeModel``. Legacy/corrupt rows may carry a non-dict ``meta[key]``
    (e.g. a stray string), so each value is isinstance-checked before ``.get``
    to avoid raising on such rows.
    """
    meta = getattr(kb, 'meta', None) or {}
    return any(isinstance(meta.get(key), dict) and meta[key].get('shared') for key in SHARED_SYNC_META_KEYS)
