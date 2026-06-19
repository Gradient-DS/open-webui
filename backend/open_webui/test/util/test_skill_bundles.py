"""Unit tests for the skill-bundle file resolution helper.

Tests cover:
- A skill with two attached markdown files yields [{'filename', 'content'}, ...]
- A skill with no files yields [] (and middleware therefore omits the 'files' key)
- A file whose data['content'] is missing falls back to Storage.get_file
- Storage fallback failure is handled gracefully (file skipped, no crash)

These tests set ``open_webui.utils.skill_bundles.SkillFiles`` and
``open_webui.utils.skill_bundles.Files`` directly (they are ``None`` until
first use, which is the extension point designed for test doubles).  No live
database is required.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import open_webui.utils.skill_bundles as skill_bundles_mod
import pytest
from open_webui.utils.skill_bundles import resolve_skill_bundle_files


def _make_skill(skill_id: str):
    return SimpleNamespace(id=skill_id)


def _make_skill_file_row(file_id: str):
    return SimpleNamespace(file_id=file_id)


def _make_file_model(file_id: str, filename: str, content: str | None = None, path: str | None = None):
    data = {'content': content} if content is not None else {}
    return SimpleNamespace(id=file_id, filename=filename, data=data, path=path or f'uploads/{file_id}')


def _make_mock_sf(rows_by_skill: dict):
    """Build a mock SkillFiles singleton with get_files_by_skill_id."""
    mock = MagicMock()
    mock.get_files_by_skill_id = AsyncMock(side_effect=lambda sid: rows_by_skill.get(sid, []))
    return mock


def _make_mock_files(files_by_id: dict):
    """Build a mock Files singleton with get_files_by_ids."""
    mock = MagicMock()
    mock.get_files_by_ids = AsyncMock(side_effect=lambda ids: [files_by_id[i] for i in ids if i in files_by_id])
    return mock


class TestResolveSkillBundleFiles:
    """Test the resolve_skill_bundle_files helper."""

    @pytest.mark.asyncio
    async def test_skill_with_two_files_returns_bundle(self):
        """A skill with two attached markdown files returns both entries."""
        skill = _make_skill('skill-1')
        sf_rows = [_make_skill_file_row('f1'), _make_skill_file_row('f2')]
        file_f1 = _make_file_model('f1', 'guide.md', content='# Guide\nStep 1')
        file_f2 = _make_file_model('f2', 'tone.md', content='Always formal.')

        mock_sf = _make_mock_sf({'skill-1': sf_rows})
        mock_files = _make_mock_files({'f1': file_f1, 'f2': file_f2})

        orig_sf, orig_files = skill_bundles_mod.SkillFiles, skill_bundles_mod.Files
        try:
            skill_bundles_mod.SkillFiles = mock_sf
            skill_bundles_mod.Files = mock_files
            result = await resolve_skill_bundle_files([skill])
        finally:
            skill_bundles_mod.SkillFiles = orig_sf
            skill_bundles_mod.Files = orig_files

        assert 'skill-1' in result
        bundle = result['skill-1']
        assert len(bundle) == 2
        assert bundle[0] == {'filename': 'guide.md', 'content': '# Guide\nStep 1'}
        assert bundle[1] == {'filename': 'tone.md', 'content': 'Always formal.'}

    @pytest.mark.asyncio
    async def test_skill_with_no_files_returns_empty_list(self):
        """A skill with no attached files returns an empty list.

        The middleware checks for non-empty and omits the 'files' key when
        the list is empty — this is the regression guarantee.
        """
        skill = _make_skill('skill-nofiles')

        mock_sf = _make_mock_sf({'skill-nofiles': []})
        mock_files = _make_mock_files({})

        orig_sf, orig_files = skill_bundles_mod.SkillFiles, skill_bundles_mod.Files
        try:
            skill_bundles_mod.SkillFiles = mock_sf
            skill_bundles_mod.Files = mock_files
            result = await resolve_skill_bundle_files([skill])
        finally:
            skill_bundles_mod.SkillFiles = orig_sf
            skill_bundles_mod.Files = orig_files

        assert result['skill-nofiles'] == []
        # get_files_by_ids must NOT be called when there are no file rows
        mock_files.get_files_by_ids.assert_not_called()

    @pytest.mark.asyncio
    async def test_storage_fallback_used_when_no_cached_content(self):
        """When data['content'] is absent, falls back to Storage.get_file."""
        skill = _make_skill('skill-legacy')
        sf_rows = [_make_skill_file_row('flegacy')]
        # File has no cached content but has a path
        file_legacy = _make_file_model('flegacy', 'legacy.md', content=None, path='uploads/flegacy')

        mock_sf = _make_mock_sf({'skill-legacy': sf_rows})
        mock_files = _make_mock_files({'flegacy': file_legacy})

        orig_sf, orig_files = skill_bundles_mod.SkillFiles, skill_bundles_mod.Files
        try:
            skill_bundles_mod.SkillFiles = mock_sf
            skill_bundles_mod.Files = mock_files

            with (
                patch('open_webui.utils.skill_bundles.asyncio.to_thread', new_callable=AsyncMock) as mock_thread,
                patch.dict('sys.modules', {'open_webui.storage.provider': MagicMock()}),
            ):
                mock_thread.return_value = b'Legacy markdown content'
                result = await resolve_skill_bundle_files([skill])
        finally:
            skill_bundles_mod.SkillFiles = orig_sf
            skill_bundles_mod.Files = orig_files

        bundle = result['skill-legacy']
        assert len(bundle) == 1
        assert bundle[0] == {'filename': 'legacy.md', 'content': 'Legacy markdown content'}

    @pytest.mark.asyncio
    async def test_storage_fallback_failure_skips_file_gracefully(self):
        """If Storage fallback fails, the file is skipped — request does not crash."""
        skill = _make_skill('skill-err')
        sf_rows = [_make_skill_file_row('ferr')]
        file_err = _make_file_model('ferr', 'broken.md', content=None, path='uploads/ferr')

        mock_sf = _make_mock_sf({'skill-err': sf_rows})
        mock_files = _make_mock_files({'ferr': file_err})

        orig_sf, orig_files = skill_bundles_mod.SkillFiles, skill_bundles_mod.Files
        try:
            skill_bundles_mod.SkillFiles = mock_sf
            skill_bundles_mod.Files = mock_files

            with (
                patch('open_webui.utils.skill_bundles.asyncio.to_thread', new_callable=AsyncMock) as mock_thread,
                patch.dict('sys.modules', {'open_webui.storage.provider': MagicMock()}),
            ):
                mock_thread.side_effect = RuntimeError('Storage unreachable')
                result = await resolve_skill_bundle_files([skill])
        finally:
            skill_bundles_mod.SkillFiles = orig_sf
            skill_bundles_mod.Files = orig_files

        # File is skipped, bundle is empty, no exception raised
        assert result['skill-err'] == []

    @pytest.mark.asyncio
    async def test_multiple_skills_resolved_independently(self):
        """Multiple skills are each resolved without cross-contamination."""
        skill_a = _make_skill('skill-A')
        skill_b = _make_skill('skill-B')

        file_a = _make_file_model('fa', 'a.md', content='Content A')

        mock_sf = _make_mock_sf({'skill-A': [_make_skill_file_row('fa')], 'skill-B': []})
        mock_files = _make_mock_files({'fa': file_a})

        orig_sf, orig_files = skill_bundles_mod.SkillFiles, skill_bundles_mod.Files
        try:
            skill_bundles_mod.SkillFiles = mock_sf
            skill_bundles_mod.Files = mock_files
            result = await resolve_skill_bundle_files([skill_a, skill_b])
        finally:
            skill_bundles_mod.SkillFiles = orig_sf
            skill_bundles_mod.Files = orig_files

        assert result['skill-A'] == [{'filename': 'a.md', 'content': 'Content A'}]
        assert result['skill-B'] == []
