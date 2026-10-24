"""Product inference uses the platform's roles and the requesting person's token."""

import math

from fastapi import UploadFile

from open_webui.soev import identity
from open_webui.soev.client import SoevApiError

MAX_AUDIO_BYTES = 25 * 1024 * 1024
INFERENCE_TIMEOUT = 120.0


async def embed_memory(text: str, user) -> list[float]:
    client = identity.build_client(timeout=INFERENCE_TIMEOUT)
    ref = await identity.acting_ref(user, client)
    result = await client.embed([text], as_user=ref)
    try:
        [item] = result['data']
        vector = item['embedding']
        if item['index'] != 0 or not isinstance(vector, list) or not vector:
            raise ValueError
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            for value in vector
        ):
            raise ValueError
        return vector
    except (KeyError, TypeError, ValueError):
        raise SoevApiError(502, 'upstream_error', 'Invalid embedding response') from None


async def transcribe(file: UploadFile, language: str | None, user) -> dict:
    if file.size is None or file.size > MAX_AUDIO_BYTES:
        raise SoevApiError(413, 'upload_size_exceeded', 'Audio upload too large')
    audio = await file.read(MAX_AUDIO_BYTES + 1)
    if len(audio) > MAX_AUDIO_BYTES:
        raise SoevApiError(413, 'upload_size_exceeded', 'Audio upload too large')
    client = identity.build_client(timeout=INFERENCE_TIMEOUT)
    ref = await identity.acting_ref(user, client)
    result = await client.transcribe(
        audio,
        filename='dictation',
        content_type=file.content_type or 'application/octet-stream',
        language=language,
        as_user=ref,
    )
    if not isinstance(result.get('text'), str):
        raise SoevApiError(502, 'upstream_error', 'Invalid transcription response')
    return {'text': result['text']}
