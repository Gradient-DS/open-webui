"""Skill-bundle file resolution — lightweight helper imported by middleware.

Fetches the files attached to each skill and returns their content (for text
files) or metadata (for binary files) so the agent service can render a
``<bundled_files>`` manifest without an extra round-trip.

Text files are forwarded inline as ``{path, content, media_type, is_binary:
False, size}``. Binary files are forwarded as metadata-only ``{path,
media_type, is_binary: True, size}`` (no ``content`` key).

Design note on imports
~~~~~~~~~~~~~~~~~~~~~~
``SkillFiles`` and ``Files`` are imported at module level but their parent
packages (``open_webui.models.*``) trigger ``open_webui.internal.db`` which
connects to Postgres at import time.  We therefore use a *deferred module
cache* pattern: the real objects are resolved once on first use (inside
``_get_skill_files()`` / ``_get_files()``) and cached as module-level
attributes so that (a) tests can replace them via
``open_webui.utils.skill_bundles.SkillFiles = mock`` before the first call,
and (b) the module itself is importable in a test environment without a live
database.

``Storage`` is similarly deferred inside ``_read_file_content`` following the
same convention used in ``routers/skill_files.py``.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from pathlib import Path

log = logging.getLogger(__name__)

# Module-level cache — replaced by the real singletons on first use or by
# test doubles before the first call.  Using ``None`` as a sentinel allows
# ``unittest.mock.patch`` to replace them at the module attribute level.
SkillFiles = None  # type: ignore[assignment]
Files = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Text / binary classification
# ---------------------------------------------------------------------------

# application/* MIME types treated as text (forwarded inline to the agent).
_KNOWN_TEXT_MIME_TYPES = frozenset(
    {
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
)

# File extensions we classify as text even when the MIME type is unknown.
_TEXT_EXTENSIONS = frozenset(
    {
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
)


def _classify_text(content_type: str, filename: str) -> bool:
    """Return True if the file should be treated as text (content forwarded inline).

    Hierarchy:
    1. content_type starts with 'text/' → text
    2. content_type in _KNOWN_TEXT_MIME_TYPES → text
    3. file extension in _TEXT_EXTENSIONS → text
    4. Otherwise → binary
    """
    ct = (content_type or '').split(';')[0].strip().lower()
    if ct.startswith('text/'):
        return True
    if ct in _KNOWN_TEXT_MIME_TYPES:
        return True
    ext = Path(filename).suffix.lower() if filename else ''
    return ext in _TEXT_EXTENSIONS


def _get_skill_files():
    """Return the SkillFiles singleton, importing it on first use."""
    global SkillFiles
    if SkillFiles is None:
        from open_webui.models.skill_files import SkillFiles as _sf

        SkillFiles = _sf
    return SkillFiles


def _get_files():
    """Return the Files singleton, importing it on first use."""
    global Files
    if Files is None:
        from open_webui.models.files import Files as _f

        Files = _f
    return Files


async def _read_text_content(f) -> str | None:  # type: ignore[return]
    """Return the text content for a single text FileModel.

    Preference order:
    1. ``f.data['content']`` — populated at attach time (Phase 3 guarantee).
    2. Storage fallback via ``asyncio.to_thread(Storage.get_file, f.path)``
       for rows attached before Phase 3 landed.

    Returns ``None`` and logs a warning on any failure so the caller can
    skip the file gracefully without crashing the request.
    """
    # Fast path: content already in the DB row.
    cached = (f.data or {}).get('content')
    if cached is not None:
        return cached

    # Fallback: read from object storage (legacy rows, defensive path).
    if not f.path:
        log.warning('Skill file %s has no path and no cached content — skipping.', getattr(f, 'id', '?'))
        return None

    try:
        from open_webui.storage.provider import Storage  # deferred to avoid config-table at import time

        local_path = await asyncio.to_thread(Storage.get_file, f.path)
        raw_bytes = await asyncio.to_thread(Path(local_path).read_bytes)
        return raw_bytes.decode('utf-8')
    except Exception as exc:
        log.warning(
            'Skill file %s: Storage fallback failed — skipping content. Error: %s',
            getattr(f, 'filename', '?'),
            exc,
        )
        return None


def _get_file_media_type(f, virtual_path: str) -> str:
    """Derive the media_type for a FileModel.

    Preference: File.meta['content_type'] → mimetypes.guess_type(virtual_path)
    → 'application/octet-stream'.
    """
    meta = getattr(f, 'meta', None) or {}
    ct = meta.get('content_type') or ''
    if ct:
        return ct.split(';')[0].strip()
    guessed, _ = mimetypes.guess_type(virtual_path)
    return guessed or 'application/octet-stream'


def _get_file_size(f) -> int | None:
    """Derive the size for a FileModel from meta."""
    meta = getattr(f, 'meta', None) or {}
    return meta.get('size') or None


async def resolve_skill_bundle_files(available_skills) -> dict[str, list[dict]]:
    """Return a mapping of skill_id → [{forwarding entry}, ...].

    For skills with no attached files the value is an empty list.  The
    middleware uses this to add ``'files'`` to the skill dict forwarded to
    the agent service — ONLY when the list is non-empty, so skills without
    files are forwarded byte-identically to today (no ``'files'`` key).

    The bundle is keyed by the virtual ``path`` (not the backing filename), so
    the same File mapped to two paths yields two entries.

    Forwarding shape (matches soev-agents SkillFile):
    - text: ``{path, content, media_type, is_binary: False, size}``
    - binary: ``{path, media_type, is_binary: True, size}``  (no 'content' key)

    Args:
        available_skills: Iterable of skill model objects (have ``.id``).

    Returns:
        Dict mapping each skill's id to a (possibly empty) list of forwarding
        entry dicts.  Files whose content cannot be resolved (text) or that
        cannot be classified are silently omitted.
    """
    skill_files = _get_skill_files()
    files = _get_files()

    result: dict[str, list[dict]] = {}
    for skill in available_skills:
        skill_file_rows = await skill_files.get_files_by_skill_id(skill.id)
        if not skill_file_rows:
            result[skill.id] = []
            continue

        # One DB round-trip for the distinct backing Files, then resolve each
        # row's content by its file_id (a File may back several paths).
        file_ids = list({row.file_id for row in skill_file_rows})
        file_models = await files.get_files_by_ids(file_ids)
        files_by_id = {f.id: f for f in file_models}

        bundle: list[dict] = []
        for row in skill_file_rows:
            f = files_by_id.get(row.file_id)
            if f is None:
                continue

            meta = getattr(f, 'meta', None) or {}
            raw_ct = meta.get('content_type') or ''
            media_type = _get_file_media_type(f, row.path)
            size = _get_file_size(f)
            filename = getattr(f, 'filename', '') or ''

            if _classify_text(raw_ct, filename or row.path):
                # Text file: forward content inline.
                content = await _read_text_content(f)
                if content is None:
                    # Could not resolve — skip gracefully.
                    continue
                entry: dict = {
                    'path': row.path,
                    'content': content,
                    'media_type': media_type,
                    'is_binary': False,
                    'size': size,
                }
            else:
                # Binary file: metadata-only (agent fetches via raw-bytes route).
                entry = {
                    'path': row.path,
                    'media_type': media_type,
                    'is_binary': True,
                    'size': size,
                }
            bundle.append(entry)

        result[skill.id] = bundle

    return result
