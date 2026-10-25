"""Serve catalog originals through the requesting user's soev-api authority."""

from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from open_webui.constants import ERROR_MESSAGES
from open_webui.soev.availability import SoevNotConfigured
from open_webui.soev.client import SoevApiError
from open_webui.soev.knowledge_store import SoevKnowledgeTable


# An original the platform does not hold answers 404 (as an unreadable one does) or 409 `original_missing`.
def _no_original(error):
    return error.status == 404 or (error.status == 409 and error.code == 'original_missing')


def _api_error(error):
    detail = ERROR_MESSAGES.NOT_FOUND if error.status == 404 else error.detail
    return HTTPException(status_code=error.status, detail=detail)


def _disposition(kind, filename):
    return {'Content-Disposition': f"{kind}; filename*=UTF-8''{quote(filename, safe='')}"}


async def stream_catalog_content(id, user, *, attachment, file_name=None):
    from open_webui.models.knowledge import Knowledges

    if not isinstance(Knowledges, SoevKnowledgeTable):
        return None
    try:
        member = await Knowledges.catalog_original(id, user_id=user.id)
        if member is None:
            return None
        row, stream = member
        filename = row['filename'] or file_name or id
        # Resolve API errors before StreamingResponse sends the successful response headers.
        try:
            first = await anext(stream, b'')
        except SoevApiError as error:
            await stream.aclose()
            if not _no_original(error):
                raise
            return await _rendition_response(Knowledges, row, id, user, attachment=attachment, filename=filename)
    except SoevNotConfigured:
        raise
    except SoevApiError as error:
        raise _api_error(error) from None
    content_type = row['meta']['content_type'] or ('application/pdf' if first.startswith(b'%PDF-') else None)
    content_type = content_type or 'application/octet-stream'
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
        headers=_disposition(disposition, filename),
        background=BackgroundTask(stream.aclose),
    )


async def _rendition_response(Knowledges, row, id, user, *, attachment, filename):
    """Without an original, the retained markdown as plain text, which the browser shows and text fragments find."""
    text = await Knowledges.catalog_rendition(row['meta']['soev_collection_key'], id, user_id=user.id)
    if text is None:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    stem, dot, extension = filename.rpartition('.')
    name = (stem if dot and stem and extension.isalnum() and len(extension) <= 5 else filename) + '.md'
    return Response(
        text,
        media_type='text/plain; charset=utf-8',
        headers={**_disposition('attachment' if attachment else 'inline', name), 'X-Content-Type-Options': 'nosniff'},
    )


async def catalog_file(id, user):
    from open_webui.models.files import FileModel
    from open_webui.models.knowledge import Knowledges
    from open_webui.soev import ingest

    if not isinstance(Knowledges, SoevKnowledgeTable):
        return None
    try:
        member = await Knowledges.catalog_original(id, user_id=user.id)
        if member is None:
            return None
        row, stream = member
        await stream.aclose()
        file = FileModel(**row)
        content = await ingest.rendition_of(file, user.id)
        return file.model_copy(update={'data': {'content': content or ''}})
    except SoevNotConfigured:
        raise
    except SoevApiError as error:
        raise _api_error(error) from None
