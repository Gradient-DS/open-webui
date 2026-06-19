"""Skill-bundle file resolution — lightweight helper imported by middleware.

Fetches the markdown files attached to each skill and returns their content
so the agent service can render a ``<bundled_files>`` manifest without an
extra round-trip.

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
from pathlib import Path

log = logging.getLogger(__name__)

# Module-level cache — replaced by the real singletons on first use or by
# test doubles before the first call.  Using ``None`` as a sentinel allows
# ``unittest.mock.patch`` to replace them at the module attribute level.
SkillFiles = None  # type: ignore[assignment]
Files = None  # type: ignore[assignment]


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


async def _read_file_content(f) -> str | None:  # type: ignore[return]
    """Return the markdown text for a single FileModel.

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


async def resolve_skill_bundle_files(available_skills) -> dict[str, list[dict]]:
    """Return a mapping of skill_id → [{'filename', 'content'}, ...].

    For skills with no attached files the value is an empty list.  The
    middleware uses this to add ``'files'`` to the skill dict forwarded to
    the agent service — ONLY when the list is non-empty, so skills without
    files are forwarded byte-identically to today (no ``'files'`` key).

    Args:
        available_skills: Iterable of skill model objects (have ``.id``).

    Returns:
        Dict mapping each skill's id to a (possibly empty) list of
        ``{'filename': str, 'content': str}`` dicts.  Files whose content
        cannot be resolved are silently omitted.
    """
    skill_files = _get_skill_files()
    files = _get_files()

    result: dict[str, list[dict]] = {}
    for skill in available_skills:
        skill_file_rows = await skill_files.get_files_by_skill_id(skill.id)
        if not skill_file_rows:
            result[skill.id] = []
            continue

        file_ids = [row.file_id for row in skill_file_rows]
        file_models = await files.get_files_by_ids(file_ids)

        bundle: list[dict] = []
        for f in file_models:
            content = await _read_file_content(f)
            if content is not None:
                bundle.append({'filename': f.filename, 'content': content})

        result[skill.id] = bundle

    return result
