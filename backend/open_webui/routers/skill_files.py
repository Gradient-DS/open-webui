"""Skill-file router — path-aware bundle CRUD under /api/v1/skills/id/{id}/files.

A skill bundle is a virtual *path tree* of files (any type). Each file is a
backing ``File`` row paired with a virtual ``path`` (e.g. ``docs/guide.md``
or ``assets/logo.png``).

Endpoints:
  - POST   /id/{id}/files          attach an existing File at a path
                                   (``{path, file_id}``) OR inline-create
                                   (``{path, content}``)
  - PUT    /id/{id}/files          inline-edit text files (``{path, content}``)
  - POST   /id/{id}/files/move     rename a file or a folder prefix
  - POST   /id/{id}/files/remove   remove a file path or a folder prefix
  - GET    /id/{id}/files          flat list (items include ``path``,
                                   ``media_type``, ``size``)
  - GET    /id/{id}/files/content  stream raw bytes of a skill file

Mounted at the same prefix as the upstream skills router (additive, no upstream
edit). Auth follows routers/skills.py: ownership-or-AccessGrants write check +
has_permission.
"""

import asyncio
import io
import logging
import mimetypes
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from open_webui.constants import ERROR_MESSAGES
from open_webui.internal.db import get_async_session
from open_webui.models.access_grants import AccessGrants
from open_webui.models.files import FileForm, Files
from open_webui.models.skill_files import SkillFileListResponse, SkillFiles
from open_webui.models.skills import Skills
from open_webui.utils.auth import get_verified_user
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

router = APIRouter()

# Path rules (see brief). A segment is one or more of these characters; the
# full path is segments joined by '/'.
_SEGMENT_RE = re.compile(r'^[A-Za-z0-9._-]+$')
_MAX_DEPTH = 8
_MAX_PATH_LENGTH = 255


def _reject_path(reason: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=ERROR_MESSAGES.DEFAULT(f'Invalid skill file path: {reason}'),
    )


def _validate_path_segment(segment: str) -> None:
    """Validate one '/'-separated path segment, raising HTTPException(400)."""
    if segment == '':
        _reject_path('path must not contain empty segments (no leading/trailing/double "/").')
    if segment in ('.', '..'):
        _reject_path('path must not contain "." or ".." segments.')
    if not _SEGMENT_RE.match(segment):
        _reject_path('path segments may only contain letters, digits, ".", "_" and "-".')


# Known-text application/* MIME types (forwarded inline to the agent).
_KNOWN_TEXT_CONTENT_TYPES = {
    'application/json',
    'application/x-yaml',
    'text/yaml',
    'application/yaml',
    'application/javascript',
    'application/x-javascript',
    'application/xml',
    'application/x-sh',
    'application/x-python',
}

# File extensions we treat as text even when the MIME type is unknown.
_TEXT_EXTENSIONS = {
    '.md',
    '.markdown',
    '.txt',
    '.py',
    '.js',
    '.ts',
    '.jsx',
    '.tsx',
    '.json',
    '.yaml',
    '.yml',
    '.toml',
    '.ini',
    '.cfg',
    '.conf',
    '.sh',
    '.bash',
    '.zsh',
    '.html',
    '.htm',
    '.css',
    '.scss',
    '.sass',
    '.xml',
    '.csv',
    '.rst',
    '.tex',
    '.sql',
    '.r',
    '.rb',
    '.java',
    '.c',
    '.cpp',
    '.h',
    '.hpp',
    '.cs',
    '.go',
    '.rs',
    '.swift',
    '.kt',
    '.php',
    '.lua',
    '.tf',
    '.hcl',
}


def _is_text_content_type(content_type: str, filename: str) -> bool:
    """Return True if the file should be classified as text (forward content inline).

    Classification hierarchy:
    1. content_type starts with 'text/' → text
    2. content_type is in _KNOWN_TEXT_CONTENT_TYPES → text
    3. file extension is in _TEXT_EXTENSIONS → text
    4. Otherwise → binary
    """
    ct = (content_type or '').split(';')[0].strip().lower()
    if ct.startswith('text/'):
        return True
    if ct in _KNOWN_TEXT_CONTENT_TYPES:
        return True
    ext = Path(filename).suffix.lower() if filename else ''
    return ext in _TEXT_EXTENSIONS


