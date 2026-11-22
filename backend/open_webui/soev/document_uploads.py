"""Admit document uploads without retaining original bytes in OWUI storage."""

import asyncio
import hashlib
import tempfile

from fastapi import HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from open_webui.constants import ERROR_MESSAGES
from open_webui.models.files import FileForm, Files
from open_webui.models.users import UserModel
from open_webui.utils.upload_guard import check_upload


async def upload(
    request: Request,
    file: UploadFile,
    *,
    file_id: str,
    name: str,
    content_type: str | None,
    extension: str,
    allowed_extensions: list[str] | None,
    metadata: dict,
    knowledge_id: str | None,
    max_size: int | str | None,
    sniff_guard: bool,
    user: UserModel,
    db: AsyncSession | None,
) -> dict:
    from open_webui.routers.files import process_uploaded_file
    from open_webui.soev.ingest import uploading

    spool = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
    try:
        digest, size, prefix = hashlib.sha256(), 0, b''
        while chunk := await file.read(64 * 1024):
            size += len(chunk)
            if max_size and size > int(max_size) * 1024 * 1024:
                raise HTTPException(413, detail=ERROR_MESSAGES.FILE_TOO_LARGE(size=f'{max_size} MB'))
            digest.update(chunk)
            if len(prefix) < 64 * 1024:
                prefix += chunk[: 64 * 1024 - len(prefix)]
            await asyncio.to_thread(spool.write, chunk)
        if sniff_guard:
            result = check_upload(prefix, name, extension, allowed_extensions or [])
            if not result.allowed:
                raise HTTPException(400, detail=ERROR_MESSAGES.DEFAULT(result.reason))
        row = await Files.insert_new_file(
            user.id,
            FileForm(
                id=file_id,
                filename=name,
                path=None,
                data={'status': 'pending'},
                meta={
                    'name': name,
                    'content_type': content_type,
                    'upload_content_type': content_type,
                    'size': size,
                    'file_hash': digest.hexdigest(),
                    'data': metadata,
                    'status': 'pending',
                },
            ),
            db=db,
        )
        with uploading(row.id, spool):
            await process_uploaded_file(
                request,
                file,
                None,
                row,
                metadata,
                user,
                db=db,
                knowledge_id=knowledge_id,
            )
        return {'status': True, **(await Files.get_file_by_id(row.id, db=db)).model_dump()}
    except BaseException as error:
        if 'row' in locals() and row is not None:
            from open_webui.soev.ingest import abort

            await asyncio.shield(abort(row, error=getattr(error, 'code', 'upload_failed')))
        raise
    finally:
        spool.close()
