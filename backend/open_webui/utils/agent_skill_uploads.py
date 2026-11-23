"""[Gradient] Cache caller-owned assistant bundle uploads for the workspace."""

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from open_webui.soev import identity
from open_webui.soev.client import SoevApiError, SoevClient

log = logging.getLogger(__name__)
# Private process memory: shared assistant metadata must never expose another user's references.
REFERENCES: OrderedDict[tuple[str, ...], tuple[dict[str, Any], str]] = OrderedDict()
CACHE_LIMIT = 512
_upload_lock: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None
MAX_EXPANDED = 100 * 1024 * 1024


def _read_bytes(path: str, limit: int) -> bytes:
    with Path(path).open('rb') as file:
        return file.read(limit)


def _notice(name: str, language: str | None) -> str:
    return (
        f'De skill “{name}” kon niet worden geladen. Deze beurt gaat verder zonder deze skill.'
        if (language or '').lower().startswith('nl')
        else f'The skill “{name}” could not be loaded. This turn continues without it.'
    )


def forget_references(user: str, references: list[dict[str, Any]], language: str | None) -> list[str]:
    ids = {reference['id'] for reference in references}
    matches = {
        key[3]: (reference['id'], name)
        for key, (reference, name) in REFERENCES.items()
        if key[2] == user and reference['id'] in ids
    }
    names = dict(matches.values())
    for key in list(REFERENCES):
        if key[2] == user and key[3] in matches:
            del REFERENCES[key]
    return [_notice(names.get(reference['id'], reference['name']), language) for reference in references]


async def upload_reference(
    key: tuple[str, ...],
    source_id: str,
    label: str,
    bundle: Callable[[], Awaitable[bytes]],
    user: str,
    client: SoevClient,
) -> dict[str, Any] | None:
    global _upload_lock
    loop = asyncio.get_running_loop()
    if _upload_lock is None or _upload_lock[0] is not loop:
        _upload_lock = (loop, asyncio.Lock())
    async with _upload_lock[1]:
        if key in REFERENCES:
            REFERENCES.move_to_end(key)
            return REFERENCES[key][0]
        body = await bundle()
        await identity.ensure_link(user, client)
        try:
            uploaded = await client.post_bytes('/v1/chat/skills', body, as_user=user, content_type='application/zip')
        except SoevApiError as error:
            if not 400 <= error.status < 500:
                raise
            log.warning('Workspace skill refused: skill=%s status=%s code=%s', source_id, error.status, error.code)
            return None
        REFERENCES[key] = (uploaded, label)
        while len(REFERENCES) > CACHE_LIMIT:
            REFERENCES.popitem(last=False)
        return uploaded