def _validate_skill_path(path: str) -> None:
    """Validate a virtual skill-bundle path, raising HTTPException(400) on any
    violation.

    Enforces ALL of:
      - non-empty, total length <= 255
      - no leading '/'
      - segments separated by '/'; each segment matches [A-Za-z0-9._-]+
      - no empty segments (no leading/trailing/double slash)
      - no '.' or '..' segments (no traversal)
      - depth (segment count) <= 8

    Any file extension (or none) is accepted — the previous markdown-only
    restriction has been removed.
    """
    if not path or not isinstance(path, str):
        _reject_path('path must be a non-empty string.')
    if len(path) > _MAX_PATH_LENGTH:
        _reject_path(f'path exceeds {_MAX_PATH_LENGTH} characters.')
    if path.startswith('/'):
        _reject_path('path must not start with "/".')

    segments = path.split('/')
    if len(segments) > _MAX_DEPTH:
        _reject_path(f'path depth exceeds {_MAX_DEPTH}.')

    for segment in segments:
        _validate_path_segment(segment)


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


async def _get_skill_or_404(id: str, db: AsyncSession):
    skill = await Skills.get_skill_by_id(id, db=db)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    return skill


async def _read_storage_text(file) -> str:
    """Read a backing File's content as UTF-8 text.

    ``Storage.get_file`` returns a local PATH string (NOT bytes); we read that
    path and decode. Raises HTTPException(400) on missing path / unreadable /
    non-UTF-8 content so a bad file is never silently attached.
    """
    if not file.path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT('File has no stored content to attach.'),
        )
    from open_webui.storage.provider import Storage  # deferred to avoid config-table at import time

    local_path = await asyncio.to_thread(Storage.get_file, file.path)
    try:
        raw_bytes = await asyncio.to_thread(Path(local_path).read_bytes)
    except Exception as read_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(f'Could not read file content for skill attachment: {read_err}'),
        ) from read_err
    try:
        return raw_bytes.decode('utf-8')
    except (UnicodeDecodeError, ValueError) as dec_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT('File content is not valid UTF-8 text and cannot be attached to a skill.'),
        ) from dec_err


async def _create_text_file(content: str, name: str, user) -> str:
    """Write ``content`` text bytes through the Storage upload pipeline and
    create a backing ``File`` row (with ``data['content']`` populated).

    Mirrors routers/files.py upload_file_handler. Returns the new file id.
    The content_type is inferred from the filename extension (fallback: text/plain).
    """
    content_bytes = content.encode('utf-8')
    file_id = str(uuid.uuid4())
    stored_filename = f'{file_id}_{name}'

    # Derive content_type from extension; fallback to text/plain (it's a text editor path).
    guessed_type, _ = mimetypes.guess_type(name)
    content_type = guessed_type if guessed_type else 'text/plain'

    from open_webui.storage.provider import Storage  # deferred to avoid config-table at import time

    contents, file_path = await asyncio.to_thread(
        Storage.upload_file,
        io.BytesIO(content_bytes),
        stored_filename,
        {
            'OpenWebUI-User-Email': user.email,
            'OpenWebUI-User-Id': user.id,
            'OpenWebUI-User-Name': getattr(user, 'name', '') or '',
            'OpenWebUI-File-Id': file_id,
        },
    )

    file_item = await Files.insert_new_file(
        user.id,
        FileForm(
            **{
                'id': file_id,
                'filename': name,
                'path': file_path,
                'data': {'content': content},
                'meta': {
                    'name': name,
                    'content_type': content_type,
                    'size': len(contents),
                },
            }
        ),
    )
    if not file_item:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT('Failed to create skill file.'),
        )
    return file_item.id


############################
# Forms
############################


class SkillFileCreateForm(BaseModel):
    path: str
    file_id: str | None = None
    content: str | None = None


class SkillFileEditForm(BaseModel):
    path: str
    content: str


class SkillFileMoveForm(BaseModel):
    from_path: str
    to_path: str


class SkillFilePathForm(BaseModel):
    path: str


############################
# Create — POST /id/{id}/files
############################


