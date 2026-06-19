"""Skill-file router — /api/v1/skills/id/{id}/files/{add,remove} and GET /id/{id}/files.

Mounted at the same prefix as the upstream skills router (additive, no upstream edit).
Auth follows routers/skills.py: ownership-or-AccessGrants write check + has_permission.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from open_webui.constants import ERROR_MESSAGES
from open_webui.internal.db import get_async_session
from open_webui.models.access_grants import AccessGrants
from open_webui.models.files import Files
from open_webui.models.skill_files import SkillFileListResponse, SkillFiles
from open_webui.models.skills import Skills
from open_webui.utils.auth import get_verified_user
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

router = APIRouter()

_MARKDOWN_CONTENT_TYPES = {'text/markdown', 'text/x-markdown'}
_MARKDOWN_EXTENSIONS = {'.md', '.markdown'}


def _is_markdown_file(file) -> bool:
    """Return True if the file is a markdown document.

    Accepts by content_type (text/markdown) OR by filename extension (.md/.markdown).
    """
    meta = file.meta or {}
    content_type = meta.get('content_type', '') or ''
    if content_type.split(';')[0].strip().lower() in _MARKDOWN_CONTENT_TYPES:
        return True
    filename = file.filename or ''
    return any(filename.lower().endswith(ext) for ext in _MARKDOWN_EXTENSIONS)


async def _assert_write_access(skill, user, db: AsyncSession) -> None:
    """Raise 401 if the user does not have write access to the skill.

    Mirrors the pattern in routers/skills.py:UpdateSkillById.
    """
    if (
        skill.user_id != user.id
        and not await AccessGrants.has_access(
            user_id=user.id,
            resource_type='skill',
            resource_id=skill.id,
            permission='write',
            db=db,
        )
        and user.role != 'admin'
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )


############################
# AddFileToSkill
############################


class SkillFileIdForm(BaseModel):
    file_id: str


@router.post('/id/{id}/files/add')
async def add_file_to_skill(
    id: str,
    form_data: SkillFileIdForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    skill = await Skills.get_skill_by_id(id, db=db)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

    # Ownership-or-AccessGrants write check — mirrors update_skill_by_id pattern
    await _assert_write_access(skill, user, db)

    file = await Files.get_file_by_id(form_data.file_id, db=db)
    if not file:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

    if not _is_markdown_file(file):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT('Only markdown (.md / .markdown) files may be attached to a skill.'),
        )

    # Read the file content once and store it in File.data['content'].
    # Fail fast: a file without a stored path or whose content cannot be decoded
    # must never be attached (later phases read content from the DB).
    if not file.path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT('File has no stored content to attach.'),
        )
    try:
        from open_webui.storage.provider import Storage  # deferred to avoid config-table at import time

        raw = await asyncio.to_thread(Storage.get_file, file.path)
        if isinstance(raw, (bytes, bytearray)):
            content = raw.decode('utf-8')
        else:
            content = raw
        await Files.update_file_data_by_id(form_data.file_id, {'content': content}, db=db)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(f'Could not read file content for skill attachment: {e}'),
        ) from e

    result = await SkillFiles.add_file_to_skill_by_id(id, form_data.file_id, user.id, db=db)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT('Failed to attach file to skill.'),
        )
    return result


############################
# RemoveFileFromSkill
############################


@router.post('/id/{id}/files/remove')
async def remove_file_from_skill(
    id: str,
    form_data: SkillFileIdForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    skill = await Skills.get_skill_by_id(id, db=db)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

    await _assert_write_access(skill, user, db)

    return await SkillFiles.remove_file_from_skill_by_id(id, form_data.file_id, db=db)


############################
# ListSkillFiles
############################


@router.get('/id/{id}/files', response_model=SkillFileListResponse)
async def list_skill_files(
    id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    skill = await Skills.get_skill_by_id(id, db=db)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

    # Read auth: owner, admin, or any AccessGrant read
    if (
        user.role != 'admin'
        and skill.user_id != user.id
        and not await AccessGrants.has_access(
            user_id=user.id,
            resource_type='skill',
            resource_id=skill.id,
            permission='read',
            db=db,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    return await SkillFiles.search_files_by_id(id, user.id, {}, db=db)
