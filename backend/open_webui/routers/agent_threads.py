"""[Gradient] Agent threads: download a v2 thread's trace file from soev-api as the caller.

soev-api answers only the caller's own threads; any other thread is a 404 there. The trace holds every stored
event, the assistant's instructions included, so the download is for admins, on tenants that set
`ENABLE_AGENT_TRACE_DOWNLOAD`.
"""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.responses import StreamingResponse
from open_webui import config
from open_webui.soev import identity
from open_webui.soev.availability import SoevRoute, require_soev
from open_webui.utils.auth import get_admin_user

router = APIRouter(route_class=SoevRoute, dependencies=[Depends(require_soev)])

# soev-api thread ids are URL-safe tokens; the pattern also keeps dot segments and header text out.
ThreadId = Annotated[str, Path(pattern=r'^[A-Za-z0-9_-]{1,256}$')]


@router.get('/{thread_id}/trace')
async def download_trace(thread_id: ThreadId, user=Depends(get_admin_user)) -> StreamingResponse:
    """The thread's `soev.trace/1` file from soev-api, unchanged, as `<thread_id>.trace.json`."""
    if not config.ENABLE_AGENT_TRACE_DOWNLOAD:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    client = identity.build_client(timeout=120.0)
    ref = identity.external_ref(user)
    stream = client.stream(f'/v1/chat/threads/{quote(thread_id, safe="")}/export', as_user=ref)
    try:
        first = await anext(stream, b'')
    except BaseException:
        await stream.aclose()
        raise

    async def chunks():
        try:
            yield first
            async for chunk in stream:
                yield chunk
        finally:
            await stream.aclose()

    return StreamingResponse(
        chunks(),
        media_type='application/json',
        headers={'Content-Disposition': f'attachment; filename="{thread_id}.trace.json"'},
    )
