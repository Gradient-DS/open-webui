"""[Gradient] Atomic bundle creation cannot assign another user's files or bypass skill permissions."""

import base64
import io
import zipfile
from pathlib import Path

import pytest
from open_webui.models.files import File
from open_webui.models.skill_files import SkillFile
from open_webui.models.skills import Skill
from open_webui.storage.provider import Storage
from sqlalchemy import func, select


def bundle(entries: dict[str, bytes] | None = None) -> str:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for path, body in (
            entries
            or {
                'outline/SKILL.md': b'---\nname: outline\ndescription: Outline text\n---\nUse the template.',
                'outline/assets/template.bin': b'\x00\xffbinary',
                'outline/scripts/run.py': b'print("example")',
            }
        ).items():
            archive.writestr(path, body)
    return base64.b64encode(stream.getvalue()).decode()


@pytest.fixture
def storage(monkeypatch, tmp_path):
    paths = []

    def upload(stream, filename, tags):
        content = stream.read()
        path = tmp_path / filename
        path.write_bytes(content)
        paths.append(path)
        return content, str(path)

    monkeypatch.setattr(Storage, 'upload_file', upload)
    monkeypatch.setattr(Storage, 'get_file', lambda path: path)
    monkeypatch.setattr(Storage, 'delete_file', lambda path: Path(path).unlink(missing_ok=True))
    return paths


def create(seeded, encoded=None, principal='owner', **extra):
    return seeded.client.post(
        '/api/v1/skills/create',
        headers=seeded.headers(principal),
        json={
            'id': 'outline-import',
            'name': 'Outline import',
            'description': 'Outline text',
            'content': 'Client content is replaced by the real SKILL.md',
            'bundle': encoded if encoded is not None else bundle(),
            **extra,
        },
    )


def counts(seeded):
    async def read():
        async with seeded.sessions() as db:
            return [await db.scalar(select(func.count()).select_from(model)) for model in (Skill, File, SkillFile)]

    return seeded.run(read)


@pytest.mark.parametrize('principal', ['owner', 'admin'])
def test_complete_private_import_and_cross_user_refusals(seeded, storage, principal):
    result = create(seeded, principal=principal)
    assert result.status_code == 200, result.text
    assert result.json()['user_id'] == principal
    assert result.json()['is_active'] is True
    assert counts(seeded) == [1, 2, 2]
    prefix = '/api/v1/skills/id/outline-import'
    for actor in ('outsider', 'reader', 'writer'):
        assert seeded.client.get(prefix, headers=seeded.headers(actor)).status_code == 401
        assert seeded.client.get(prefix + '/files', headers=seeded.headers(actor)).status_code == 401
        assert (
            seeded.client.get(
                prefix + '/files/content',
                headers=seeded.headers(actor),
                params={
                    'path': 'assets/template.bin',
                },
            ).status_code
            == 401
        )
    detail = seeded.client.get(prefix, headers=seeded.headers(principal))
    assert detail.json()['content'].endswith('Use the template.')
    response = seeded.client.get(
        prefix + '/files/content',
        headers=seeded.headers(principal),
        params={
            'path': 'assets/template.bin',
        },
    )
    assert response.content == b'\x00\xffbinary'


def test_import_rollback_cleans_database_and_storage(seeded, storage, monkeypatch):
    original = Storage.upload_file

    def fail_second(*args):
        if storage:
            raise OSError('Invented storage failure')
        return original(*args)

    monkeypatch.setattr(Storage, 'upload_file', fail_second)
    result = create(seeded)
    assert result.status_code == 400
    assert counts(seeded) == [0, 0, 0]
    assert storage and not any(path.exists() for path in storage)


@pytest.mark.parametrize('entry', ['../escape.txt', '/absolute.txt', 'assets/../../escape.txt', 'a\\b.txt'])
def test_import_rejects_unsafe_paths_before_storage(seeded, storage, entry):
    result = create(seeded, bundle({'SKILL.md': b'Body', entry: b'escape'}))
    assert result.status_code == 400
    assert not storage
    assert counts(seeded) == [0, 0, 0]


