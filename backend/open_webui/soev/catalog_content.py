"""Serve catalog originals through the requesting user's soev-api authority."""

from fastapi import HTTPException

from open_webui.constants import ERROR_MESSAGES
from open_webui.soev.availability import SoevNotConfigured
from open_webui.soev.client import SoevApiError
from open_webui.soev.downloads import proxy_download
from open_webui.soev.knowledge_store import SoevKnowledgeTable


async def stream_catalog_content(id, user, *, attachment, file_name=None, range_header=None):
    from open_webui.models.knowledge import Knowledges

    if not isinstance(Knowledges, SoevKnowledgeTable):
        return None
    try:
        member = await Knowledges.catalog_original(id, user_id=user.id, range_header=range_header)
        if member is None:
            return None
        row, context = member
        return await proxy_download(
            context,
            filename=row['filename'] or file_name or id,
            content_type=row['meta']['content_type'] or 'application/octet-stream',
            attachment=attachment,
        )
    except SoevNotConfigured:
        raise
    except SoevApiError as error:
        detail = ERROR_MESSAGES.NOT_FOUND if error.status == 404 else error.detail
        raise HTTPException(status_code=error.status, detail=detail) from None


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
        row, _ = member
        file = FileModel(**row)
        content = await ingest.rendition_of(file, user.id)
        return file.model_copy(update={'data': {'content': content or ''}})
    except SoevNotConfigured:
        raise
    except SoevApiError as error:
        detail = ERROR_MESSAGES.NOT_FOUND if error.status == 404 else error.detail
        raise HTTPException(status_code=error.status, detail=detail) from None
