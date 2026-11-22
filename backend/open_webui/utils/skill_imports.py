"""[Gradient] Validate zip imports before storing the skill and its files in one transaction."""

import asyncio
import base64
import binascii
import io
import mimetypes
import stat
import time
import uuid
import zipfile
from pathlib import PurePosixPath
from typing import Any

from fastapi import HTTPException
from open_webui.models.files import File
from open_webui.models.skill_files import SkillFile
from open_webui.models.skills import Skill, SkillForm, SkillModel
from open_webui.storage.provider import Storage
from sqlalchemy.ext.asyncio import AsyncSession

MAX_COMPRESSED = 25 * 1024 * 1024
MAX_EXPANDED = 100 * 1024 * 1024


def _validate_entries(entries: list[zipfile.ZipInfo]) -> None:
    for entry in entries:
        path = entry.filename.rstrip('/') if entry.is_dir() else entry.filename
        if path.startswith('/') or '\\' in path or any(part in ('', '.', '..') for part in path.split('/')):
            raise HTTPException(400, 'Invalid skill bundle path')
        mode = entry.external_attr >> 16
        if stat.S_IFMT(mode) not in (0, stat.S_IFDIR if entry.is_dir() else stat.S_IFREG):
            raise HTTPException(422, 'Skill bundle entries must be regular files or directories')


def _bundle_root(entries: list[zipfile.ZipInfo]) -> str:
    names = [entry.filename for entry in entries if not entry.is_dir()]
    if 'SKILL.md' in names:
        return ''
    folders = {name.split('/')[0] for name in names}
    if len(folders) != 1 or f'{next(iter(folders))}/SKILL.md' not in names:
        raise HTTPException(422, 'Skill bundle needs SKILL.md at its root or in one top folder')
    return next(iter(folders)) + '/'


def read_bundle(encoded: str) -> tuple[str, list[tuple[str, bytes]]]:
    from open_webui.routers.skill_files import _validate_skill_path

    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(422, 'Invalid skill bundle') from None
    if len(raw) > MAX_COMPRESSED:
        raise HTTPException(413, 'Skill bundle too large')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > 500 or sum(entry.file_size for entry in entries) > MAX_EXPANDED:
                raise HTTPException(413, 'Skill bundle too large')
            _validate_entries(entries)
            root = _bundle_root(entries)
            files, seen = [], set()
            content = ''
            for entry in entries:
                if entry.is_dir():
                    continue
                path = entry.filename.removeprefix(root)
                _validate_skill_path(path)
                if path.casefold() in seen:
                    raise HTTPException(422, 'Duplicate skill bundle path')
                seen.add(path.casefold())
                body = archive.read(entry)
                if path == 'SKILL.md':
                    content = body.decode('utf-8')
                else:
                    files.append((path, body))
            return content, files
    except (zipfile.BadZipFile, UnicodeDecodeError, RuntimeError, NotImplementedError):
        raise HTTPException(422, 'Invalid skill bundle') from None


async def import_skill(form: SkillForm, encoded: str, user: Any, db: AsyncSession) -> SkillModel:
    content, files = await asyncio.to_thread(read_bundle, encoded)
    now = int(time.time())
    stored: list[str] = []
    try:
        skill = Skill(
            **form.model_dump(exclude={'access_grants', 'content'}),
            content=content,
            user_id=user.id,
            created_at=now,
            updated_at=now,
        )
        db.add(skill)
        await db.flush()
        for path, body in files:
            file_id = str(uuid.uuid4())
            filename = PurePosixPath(path).name
            media_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
            storage_path = None
            if body:
                _, storage_path = await asyncio.to_thread(
                    Storage.upload_file,
                    io.BytesIO(body),
                    f'{file_id}_{filename}',
                    {'OpenWebUI-User-Id': user.id, 'OpenWebUI-File-Id': file_id},
                )
                stored.append(storage_path)
            db.add(
                File(
                    id=file_id,
                    user_id=user.id,
                    filename=filename,
                    path=storage_path,
                    data={} if body else {'content': ''},
                    meta={'name': filename, 'content_type': media_type, 'size': len(body)},
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.flush()
            db.add(
                SkillFile(
                    id=str(uuid.uuid4()),
                    skill_id=skill.id,
                    file_id=file_id,
                    path=path,
                    user_id=user.id,
                    created_at=now,
                    updated_at=now,
                )
            )
        # Imports are private; sharing remains on the existing, separately authorized route.
        await db.commit()
        return SkillModel.model_validate(skill)
    except Exception:
        await db.rollback()
        for path in stored:
            await asyncio.to_thread(Storage.delete_file, path)
        raise
