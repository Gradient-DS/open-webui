"""Live tenant configuration read with the OWUI service credential."""

import asyncio
import json
import logging
import re
from copy import deepcopy
from pathlib import Path
from time import monotonic
from typing import Any

import httpx

from open_webui import config
from open_webui.soev.client import SoevApiError, build_service_client
from open_webui.soev.policy_jobs import v2_enabled as v2_enabled

# Exact key/type inventory from soev_platform.contract.owui.settings.
_schema = json.loads(Path(__file__).with_name('product_config_schema.json').read_text())
SETTINGS: dict[str, str] = _schema['settings']
FEATURES: frozenset[str] = frozenset(_schema['features'])
DISABLED: dict[str, Any] = _schema['disabled']
TTL_SECONDS = 60
RETRY_SECONDS = 10
log = logging.getLogger(__name__)
_snapshot: dict | None = None
_expires_at = 0.0
_backend: tuple[str, str] | None = None
_lock: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None


def owns(key: str) -> bool:
    return key in SETTINGS or key in DISABLED or key == 'user.permissions'


def unavailable() -> SoevApiError:
    return SoevApiError(503, 'service_unavailable', 'Product configuration unavailable')


def _validate_overrides(overrides: dict) -> None:
    kinds = {
        'name': (str,),
        'base_model_id': (str, type(None)),
        'params': (dict,),
        'meta': (dict,),
        'is_active': (bool,),
    }
    for model_id, fields in overrides.items():
        if not model_id.strip() or not isinstance(fields, dict) or set(fields) - kinds.keys():
            raise ValueError('Invalid model override')
        if any(type(value) not in kinds[key] for key, value in fields.items()):
            raise ValueError('Invalid model override field')
        if type(fields.get('params', {}).get('system')) not in (str, type(None)):
            raise ValueError('Invalid model prompt')


def _validate(payload: Any) -> dict:
    if not isinstance(payload, dict) or payload.get('product') != 'owui':
        raise ValueError('Invalid product configuration')
    values, entitlements, unset = (payload.get(key) for key in ('values', 'entitlements', 'unset'))
    if (
        type(payload.get('revision')) is not int
        or not isinstance(values, dict)
        or not isinstance(entitlements, dict)
        or not isinstance(unset, list)
        or not all(isinstance(key, str) for key in unset)
        or not all(type(value) is bool for value in entitlements.values())
        or set(entitlements) != FEATURES
        or set(values) & set(unset)
        or set(values) | set(unset) | set(entitlements) != set(SETTINGS)
        or any(key in values for key, entitled in entitlements.items() if not entitled)
    ):
        raise ValueError('Incomplete product configuration')
    kinds: dict[str, tuple[type, ...]] = {
        'boolean': (bool,),
        'integer': (int,),
        'number': (int, float),
        'string': (str,),
        'array': (list,),
        'object': (dict,),
        'null': (type(None),),
    }
    for key, value in values.items():
        allowed = tuple(kind for part in SETTINGS[key].split('|') for kind in kinds[part])
        if type(value) not in allowed:
            raise ValueError('Invalid product setting type')
    for key in ('evaluation.feedback.reasons', 'evaluation.feedback.tags'):
        if key in values:
            _validate_feedback(values[key])
    if 'models.overrides' in values:
        _validate_overrides(values['models.overrides'])
    return payload


async def refresh() -> dict:
    global _snapshot, _expires_at, _backend, _lock
    loop = asyncio.get_running_loop()
    if _lock is None or _lock[0] is not loop:
        _lock = (loop, asyncio.Lock())
    async with _lock[1]:
        backend = (config.SOEV_API_URL, config.SOEV_API_KEY)
        if backend != _backend:
            _backend, _snapshot, _expires_at = backend, None, 0.0
        if monotonic() >= _expires_at:
            ttl = TTL_SECONDS
            try:
                _snapshot = _validate(await build_service_client().get('/v1/products/owui/config'))
            except (httpx.HTTPError, SoevApiError, ValueError):
                ttl = RETRY_SECONDS
                log.warning('Product configuration unavailable; retaining last successful snapshot')
            _expires_at = monotonic() + ttl
        if _snapshot is None:
            raise unavailable()
        return _snapshot


def _value(snapshot: dict, key: str) -> Any:
    if key in DISABLED:
        return deepcopy(DISABLED[key])
    if key == 'user.permissions':
        return _permissions(snapshot)
    if snapshot['entitlements'].get(key) is False:
        kind = SETTINGS[key]
        return {'array': [], 'object': {}, 'string': '', 'integer': 0, 'number': 0}.get(kind, False)
    if key not in snapshot['values']:
        raise unavailable()
    return deepcopy(snapshot['values'][key])


def _permissions(snapshot: dict) -> dict:
    result: dict = {}
    for key in (*SETTINGS, *DISABLED):
        if not key.startswith('permissions.'):
            continue
        current = result
        *parents, leaf = key.removeprefix('permissions.').split('.')
        for parent in parents:
            current = current.setdefault(parent, {})
        current[leaf] = _value(snapshot, key)
    return result


async def get(key: str) -> Any:
    return _value(await refresh(), key)


async def get_many(*keys: str) -> dict:
    snapshot = await refresh()
    return {key: _value(snapshot, key) for key in keys}


async def permissions() -> dict:
    return _permissions(await refresh())


def _validate_feedback(definitions: list) -> None:
    ids = set()
    for definition in definitions:
        if (
            not isinstance(definition, dict)
            or set(definition) != {'id', 'labels'}
            or not isinstance(definition['id'], str)
            or re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', definition['id']) is None
            or definition['id'] in ids
            or len(definitions) > 64
            or not isinstance(definition['labels'], dict)
            or set(definition['labels']) != {'nl', 'en'}
            or any(not isinstance(label, str) or not 1 <= len(label) <= 120 for label in definition['labels'].values())
        ):
            raise ValueError('Invalid feedback definition')
        ids.add(definition['id'])
