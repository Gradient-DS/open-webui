"""[Gradient] In v2 mode soev-api's catalog is the only source of chat models, the default and task completions."""

import copy
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import HTTPException
from starlette.responses import StreamingResponse

from open_webui import config, env
from open_webui.soev import identity
from open_webui.soev.client import SoevApiError

log = logging.getLogger(__name__)

OWNER = 'soev'
CATALOG_TTL = 60.0
TASK_TIMEOUT = 120.0
CATALOG_CACHE: dict[str, Any] = {'expires_at': 0.0, 'entries': []}
_TASK_FIELDS = ('temperature', 'top_p', 'response_format', 'stop')


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


async def default_models(configured: str | None) -> str | None:
    """The catalog default replaces ui.default_models in v2 mode."""
    if not is_v2():
        return configured
    return next((entry['id'] for entry in await catalog() if entry.get('default')), None)


def _content(content: Any) -> Any:
    if not isinstance(content, list):
        return content
    parts = []
    for part in content:
        if part.get('type') == 'text':
            parts.append({'type': 'text', 'text': part.get('text', '')})
        elif part.get('type') == 'image_url':
            parts.append({'type': 'image_url', 'image_url': {'url': (part.get('image_url') or {}).get('url', '')}})
    return parts


def _message(message: dict) -> dict:
    result = {'role': message['role'], 'content': _content(message.get('content'))}
    if message.get('tool_call_id'):
        result['tool_call_id'] = message['tool_call_id']
    if message.get('tool_calls'):
        result['tool_calls'] = [
            {
                'id': call['id'],
                'type': 'function',
                'function': {'name': call['function']['name'], 'arguments': call['function']['arguments']},
            }
            for call in message['tool_calls']
        ]
    return result


def task_body(form_data: dict) -> dict:
    """The task subset soev-api accepts; it picks the model, so `model` is never sent."""
    body = {'messages': [_message(message) for message in form_data['messages']]}
    body['stream'] = bool(form_data.get('stream'))
    body.update({key: form_data[key] for key in _TASK_FIELDS if form_data.get(key) is not None})
    max_tokens = form_data.get('max_tokens') or form_data.get('max_completion_tokens')
    if max_tokens:
        body['max_tokens'] = max_tokens
    return body


async def task_completion(form_data: dict, user) -> dict | StreamingResponse:
    """Run an Open WebUI task (title, tags, follow-ups, queries, ...) as the acting user."""
    client = identity.build_client(timeout=TASK_TIMEOUT)
    body = task_body(form_data)
    try:
        user_ref = await identity.acting_ref(user, client)
        if not body['stream']:
            return await client.complete_task(body, as_user=user_ref)
        stream = client.stream_task(body, as_user=user_ref)
        # Resolve refusals before StreamingResponse sends its success headers.
        first = await anext(stream, b'')
    except SoevApiError as error:
        raise HTTPException(status_code=error.status, detail=error.detail) from None

    async def passthrough() -> AsyncIterator[bytes]:
        try:
            if first:
                yield first
            async for chunk in stream:
                yield chunk
        finally:
            await stream.aclose()

    return StreamingResponse(passthrough(), media_type='text/event-stream')