@router.post('/id/{id}/files')
async def create_skill_file(
    id: str,
    form_data: SkillFileCreateForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    skill = await _get_skill_or_404(id, db)
    await _assert_write_access(skill, user, db)

    # md-only path validation FIRST (load-bearing 400).
    _validate_skill_path(form_data.path)

    if (form_data.file_id is None) == (form_data.content is None):
        # Either both missing or both supplied — ambiguous.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT('Provide exactly one of "file_id" (upload) or "content" (inline create).'),
        )

    # Explicit empty-content guard for the inline-create branch: an empty file is
    # not a valid markdown document and Storage.upload_file would 500 on empty bytes.
    if form_data.content is not None and not form_data.content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT('Inline-create content must not be empty.'),
        )

    # Case-insensitive collision check (DB unique is exact (skill_id, path)).
    if await SkillFiles.path_exists(id, form_data.path, db=db):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(f'A file already exists at path "{form_data.path}".'),
        )

    file_basename = form_data.path.rsplit('/', 1)[-1]

    if form_data.file_id is not None:
        # Upload an existing File at this path (any type accepted).
        file = await Files.get_file_by_id(form_data.file_id, db=db)
        if not file:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
        # For text files, cache the content in data['content'] for cheap inline reads.
        # For binary files, we skip content caching — they are served via the raw-bytes route.
        meta = file.meta or {}
        ct = (meta.get('content_type') or '').split(';')[0].strip()
        if _is_text_content_type(ct, file.filename or ''):
            content = await _read_storage_text(file)
            await Files.update_file_data_by_id(form_data.file_id, {'content': content}, db=db)
        file_id = form_data.file_id
    else:
        # Inline-create: write the content through the Storage/Files pipeline.
        file_id = await _create_text_file(form_data.content, file_basename, user)

    result = await SkillFiles.add_file_to_skill_by_id(id, file_id, form_data.path, user.id, db=db)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT('Failed to attach file to skill.'),
        )
    return result


############################
# Edit — PUT /id/{id}/files
############################


@router.put('/id/{id}/files')
async def edit_skill_file(
    id: str,
    form_data: SkillFileEditForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    skill = await _get_skill_or_404(id, db)
    await _assert_write_access(skill, user, db)

    _validate_skill_path(form_data.path)

    # Explicit empty-content guard: an empty edit is not a valid markdown document
    # and Storage.upload_file would 500 on empty bytes.
    if not form_data.content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT('Edit content must not be empty.'),
        )

    row = await SkillFiles.get_file_by_path(id, form_data.path, db=db)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

    file = await Files.get_file_by_id(row.file_id, db=db)
    if not file:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

    # Rewrite the backing File's bytes (Storage) + data['content'].
    # Last-write-wins (no optimistic concurrency control).
    file_basename = form_data.path.rsplit('/', 1)[-1]
    content_bytes = form_data.content.encode('utf-8')
    stored_filename = f'{file.id}_{file_basename}'

    from open_webui.storage.provider import Storage  # deferred to avoid config-table at import time

    _contents, file_path = await asyncio.to_thread(
        Storage.upload_file,
        io.BytesIO(content_bytes),
        stored_filename,
        {
            'OpenWebUI-User-Email': user.email,
            'OpenWebUI-User-Id': user.id,
            'OpenWebUI-User-Name': getattr(user, 'name', '') or '',
            'OpenWebUI-File-Id': file.id,
        },
    )
    await Files.update_file_path_by_id(file.id, file_path, db=db)
    await Files.update_file_data_by_id(file.id, {'content': form_data.content}, db=db)
    return {'path': form_data.path, 'file_id': file.id}


############################
# Move / rename — POST /id/{id}/files/move
############################


