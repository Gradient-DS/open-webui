"""[Gradient] Portable skill bytes, metadata and platform limits."""

import io
import json
import re
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from open_webui.models.models import ModelMeta, ModelParams
from open_webui.utils import assistant_skill_bundles as bundles


def assistant(**extra):
    return SimpleNamespace(
        id='invented-assistant',
        user_id='invented-owner',
        name='Invented Helper',
        meta=ModelMeta(description='Invented description'),
        params=ModelParams(system='---\nBody stays exactly as written.\n'),
        **extra,
    )


@pytest.mark.parametrize('name', ['A Name', '___prefix', 'één 🤖', 'a' * 100, '9-safe_name'])
def test_slug_always_matches_platform(name):
    assert re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', bundles.skill_name(name))


@pytest.mark.parametrize(
    'path', ['/x', 'a//b', 'a/./b', 'a/../b', 'a\\b', 'C:/x', 'SKILL.md', 'skill.MD', 'soev.json', '', 'x' * 256]
)
def test_path_validation(path):
    with pytest.raises(ValueError, match='path'):
        bundles.validate_path(path)


def test_frontmatter_quotes_metadata_and_preserves_instructions():
    model = assistant()
    model.meta.description = 'Invented: "description"\nSecond line'
    text = bundles.skill_md(model)
    header, body = text[4:].split('---\n', 1)
    assert yaml.safe_load(header) == {'name': 'invented-helper', 'description': model.meta.description}
    assert body == model.params.system


@pytest.mark.asyncio
@pytest.mark.parametrize('description', ['', 'Invented description'])
async def test_deterministic_export_has_only_allowlisted_settings_and_owner_bytes(monkeypatch, tmp_path, description):
    model = assistant()
    model.meta = ModelMeta(
        description=description,
        defaultModelId='invented-llm',
        capabilities={'workspace': True},
        defaultFeatureIds=['workspace'],
        builtinTools={},
        secret='never exported',
        knowledge=[{'type': 'note', 'id': 'invented-note', 'content': 'private'}],
    )
    path = tmp_path / 'invented.bin'
    path.write_bytes(b'\x00\xffinvented')
    file = SimpleNamespace(id='invented-file', user_id=model.user_id, path=str(path))
    row = SimpleNamespace(path='assets/invented.bin', file_id=file.id)
    monkeypatch.setattr(bundles.Files, 'get_files_by_ids', AsyncMock(return_value=[file]))
    monkeypatch.setattr(bundles.Storage, 'get_file', lambda path: path)
    first = await bundles.build_bundle(model, [row], export=True)
    assert first == await bundles.build_bundle(model, [row], export=True)
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.namelist() == ['SKILL.md', 'soev.json', 'assets/invented.bin']
        assert archive.read(row.path) == path.read_bytes()
        settings = json.loads(archive.read('soev.json'))
        assert set(settings) == {
            'version',
            'name',
            'description',
            'defaultModelId',
            'capabilities',
            'defaultFeatureIds',
            'builtinTools',
            'knowledge',
        }
        assert settings['name'] == model.name
        assert settings['description'] == description
        header, body = archive.read('SKILL.md').decode()[4:].split('---\n', 1)
        assert yaml.safe_load(header) == {'name': 'invented-helper', 'description': description or model.name}
        assert body == model.params.system
        assert settings['knowledge'] == [{'type': 'note', 'id': 'invented-note'}]
    file.user_id = 'invented-outsider'
    with pytest.raises(ValueError, match='owner'):
        await bundles.build_bundle(model, [row])


@pytest.mark.asyncio
async def test_caps_are_checked_before_or_during_packaging(monkeypatch, tmp_path):
    model = assistant()
    with pytest.raises(ValueError, match='count'):
        await bundles.build_bundle(model, [None] * 499, export=True)
    with pytest.raises(ValueError, match='count'):
        await bundles.build_bundle(model, [None] * 500)
    monkeypatch.setattr(bundles.Files, 'get_files_by_ids', AsyncMock(return_value=[]))
    monkeypatch.setattr(bundles, 'MAX_COMPRESSED', 1)
    with pytest.raises(ValueError, match='size'):
        await bundles.build_bundle(model, [])
    monkeypatch.setattr(bundles, 'MAX_EXPANDED', 1)
    with pytest.raises(ValueError, match='size'):
        await bundles.build_bundle(model, [])


def test_description_must_fit_the_platform():
    model = assistant()
    model.meta.description = 'x' * 1025
    with pytest.raises(ValueError, match='description'):
        bundles.skill_md(model)
