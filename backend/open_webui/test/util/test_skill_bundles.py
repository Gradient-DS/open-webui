"""Unit tests for the skill-bundle file resolution helper.

Tests cover:
- A skill with two attached markdown files yields [{'path', 'content'}, ...]
- A skill with no files yields [] (and middleware therefore omits the 'files' key)
- A file whose data['content'] is missing falls back to Storage.get_file
- Storage fallback failure is handled gracefully (file skipped, no crash)
- The same backing File at two paths yields two entries (path-keyed, not file-keyed)

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


def _make_skill_file_row(file_id: str, path: str | None = None):
    return SimpleNamespace(file_id=file_id, path=path or f'{file_id}.md')


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
        sf_rows = [
            _make_skill_file_row('f1', path='docs/guide.md'),
            _make_skill_file_row('f2', path='tone.md'),
        ]
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
        by_path = {entry['path']: entry['content'] for entry in bundle}
        assert by_path == {'docs/guide.md': '# Guide\nStep 1', 'tone.md': 'Always formal.'}
        # No 'filename' key — the contract is now path-keyed.
        assert all('filename' not in entry for entry in bundle)

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
    async def test_storage_fallback_used_when_no_cached_content(self, tmp_path):
        """When data['content'] is absent, falls back to Storage.get_file.

        Storage.get_file returns a LOCAL PATH (str), not bytes.  The fallback
        must READ the file at that path and return its text content — not the
        path string itself.
        """
        md_text = 'Legacy markdown content'
        tmp_file = tmp_path / 'legacy.md'
        tmp_file.write_text(md_text, encoding='utf-8')

        skill = _make_skill('skill-legacy')
        sf_rows = [_make_skill_file_row('flegacy', path='legacy.md')]
        # File has no cached content but has a path
        file_legacy = _make_file_model('flegacy', 'legacy.md', content=None, path='uploads/flegacy')

        mock_sf = _make_mock_sf({'skill-legacy': sf_rows})
        mock_files = _make_mock_files({'flegacy': file_legacy})

        orig_sf, orig_files = skill_bundles_mod.SkillFiles, skill_bundles_mod.Files
        try:
            skill_bundles_mod.SkillFiles = mock_sf
            skill_bundles_mod.Files = mock_files

            # to_thread is called twice:
            #   1st call: Storage.get_file('uploads/flegacy') → returns tmp_file path (str)
            #   2nd call: Path(local_path).read_bytes() → reads the real file
            # We let the 2nd call execute for real via run_in_executor so the
            # actual file is read, while the 1st call is intercepted to return the
            # tmp_file path instead of hitting real storage.
            import asyncio as _asyncio

            call_count = {'n': 0}

            async def _to_thread(fn, *args, **kwargs):
                call_count['n'] += 1
                if call_count['n'] == 1:
                    # First call: Storage.get_file → return the tmp file path
                    return str(tmp_file)
                # Subsequent calls: execute for real (Path.read_bytes)
                return await _asyncio.get_event_loop().run_in_executor(None, fn, *args, **kwargs)

            with (
                patch('open_webui.utils.skill_bundles.asyncio.to_thread', side_effect=_to_thread),
                patch.dict('sys.modules', {'open_webui.storage.provider': MagicMock()}),
            ):
                result = await resolve_skill_bundle_files([skill])
        finally:
            skill_bundles_mod.SkillFiles = orig_sf
            skill_bundles_mod.Files = orig_files

        bundle = result['skill-legacy']
        assert len(bundle) == 1
        # Content must be the FILE'S TEXT, not the path string.
        assert bundle[0] == {'path': 'legacy.md', 'content': md_text}
        assert bundle[0]['content'] != str(tmp_file)

    @pytest.mark.asyncio
    async def test_storage_fallback_failure_skips_file_gracefully(self):
        """If Storage fallback fails, the file is skipped — request does not crash."""
        skill = _make_skill('skill-err')
        sf_rows = [_make_skill_file_row('ferr', path='broken.md')]
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

        mock_sf = _make_mock_sf({'skill-A': [_make_skill_file_row('fa', path='a.md')], 'skill-B': []})
        mock_files = _make_mock_files({'fa': file_a})

        orig_sf, orig_files = skill_bundles_mod.SkillFiles, skill_bundles_mod.Files
        try:
            skill_bundles_mod.SkillFiles = mock_sf
            skill_bundles_mod.Files = mock_files
            result = await resolve_skill_bundle_files([skill_a, skill_b])
        finally:
            skill_bundles_mod.SkillFiles = orig_sf
            skill_bundles_mod.Files = orig_files

        assert result['skill-A'] == [{'path': 'a.md', 'content': 'Content A'}]
        assert result['skill-B'] == []

    @pytest.mark.asyncio
    async def test_same_file_at_two_paths_yields_two_entries(self):
        """One backing File mapped to two paths must produce two bundle entries
        (the contract is path-keyed, not file-keyed)."""
        skill = _make_skill('skill-dup')
        sf_rows = [
            _make_skill_file_row('shared', path='a.md'),
            _make_skill_file_row('shared', path='docs/b.md'),
        ]
        shared = _make_file_model('shared', 'shared.md', content='shared content')

        mock_sf = _make_mock_sf({'skill-dup': sf_rows})
        mock_files = _make_mock_files({'shared': shared})

        orig_sf, orig_files = skill_bundles_mod.SkillFiles, skill_bundles_mod.Files
        try:
            skill_bundles_mod.SkillFiles = mock_sf
            skill_bundles_mod.Files = mock_files
            result = await resolve_skill_bundle_files([skill])
        finally:
            skill_bundles_mod.SkillFiles = orig_sf
            skill_bundles_mod.Files = orig_files

        bundle = result['skill-dup']
        by_path = {entry['path']: entry['content'] for entry in bundle}
        assert by_path == {'a.md': 'shared content', 'docs/b.md': 'shared content'}
