"""[Gradient] The portable assistant bundle, shared by export and workspace turns."""

import asyncio
import io
import json
import re
import zipfile

import yaml
from open_webui.models.files import Files
from open_webui.storage.provider import Storage
from open_webui.utils.agent_skills import MAX_EXPANDED, _read_bytes

MAX_COMPRESSED = 25 * 1024 * 1024
MAX_ENTRIES = 500


def validate_path(path: str) -> None:
    if (
        not path
        or len(path) > 255
        or '\\' in path
        or ':' in path
        or any(ord(char) < 32 for char in path)
        or any(part in ('', '.', '..') for part in path.split('/'))
        or path.casefold() in ('skill.md', 'soev.json')
    ):
        raise ValueError('path')


def skill_name(name: str) -> str:
    return re.sub(r'[^a-z0-9_-]+', '-', name.lower()).strip('-_')[:64] or 'assistant'


def skill_md(model) -> str:
    meta = model.meta.model_dump()
    description = meta.get('description') or model.name
    if not 1 <= len(description) <= 1024:
        raise ValueError('description')
    frontmatter = {'name': skill_name(model.name), 'description': description}
    return (
        '---\n'
        + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False)
        + '---\n'
        + (model.params.model_dump().get('system') or '')
    )


def portable_settings(model) -> dict:
    meta = model.meta.model_dump()
    return {
        'version': 1,
        'name': model.name,
        'description': meta.get('description') or '',
        **{key: meta.get(key) for key in ('defaultModelId', 'capabilities', 'defaultFeatureIds', 'builtinTools')},
        'knowledge': [
            {'type': entry['type'], 'id': entry['id']}
            for entry in meta.get('knowledge') or []
            if isinstance(entry, dict) and isinstance(entry.get('type'), str) and isinstance(entry.get('id'), str)
        ],
    }


async def _owner_bytes(file, owner: str, limit: int) -> bytes:
    # Assistant access delegates its owner's bytes; individual caller file access is irrelevant.
    if file is None or file.user_id != owner:
        raise ValueError('owner')
    if file.path:
        local = await asyncio.to_thread(Storage.get_file, file.path)
        return await asyncio.to_thread(_read_bytes, local, limit)
    if (file.meta or {}).get('size') == 0 and (file.data or {}).get('content') == '':
        return b''
    raise ValueError('missing')


async def build_bundle(model, rows, *, export: bool = False, db=None) -> bytes:
    if len(rows) + (2 if export else 1) > MAX_ENTRIES:
        raise ValueError('count')
    entries = [('SKILL.md', skill_md(model).encode())]
    if export:
        entries.append(('soev.json', json.dumps(portable_settings(model), ensure_ascii=False).encode()))
    expanded = sum(len(body) for _, body in entries)
    file_size = 0
    if expanded > MAX_EXPANDED:
        raise ValueError('size')
    seen = {path.casefold() for path, _ in entries}
    backing = {file.id: file for file in await Files.get_files_by_ids(list({row.file_id for row in rows}), db=db)}
    for row in sorted(rows, key=lambda row: row.path):
        validate_path(row.path)
        if row.path.casefold() in seen:
            raise ValueError('path')
        seen.add(row.path.casefold())
        body = await _owner_bytes(backing.get(row.file_id), model.user_id, MAX_EXPANDED - expanded + 1)
        file_size += len(body)
        if file_size > MAX_COMPRESSED:
            raise ValueError('size')
        expanded += len(body)
        if expanded > MAX_EXPANDED:
            raise ValueError('size')
        entries.append((row.path, body))
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path, body in entries:
            info = zipfile.ZipInfo(path)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, body)
    bundle = output.getvalue()
    if len(bundle) > MAX_COMPRESSED:
        raise ValueError('size')
    return bundle
