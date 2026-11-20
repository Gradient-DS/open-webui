"""[Gradient] Package readable skills for the workspace and cache caller-owned platform references."""

import asyncio
import hashlib
import io
import json
import logging
import re
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import Any

import yaml
from open_webui import config
from open_webui.models.files import Files
from open_webui.models.skill_files import SkillFileModel, SkillFiles
from open_webui.models.skills import SkillModel, Skills
from open_webui.soev import identity
from open_webui.soev.client import SoevApiError, SoevClient
from open_webui.storage.provider import Storage

log = logging.getLogger(__name__)
# Private process memory: shared skill metadata must never expose another user's references.
REFERENCES: OrderedDict[tuple[str, ...], tuple[dict[str, Any], str]] = OrderedDict()
CACHE_LIMIT = 512
_upload_lock: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None
MAX_EXPANDED = 100 * 1024 * 1024
_FRONTMATTER = re.compile(r'\A---\r?\n(.*?)\r?\n(?:---|\.\.\.)(?:\r?\n|\Z)', re.DOTALL)


def _skill_name(skill_id: str, used: set[str], label: str = '') -> str:
    # The agent reads the skill's own name; ids are global and may carry a suffix another user's upload caused.
    slug = re.sub(r'[^a-z0-9_-]+', '-', label.lower()).strip('-')
    base = re.sub(r'^[^a-z0-9]+', '', slug or skill_id.lower())[:64]
    name = base
    attempt = 0
    while not name or name in used:
        source = skill_id if attempt == 0 else f'{skill_id}\0{attempt}'
        suffix = hashlib.sha256(source.encode()).hexdigest()[:8]
        name = f'{(base or "skill")[:55]}-{suffix}'
        attempt += 1
    used.add(name)
    return name


def _skill_md(skill: SkillModel, name: str) -> str:
    match = _FRONTMATTER.match(skill.content)
    frontmatter = yaml.safe_load(match[1]) if match else {}
    if frontmatter is None:
        frontmatter = {}
    if not isinstance(frontmatter, dict):
        raise ValueError('Invalid skill frontmatter')
    frontmatter['name'] = name
    frontmatter['description'] = skill.description or skill.name
    body = skill.content[match.end() :] if match else skill.content
    return '---\n' + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False) + '---\n' + body


def _read_bytes(path: str, limit: int) -> bytes:
    with Path(path).open('rb') as file:
        return file.read(limit)


async def _bundle(skill: SkillModel, name: str, files: list[SkillFileModel]) -> bytes:
    if len(files) + 1 > 500:
        raise ValueError('Too many skill files')
    content = _skill_md(skill, name).encode()
    size = len(content)
    if size > MAX_EXPANDED:
        raise ValueError('Skill bundle too large')
    seen = {'skill.md'}
    backing = {file.id: file for file in await Files.get_files_by_ids(list({entry.file_id for entry in files}))}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo('SKILL.md')
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, content)
        for entry in sorted(files, key=lambda entry: entry.path):
            parts = entry.path.split('/')
            if (
                any(part in ('', '.', '..') for part in parts)
                or '\\' in entry.path
                or ':' in entry.path
                or entry.path.casefold() in seen
            ):
                raise ValueError('Invalid skill file path')
            seen.add(entry.path.casefold())
            file = backing.get(entry.file_id)
            if file is None:
                raise ValueError('Missing skill file')
            if not file.path and (file.meta or {}).get('size') == 0 and (file.data or {}).get('content') == '':
                content = b''
            elif file.path:
                path = await asyncio.to_thread(Storage.get_file, file.path)
                # Read bytes for text too: data.content can be stale and cannot represent binary files.
                content = await asyncio.to_thread(_read_bytes, path, MAX_EXPANDED - size + 1)
            else:
                raise ValueError('Missing skill file')
            size += len(content)
            if size > MAX_EXPANDED:
                raise ValueError('Skill bundle too large')
            # Fixed timestamps make identical bundles hash identically across turns.
            info = zipfile.ZipInfo(entry.path)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    body = output.getvalue()
    if len(body) > 25 * 1024 * 1024 or size > MAX_EXPANDED:
        raise ValueError('Skill bundle too large')
    return body


