"""Product inference uses the platform's roles and the requesting person's token."""

import math
from collections.abc import AsyncIterator

from fastapi import UploadFile
from fastapi.responses import StreamingResponse

from open_webui.soev import identity
from open_webui.soev.acting import user_token
from open_webui.soev.client import SoevApiError

MAX_AUDIO_BYTES = 25 * 1024 * 1024
INFERENCE_TIMEOUT = 120.0


async def speech(body: dict, user) -> StreamingResponse:
    token = await _login_token(user)
    client = identity.build_client(timeout=INFERENCE_TIMEOUT)
    stream = client.speech(body, user_access_token=token)
    response = await stream.__aenter__()

    async def audio() -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await stream.__aexit__(None, None, None)

    return StreamingResponse(
        audio(),
        media_type=response.headers.get('content-type', 'application/octet-stream'),
        headers={'Cache-Control': 'no-store'},
    )


async def _login_token(user) -> str:
    """The requesting user's own platform login token; nothing else may act for them."""
    token = await user_token(f'owui:user:{user.id}')
    if not token:
        raise SoevApiError(401, 'sign_in', 'Platform login required', 'identity:platform_login')
    return token


async def embed_memory(text: str, user) -> list[float]:
    token = await _login_token(user)
    client = identity.build_client(timeout=INFERENCE_TIMEOUT)
    result = await client.embed([text], user_access_token=token)
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
    token = await _login_token(user)
    client = identity.build_client(timeout=INFERENCE_TIMEOUT)
    result = await client.transcribe(
        audio,
        filename='dictation',
        content_type=file.content_type or 'application/octet-stream',
        language=language,
        user_access_token=token,
    )
    if not isinstance(result.get('text'), str):
        raise SoevApiError(502, 'upstream_error', 'Invalid transcription response')
    return {'text': result['text']}
