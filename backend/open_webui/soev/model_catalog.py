"""[Gradient] In v2 mode soev-api's catalog is the only source of chat models."""

import copy
import logging
import time
from typing import Any

from open_webui import config, env
from open_webui.soev import identity
from open_webui.soev.client import SoevApiError

log = logging.getLogger(__name__)

OWNER = 'soev'
CATALOG_TTL = 60.0
CATALOG_CACHE: dict[str, Any] = {'expires_at': 0.0, 'entries': []}


def is_v2() -> bool:
    """The agent runtime is v2 and soev-api is configured."""
    return bool(env.AGENT_API_ENABLED and env.AGENT_API_RUNTIME == 'v2' and config.SOEV_API_URL)


async def catalog() -> list[dict]:
    """The client's chat models, per process for a minute; empty while soev-api cannot say."""
    if time.monotonic() < CATALOG_CACHE['expires_at']:
        return CATALOG_CACHE['entries']
    try:
        listed = await identity.build_client().get('/v1/models')
        entries = [entry for entry in listed['data'] if isinstance(entry, dict) and entry.get('id')]
    except (SoevApiError, KeyError, TypeError):
        log.warning('Could not read the soev-api model catalog')
        return []
    CATALOG_CACHE.update(expires_at=time.monotonic() + CATALOG_TTL, entries=entries)
    return entries


def _description(description: dict | None) -> str | None:
    """English for API readers; the frontend picks the UI locale from meta.soev.description."""
    if not description:
        return None
    return description.get('en') or description.get('nl') or None


def owui_model(entry: dict) -> dict:
    """One catalog entry in Open WebUI's model shape."""
    capabilities = entry.get('capabilities') or {}
    description = entry.get('description')
    return {
        'id': entry['id'],
        'name': entry.get('label') or entry['id'],
        'object': 'model',
        'created': 0,
        'owned_by': OWNER,
        'connection_type': 'external',
        'info': {
            'meta': {
                'description': _description(description),
                # Only `supported` counts; `unknown` stays off for the UI.
                'capabilities': {'vision': capabilities.get('vision') == 'supported'},
                OWNER: {
                    'description': description,
                    'vendor': entry.get('vendor'),
                    'origin': entry.get('origin'),
                    'open_weights': entry.get('open_weights'),
                    'license': entry.get('license'),
                    'hosting': entry.get('hosting'),
                    'capabilities': capabilities,
                    'context_window': entry.get('context_window'),
                    'max_output_tokens': entry.get('max_output_tokens'),
                    'lifecycle': entry.get('lifecycle'),
                    'replaced_by': entry.get('replaced_by'),
                    'default': bool(entry.get('default')),
                },
            }
        },
    }


async def base_models() -> list[dict]:
    return [owui_model(entry) for entry in await catalog()]


def apply_catalog(models: list[dict], base: list[dict]) -> None:
    """Give each catalog model its own copy of the catalog meta, below an admin's model row."""
    pristine = {model['id']: model['info'] for model in base if model.get('owned_by') == OWNER}
    for model in models:
        info = pristine.get(model['id'])
        if info is None:
            continue
        if model.get('info') is info:
            model['info'] = copy.deepcopy(info)
            continue
        meta = model.setdefault('info', {}).get('meta') or {}
        model['info']['meta'] = meta
        meta[OWNER] = copy.deepcopy(info['meta'][OWNER])
        meta['capabilities'] = {**(meta.get('capabilities') or {}), **info['meta']['capabilities']}
        if not meta.get('description'):
            meta['description'] = info['meta']['description']


def is_unconfigured(model: dict) -> bool:
    """A catalog model whose info comes from the catalog alone, without an admin's model row."""
    return model.get('owned_by') == OWNER and not (model.get('info') or {}).get('id')