@pytest.mark.parametrize(
    'entries',
    [
        {'outer/inner/SKILL.md': b'Body'},
        {'outline/SKILL.md': b'Body', 'other/file.txt': b'Other'},
        {'SKILL.md': b'Body', 'assets/X.txt': b'1', 'assets/x.txt': b'2'},
    ],
)
def test_import_rejects_ambiguous_layout_and_collisions(seeded, storage, entries):
    assert create(seeded, bundle(entries)).status_code == 422
    assert not storage
    assert counts(seeded) == [0, 0, 0]


def test_import_cannot_set_foreign_ownership_or_grants(seeded, storage):
    result = create(seeded, user_id='outsider', file_id='outsider-file', path='/private/secret')
    assert result.status_code == 200
    assert result.json()['user_id'] == 'owner'
    assert all(path.parent == storage[0].parent for path in storage)


def test_import_requires_existing_creation_permission(seeded, storage):
    from open_webui.models.config import Config

    async def deny():
        permissions = await Config.get('user.permissions')
        permissions['workspace']['skills'] = False
        permissions['workspace']['skills_import'] = False
        await Config.upsert({'user.permissions': permissions})

    seeded.run(deny)
    result = create(seeded)
    assert result.status_code == 401
    assert not storage
    assert counts(seeded) == [0, 0, 0]


def test_import_refuses_direct_sharing(seeded, storage):
    result = create(
        seeded,
        access_grants=[
            {
                'principal_type': 'user',
                'principal_id': 'reader',
                'permission': 'read',
            }
        ],
    )
    assert result.status_code == 400
    assert counts(seeded) == [0, 0, 0]


def test_empty_imported_file_round_trips(seeded, storage):
    result = create(seeded, bundle({'outline/SKILL.md': b'Body', 'outline/assets/empty.bin': b''}))
    assert result.status_code == 200
    assert counts(seeded) == [1, 1, 1]
    response = seeded.client.get(
        '/api/v1/skills/id/outline-import/files/content',
        headers=seeded.headers('owner'),
        params={'path': 'assets/empty.bin'},
    )
    assert response.status_code == 200 and response.content == b''
    assert (
        seeded.client.get(
            '/api/v1/skills/id/outline-import/files/content',
            headers=seeded.headers('outsider'),
            params={'path': 'assets/empty.bin'},
        ).status_code
        == 401
    )


@pytest.mark.parametrize('kind', ['symlink', 'fifo', 'too-many', 'bad-base64', 'bad-utf8'])
def test_import_rejects_unsafe_zip_entries_and_invalid_content(seeded, storage, kind):
    import stat

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('SKILL.md', b'\xff' if kind == 'bad-utf8' else b'Body')
        if kind in ('symlink', 'fifo'):
            info = zipfile.ZipInfo('unsafe')
            info.create_system = 3
            info.external_attr = ((stat.S_IFLNK if kind == 'symlink' else stat.S_IFIFO) | 0o600) << 16
            archive.writestr(info, b'target')
        elif kind == 'too-many':
            for index in range(500):
                archive.writestr(f'file-{index}.txt', b'x')
    encoded = 'invalid base64' if kind == 'bad-base64' else base64.b64encode(stream.getvalue()).decode()
    assert create(seeded, encoded).status_code == (413 if kind == 'too-many' else 422)
    assert not storage
    assert counts(seeded) == [0, 0, 0]


@pytest.mark.parametrize('limit', ['MAX_COMPRESSED', 'MAX_EXPANDED'])
def test_import_limits_are_checked_before_storage(seeded, storage, monkeypatch, limit):
    from open_webui.utils import skill_imports

    monkeypatch.setattr(skill_imports, limit, 1)
    assert create(seeded).status_code == 413
    assert not storage
    assert counts(seeded) == [0, 0, 0]


def test_second_user_uploads_the_same_skill_privately(seeded, storage):
    first = create(seeded)
    second = create(seeded, principal='admin')
    assert first.status_code == second.status_code == 200, second.text
    assert second.json()['user_id'] == 'admin'
    assert second.json()['id'].startswith('outline-import-') and first.json()['id'] == 'outline-import'
    assert counts(seeded) == [2, 4, 4]
    own = seeded.client.get(f'/api/v1/skills/id/{second.json()["id"]}', headers=seeded.headers('owner'))
    assert own.status_code == 401
    assert create(seeded).status_code == 400
