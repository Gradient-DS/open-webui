"""Persist a skill upload and its bundle link in one database commit."""

import asyncio
import hashlib
import os
import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select

from open_webui.constants import ERROR_MESSAGES
from open_webui.models.files import File, FileModel
from open_webui.models.skill_files import SkillFile
from open_webui.models.skills import Skill
from open_webui.storage.provider import Storage, GCSStorageProvider, AzureStorageProvider
from open_webui.utils.upload_guard import check_upload
from open_webui.soev import skill_storage


async def upload(
    file,
    *,
    file_id: str,
    metadata: dict,
    user,
    db,
    extension: str,
    allowed_extensions: list[str],
    max_size,
    content_type: str,
) -> FileModel:
    from open_webui.routers.skill_files import (
        _assert_write_access,
        _validate_skill_path,
        _is_text_content_type,
        require_skill_files,
    )

    name = os.path.basename(file.filename)
    path = metadata.get('skill_path')
    if not isinstance(path, str) or metadata.get('knowledge_id'):
        raise HTTPException(400, detail=ERROR_MESSAGES.DEFAULT('Error uploading file'))
    _validate_skill_path(path)
    await require_skill_files()
    stored_path = None
    removed = []
    result = None
    async with skill_storage.session_for(db) as session:
        try:
            # Serialize path selection/replacement and protect the skill against deletion.
            skill = await session.scalar(select(Skill).where(Skill.id == metadata['skill_id']).with_for_update())
            if skill is None:
                raise HTTPException(404, detail=ERROR_MESSAGES.NOT_FOUND)
            try:
                await _assert_write_access(skill, user, session)
            except HTTPException as error:
                if error.status_code in (401, 403):
                    raise HTTPException(404, detail=ERROR_MESSAGES.NOT_FOUND) from None
                raise
            existing = await session.scalar(
                select(SkillFile)
                .where(
                    SkillFile.skill_id == skill.id,
                    func.lower(SkillFile.path) == path.lower(),
                )
                .with_for_update()
            )
            if existing and metadata.get('skill_replace') is not True:
                raise HTTPException(409, detail=ERROR_MESSAGES.DEFAULT('Error uploading file'))
            contents = await file.read()
            if max_size and len(contents) > int(max_size) * 1024 * 1024:
                raise HTTPException(413, detail=ERROR_MESSAGES.FILE_TOO_LARGE(size=f'{max_size} MB'))
            guard = check_upload(contents, file.filename, extension, allowed_extensions or [])
            if not guard.allowed:
                raise HTTPException(400, detail=ERROR_MESSAGES.DEFAULT(guard.reason))
            data = {}
            if _is_text_content_type(content_type or '', name):
                try:
                    data['content'] = contents.decode('utf-8')
                except UnicodeDecodeError:
                    raise HTTPException(
                        400,
                        detail=ERROR_MESSAGES.DEFAULT(
                            'File content is not valid UTF-8 text and cannot be attached to a skill.'
                        ),
                    ) from None
            import io

            stored_name = f'{file_id}_{name}'
            try:
                stored_path = Storage.get_object_path(stored_name)
            except NotImplementedError:
                if isinstance(Storage, GCSStorageProvider):
                    stored_path = f'gs://{Storage.bucket_name}/{stored_name}'
                elif isinstance(Storage, AzureStorageProvider):
                    stored_path = f'{Storage.endpoint}/{Storage.container_name}/{stored_name}'
                else:
                    raise
            write = asyncio.create_task(asyncio.to_thread(Storage.upload_file, io.BytesIO(contents), stored_name, {}))
            try:
                _, stored_path = await asyncio.shield(write)
            except asyncio.CancelledError:
                _, stored_path = await write
                raise
            now = int(time.time())
            record = File(
                id=file_id,
                user_id=user.id,
                filename=name,
                path=stored_path,
                data=data,
                meta={
                    'name': name,
                    'content_type': content_type,
                    'size': len(contents),
                    'file_hash': hashlib.sha256(contents).hexdigest(),
                    'local_kind': 'skill',
                },
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            await session.flush()
            previous = existing.file_id if existing else None
            if existing:
                existing.file_id, existing.updated_at = file_id, now
            else:
                session.add(
                    SkillFile(
                        id=str(uuid4()),
                        skill_id=skill.id,
                        file_id=file_id,
                        path=path,
                        user_id=user.id,
                        created_at=now,
                        updated_at=now,
                    )
                )
            if previous:
                removed = await skill_storage.remove_unused(session, [previous])
            result = FileModel.model_validate(record)
            commit = asyncio.create_task(session.commit())
            try:
                await asyncio.shield(commit)
            except asyncio.CancelledError:
                await commit
                raise
            await skill_storage.purge_unreferenced(removed, db=session)
            return result
        except BaseException as error:
            await session.rollback()
            if stored_path:
                linked = await skill_storage.upload_linked(file_id, db=session)
                if linked:
                    await skill_storage.purge_unreferenced(removed, db=session)
                    if not isinstance(error, asyncio.CancelledError) and result is not None:
                        return result
                else:
                    await skill_storage.purge_unreferenced([(file_id, stored_path)], db=session)
            raise