@router.post('/id/{id}/files/move')
async def move_skill_file(
    id: str,
    form_data: SkillFileMoveForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    skill = await _get_skill_or_404(id, db)
    await _assert_write_access(skill, user, db)

    from_path = form_data.from_path
    to_path = form_data.to_path

    # A single-file move targets an existing exact path; otherwise it is a
    # folder-prefix rename. Decide by whether an exact row exists at from_path.
    existing_row = await SkillFiles.get_file_by_path(id, from_path, db=db)

    if existing_row is not None:
        # Single-file rename — validate the md-only destination + dupe.
        _validate_skill_path(to_path)
        if await SkillFiles.path_exists(id, to_path, db=db):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT(f'A file already exists at path "{to_path}".'),
            )
        ok = await SkillFiles.move_path(id, from_path, to_path, db=db)
        if not ok:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
        return {'from_path': from_path, 'to_path': to_path}

    # Folder rename: rewrite the prefix over all rows under ``from_path`` + '/'.
    from_prefix = from_path.rstrip('/') + '/'
    to_prefix = to_path.rstrip('/') + '/'

    rows = await SkillFiles.get_files_by_skill_id(id, db=db)
    affected = [r for r in rows if r.path.startswith(from_prefix)]
    if not affected:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

    # Validate every resulting path (md-only) and guard against collisions with
    # rows that are NOT being moved.
    untouched_paths = {r.path.lower() for r in rows if not r.path.startswith(from_prefix)}
    for r in affected:
        new_path = to_prefix + r.path[len(from_prefix) :]
        _validate_skill_path(new_path)
        if new_path.lower() in untouched_paths:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT(f'Folder rename would collide with existing path "{new_path}".'),
            )

    ok = await SkillFiles.move_paths_under_prefix(id, from_prefix, to_prefix, db=db)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)
    return {'from_path': from_path, 'to_path': to_path, 'moved': len(affected)}


############################
# Remove — POST /id/{id}/files/remove
############################


@router.post('/id/{id}/files/remove')
async def remove_skill_file(
    id: str,
    form_data: SkillFilePathForm,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    skill = await _get_skill_or_404(id, db)
    await _assert_write_access(skill, user, db)

    path = form_data.path

    # A single-file removal targets an existing exact path; otherwise treat the
    # value as a folder prefix and remove everything under it.
    existing_row = await SkillFiles.get_file_by_path(id, path, db=db)
    if existing_row is not None:
        removed = await SkillFiles.remove_file_by_path(id, path, db=db)
        if removed is not None:
            await Files.delete_file_by_id(removed.file_id, db=db)
        return True

    # Folder prefix delete.
    prefix = path.rstrip('/') + '/'
    removed_rows = await SkillFiles.remove_paths_under_prefix(id, prefix, db=db)
    if removed_rows:
        await Files.delete_files_by_ids([r.file_id for r in removed_rows], db=db)
    return True


############################
# Raw bytes — GET /id/{id}/files/content
############################


@router.get('/id/{id}/files/content')
async def get_skill_file_content(
    id: str,
    path: str = Query(..., description='Virtual skill-bundle path, e.g. "assets/logo.png"'),
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    """Stream the raw bytes of a skill-bundle file with the correct Content-Type.

    Auth: same read-access check as GET /id/{id}/files (skill owner, admin, or
    any AccessGrant read permission). Mirrors routers/files.py get_file_content_by_id.
    """
    skill = await _get_skill_or_404(id, db)

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

    # Validate the path (path-safety check — load-bearing 400).
    _validate_skill_path(path)

    # Resolve the skill_file row by (skill_id, path).
    row = await SkillFiles.get_file_by_path(id, path, db=db)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

    file = await Files.get_file_by_id(row.file_id, db=db)
    if not file:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND)

    if not file.path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    from open_webui.storage.provider import Storage  # deferred to avoid config-table at import time

    try:
        local_path = await asyncio.to_thread(Storage.get_file, file.path)
    except Exception as exc:
        log.exception('Error resolving storage path for skill file: %s', exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT('Error retrieving skill file content.'),
        ) from exc

    # Determine content-type (from meta, then extension, then octet-stream).
    meta = file.meta or {}
    media_type: str = meta.get('content_type') or ''
    if not media_type:
        guessed, _ = mimetypes.guess_type(path)
        media_type = guessed or 'application/octet-stream'

    return FileResponse(local_path, media_type=media_type)


############################
# List — GET /id/{id}/files
############################


@router.get('/id/{id}/files', response_model=SkillFileListResponse)
async def list_skill_files(
    id: str,
    user=Depends(get_verified_user),
    db: AsyncSession = Depends(get_async_session),
):
    skill = await _get_skill_or_404(id, db)

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