def _notice(name: str, language: str | None) -> str:
    return (
        f'De skill “{name}” kon niet worden geladen. Deze beurt gaat verder zonder deze skill.'
        if (language or '').lower().startswith('nl')
        else f'The skill “{name}” could not be loaded. This turn continues without it.'
    )


def forget_references(user: str, references: list[dict[str, Any]], language: str | None) -> list[str]:
    ids = {reference['id'] for reference in references}
    matches = {
        key[3]: (reference['id'], name)
        for key, (reference, name) in REFERENCES.items()
        if key[2] == user and reference['id'] in ids
    }
    names = dict(matches.values())
    for key in list(REFERENCES):
        if key[2] == user and key[3] in matches:
            del REFERENCES[key]
    return [_notice(names.get(reference['id'], reference['name']), language) for reference in references]


async def skill_references(metadata: dict[str, Any], user: str) -> tuple[list[dict[str, Any]], list[str]]:
    # The model is server-resolved, including the assistant/LLM split. Never trust metadata.skills selections.
    attached = set((((metadata.get('model') or {}).get('info') or {}).get('meta') or {}).get('skillIds') or [])
    readable = await Skills.get_skills(user_id=metadata['user_id'])
    selected = [skill for skill in readable if skill.is_active or skill.id in attached]
    selected.sort(key=lambda skill: (skill.id not in attached, -skill.updated_at, skill.id))
    if len(selected) > 20:
        log.warning('Workspace skill limit: left out %d skills', len(selected) - 20)
    references, notices = [], []
    names: set[str] = set()
    client = identity.build_client() if selected else None
    selected = selected[:20]
    bundles = await SkillFiles.get_files_by_skill_ids([skill.id for skill in selected]) if selected else {}
    file_ids = {entry.file_id for files in bundles.values() for entry in files}
    backing = {file.id: file for file in await Files.get_file_metadatas_by_ids(list(file_ids))} if file_ids else {}
    for skill in selected:
        name = _skill_name(skill.id, names, skill.name or '')
        files = sorted(bundles[skill.id], key=lambda entry: entry.path)
        versions = [
            [
                entry.path,
                entry.file_id,
                entry.updated_at,
                [file.hash, file.updated_at, (file.meta or {}).get('size')]
                if (file := backing.get(entry.file_id)) is not None
                else None,
            ]
            for entry in files
        ]
        source = json.dumps([skill.content, skill.name, skill.description, name, versions], ensure_ascii=False).encode()
        key = (
            config.SOEV_API_URL,
            hashlib.sha256(config.SOEV_API_KEY.encode()).hexdigest(),
            user,
            skill.id,
            hashlib.sha256(source).hexdigest(),
        )
        try:
            uploaded = await _reference(key, skill, name, files, user, client)
        except (OSError, ValueError, yaml.YAMLError):
            log.warning('Workspace skill unavailable: skill=%s', skill.id)
            notices.append(_notice(skill.name, metadata.get('user_language')))
            continue
        if uploaded is None:
            notices.append(_notice(skill.name, metadata.get('user_language')))
        else:
            references.append(uploaded)
    return references, notices


async def _reference(
    key: tuple[str, ...],
    skill: SkillModel,
    name: str,
    files: list[SkillFileModel],
    user: str,
    client: SoevClient,
) -> dict[str, Any] | None:
    global _upload_lock
    loop = asyncio.get_running_loop()
    if _upload_lock is None or _upload_lock[0] is not loop:
        _upload_lock = (loop, asyncio.Lock())
    async with _upload_lock[1]:
        if key in REFERENCES:
            REFERENCES.move_to_end(key)
            return REFERENCES[key][0]
        body = await _bundle(skill, name, files)
        await identity.ensure_link(user, client)
        try:
            uploaded = await client.post_bytes('/v1/chat/skills', body, as_user=user, content_type='application/zip')
        except SoevApiError as error:
            if not 400 <= error.status < 500:
                raise
            log.warning('Workspace skill refused: skill=%s status=%s code=%s', skill.id, error.status, error.code)
            return None
        REFERENCES[key] = (uploaded, skill.name)
        while len(REFERENCES) > CACHE_LIMIT:
            REFERENCES.popitem(last=False)
        return uploaded
