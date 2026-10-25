"""Relay platform download metadata and bytes to file previews."""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from urllib.parse import quote

import httpx
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask


async def proxy_download(
    context: AbstractAsyncContextManager[httpx.Response], *, filename: str, content_type: str, attachment: bool
) -> StreamingResponse:
    stack = AsyncExitStack()
    try:
        response = await stack.enter_async_context(context)
    except BaseException:
        await stack.aclose()
        raise
    disposition = (
        'inline'
        if not attachment and (content_type == 'application/pdf' or content_type.startswith('image/'))
        else 'attachment'
    )
    headers = {
        name: value
        for name, value in response.headers.items()
        if name in {'content-length', 'content-range', 'accept-ranges', 'cache-control', 'x-content-type-options'}
    }
    headers['Content-Disposition'] = f"{disposition}; filename*=UTF-8''{quote(filename, safe='')}"

    async def chunks() -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await stack.aclose()

    return StreamingResponse(
        chunks(),
        status_code=response.status_code,
        media_type=content_type,
        headers=headers,
        background=BackgroundTask(stack.aclose),
    )
