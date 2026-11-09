"""Use the platform catalog for chat models and the default in v2 mode."""

import copy
import logging
import time
from typing import Any

from fastapi import HTTPException

from open_webui import config, env
from open_webui.soev import capabilities
from open_webui.soev.client import SoevApiError, build_service_client

log = logging.getLogger(__name__)

OWNER = 'soev'
CATALOG_TTL = 60.0
CATALOG_CACHE: dict[str, Any] = {'expires_at': 0.0, 'entries': []}


def is_v2() -> bool:
    """The agent runtime is v2 and soev-api is configured."""
    return bool(env.AGENT_API_ENABLED and config.SOEV_API_URL)


def require_custom_functions() -> None:
    if is_v2():
        raise HTTPException(status_code=403, detail='Custom functions are disabled under v2')


async def catalog() -> list[dict]:
    """The client's chat models, per process for a minute; empty while soev-api cannot say."""
    if time.monotonic() < CATALOG_CACHE['expires_at']:
        return CATALOG_CACHE['entries']
    try:
        listed = await build_service_client().get('/v1/models')
        entries = [entry for entry in listed['data'] if isinstance(entry, dict) and entry.get('id')]
    except (SoevApiError, KeyError, TypeError):
        log.warning('Could not read the soev-api model catalog')
        return []
    CATALOG_CACHE.update(expires_at=time.monotonic() + CATALOG_TTL, entries=entries)
    return entries


def catalog_id(model: str, aliases: dict[str, str] | None = None, *, requested: str | None = None) -> str:
    """Resolve recorded endpoints using the platform's answered_model annotations or catalog facts."""
    if aliases and model in aliases:
        return catalog_id(aliases[model])
    for entry in CATALOG_CACHE['entries']:
        if model == entry['id'] or model == entry.get('endpoint_id'):
            return entry['id']
    return requested or model


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
        # Every model goes by its provider/model id, never a display label.
        'name': entry['id'],
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
    models = [owui_model(entry) for entry in await catalog()]
    if models and not await capabilities.has(capabilities.IMAGES):
        for model in models:
            model['info']['meta']['capabilities']['vision'] = False
    return models


def apply_catalog(models: list[dict], base: list[dict], names: dict[str, str] | None = None) -> None:
    """Preserve catalog capability facts under product metadata; only explicit product overrides rename models."""
    pristine = {model['id']: model['info'] for model in base if model.get('owned_by') == OWNER}
    for model in models:
        info = pristine.get(model['id'])
        if info is None:
            continue
        model['name'] = (names or {}).get(model['id'], model['id'])
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


async def is_offered(model_id: str, role: str | None) -> bool:
    """Active catalog models are available to every verified user and admin in v2 mode."""
    if not is_v2() or role not in ('user', 'admin'):
        return False
    return any(entry['id'] == model_id for entry in await catalog()) and (await model_settings(model_id)).is_active


async def is_catalog_model(model_id: str) -> bool:
    """Known catalog IDs retain product content through a transient catalog outage."""
    entries = await catalog()
    return any(entry['id'] == model_id for entry in entries or CATALOG_CACHE['entries'])


async def model_settings(model_id: str):
    from open_webui.models.models import ModelModel
    from open_webui.soev import product_config

    overrides = await product_config.get('models.overrides')
    fields = overrides.get(model_id, {})
    return ModelModel(
        id=model_id,
        user_id=OWNER,
        name=fields.get('name', model_id),
        base_model_id=fields.get('base_model_id'),
        params=fields.get('params', {}),
        meta=fields.get('meta', {}),
        is_active=fields.get('is_active', True),
        created_at=0,
        updated_at=0,
    )


async def configured_rows(rows: list, base: list[dict]) -> tuple[list, dict]:
    if not is_v2():
        return rows, {}
    settings = {model['id']: await model_settings(model['id']) for model in base if model.get('owned_by') == OWNER}
    return [row for row in rows if row.id not in settings] + list(settings.values()), settings


async def configured_model(model: dict, model_info) -> dict:
    if not is_v2():
        return model
    if model_info and not model_info.is_active:
        raise HTTPException(status_code=403, detail='Model not found')
    if await is_catalog_model(model['id']):
        model = copy.deepcopy(model)
        model['info'] = model_info.model_dump(exclude={'params'})
        apply_catalog([model], await base_models(), names={model['id']: model_info.name})
    return model


async def default_models(configured: str | None) -> str | None:
    """Product configuration owns the default selection on the platform route."""
    if not is_v2():
        return configured
    from open_webui.soev import product_config

    return await product_config.get('ui.default_models')
