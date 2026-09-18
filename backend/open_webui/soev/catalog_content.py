"""Serve catalog originals through the requesting user's soev-api authority."""

from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from open_webui.constants import ERROR_MESSAGES
from open_webui.soev.client import SoevApiError
from open_webui.soev.knowledge_store import SoevKnowledgeTable


async def stream_catalog_content(id, user, *, attachment, file_name=None):
    from open_webui.models.knowledge import Knowledges

    if not isinstance(Knowledges, SoevKnowledgeTable):
        return None
    try:
        member = await Knowledges.catalog_original(id, user_id=user.id)
        if member is None:
            return None
        row, stream = member
        # Resolve API errors before StreamingResponse sends the successful response headers.
        first = await anext(stream, b'')
    except SoevApiError as error:
        detail = ERROR_MESSAGES.NOT_FOUND if error.status == 404 else error.detail
        raise HTTPException(status_code=error.status, detail=detail) from None
    filename = row['filename'] or file_name or id
    content_type = row['meta']['content_type'] or 'application/octet-stream'
    disposition = 'inline' if not attachment and content_type == 'application/pdf' else 'attachment'

    async def chunks():
        try:
            yield first
            async for chunk in stream:
                yield chunk
        finally:
            await stream.aclose()

    return StreamingResponse(
        chunks(),
        media_type=content_type,
        headers={'Content-Disposition': f"{disposition}; filename*=UTF-8''{quote(filename, safe='')}"},
        background=BackgroundTask(stream.aclose),
    )
