"""[Gradient] Assistant access delegates only owner-owned skill bytes, including after revocation."""

import io
import zipfile

import pytest
from open_webui.models.access_grants import AccessGrants
from open_webui.models.assistant_skill_files import AssistantSkillFile, AssistantSkillFiles
from open_webui.models.files import File, Files
from open_webui.models.models import Model, Models
from open_webui.storage.provider import Storage
from sqlalchemy import func, select

PREFIX = '/api/v1/models/id/invented-helper'


@pytest.fixture
def folder(seeded, monkeypatch, tmp_path):
    monkeypatch.setattr(Storage, 'get_file', lambda path: path)
    path = tmp_path / 'invented.bin'
    path.write_bytes(b'\x00\xffinvented')

    async def insert():
        async with seeded.sessions() as db:
            db.add(
                Model(
                    id='invented-helper',
                    user_id='owner',
                    base_model_id='invented-llm',
                    name='Invented Helper',
                    params={'system': 'Invented instructions'},
                    meta={'description': 'Invented description'},
                    created_at=1,
                    updated_at=1,
                )
            )
            for principal in ('owner', 'writer', 'outsider'):
                db.add(
                    File(
                        id=f'{principal}-upload',
                        user_id=principal,
                        path=str(path),
                        filename='invented.bin',
                        meta={'size': 10},
                        created_at=1,
                        updated_at=1,
                    )
                )
            await db.commit()
            await AccessGrants.set_access_grants(
                'model',
                'invented-helper',
                [
                    {'principal_type': 'user', 'principal_id': 'reader', 'permission': 'read'},
                    {'principal_type': 'user', 'principal_id': 'writer', 'permission': 'write'},
                ],
                db=db,
            )

    seeded.run(insert)
    return seeded


def attach(folder, principal='owner', **fields):
    return folder.client.put(
        PREFIX + '/skill-files',
        headers=folder.headers(principal),
        json={'path': 'assets/invented.bin', 'file_id': 'owner-upload', **fields},
    )


def test_owner_and_shared_reader_export_without_individual_file_access(folder):
    assert attach(folder).status_code == 200
    for principal in ('owner', 'reader', 'writer'):
        listing = folder.client.get(PREFIX + '/skill-files', headers=folder.headers(principal))
        assert listing.status_code == 200
        assert listing.json() == [{'path': 'assets/invented.bin', 'file_id': 'owner-upload', 'size': 10}]
        exported = folder.client.get(PREFIX + '/skill', headers=folder.headers(principal))
        assert exported.status_code == 200
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            assert archive.read('assets/invented.bin') == b'\x00\xffinvented'
            assert archive.read('SKILL.md').endswith(b'Invented instructions')
    # The general file route remains protected, even though assistant export delegates these bytes.
    assert folder.client.get('/api/v1/files/owner-upload/content', headers=folder.headers('reader')).status_code == 404


@pytest.mark.parametrize('principal', ['outsider', 'admin', 'reader'])
def test_cross_user_write_refusals_leave_rows_unchanged(folder, principal):
    assert attach(folder).status_code == 200
    assert attach(folder, principal).status_code == 403
    assert (
        folder.client.delete(
            PREFIX + '/skill-files', params={'path': 'assets/invented.bin'}, headers=folder.headers(principal)
        ).status_code
        == 403
    )
    assert len(folder.client.get(PREFIX + '/skill-files', headers=folder.headers('owner')).json()) == 1


@pytest.mark.parametrize('principal', ['outsider', 'admin'])
def test_private_list_and_export_refuse_ungranted_users(folder, principal):
    for suffix in ('/skill-files', '/skill'):
        assert folder.client.get(PREFIX + suffix, headers=folder.headers(principal)).status_code == 403


def test_writer_can_manage_owner_uploads_but_cannot_attach_their_own(folder):
    assert attach(folder, 'writer', file_id='writer-upload').status_code == 403
    assert attach(folder, 'writer', file_id='outsider-upload').status_code == 403
    assert attach(folder, 'writer').status_code == 200
    assert attach(folder, 'writer', from_path='assets/invented.bin', path='scripts/renamed.bin').status_code == 200
    assert (
        folder.client.get(PREFIX + '/skill-files', headers=folder.headers('owner')).json()[0]['path']
        == 'scripts/renamed.bin'
    )
    assert (
        folder.client.delete(
            PREFIX + '/skill-files', params={'path': 'scripts/renamed.bin'}, headers=folder.headers('writer')
        ).status_code
        == 200
    )


def test_revoked_reader_and_writer_lose_all_folder_access(folder):
    assert attach(folder).status_code == 200

    async def revoke():
        async with folder.sessions() as db:
            await AccessGrants.revoke_all_access('model', 'invented-helper', db=db)

    folder.run(revoke)
    for principal in ('reader', 'writer'):
        for suffix in ('/skill-files', '/skill'):
            assert folder.client.get(PREFIX + suffix, headers=folder.headers(principal)).status_code == 403
        assert attach(folder, principal).status_code == 403


@pytest.mark.parametrize(
    'path', ['', '/absolute', 'a//b', 'a/./b', 'a/../b', 'a\\b', 'C:/a', 'SKILL.md', 'skill.MD', 'soev.json', 'x' * 256]
)
def test_invalid_paths_are_rejected_without_mutation(folder, path):
    assert attach(folder, path=path).status_code in (400, 422)
    assert folder.client.get(PREFIX + '/skill-files', headers=folder.headers('owner')).json() == []


def test_replacement_and_case_collision(folder):
    assert attach(folder).status_code == 200
    assert attach(folder).status_code == 200
    assert attach(folder, path='assets/INVENTED.bin').status_code == 400
    assert len(folder.client.get(PREFIX + '/skill-files', headers=folder.headers('owner')).json()) == 1


@pytest.mark.parametrize('target', ['assistant', 'file'])
def test_deleting_parent_cleans_folder_rows_even_without_sqlite_cascades(folder, target):
    assert attach(folder).status_code == 200

    async def remove():
        async with folder.sessions() as db:
            if target == 'assistant':
                assert await Models.delete_model_by_id('invented-helper', db=db)
            else:
                assert await Files.delete_file_by_id('owner-upload', db=db)
            assert await db.scalar(select(func.count()).select_from(AssistantSkillFile)) == 0

    folder.run(remove)


@pytest.mark.parametrize('language,expected', [('en', '25 MiB'), ('nl', 'niet groter')])
def test_oversize_admission_is_localized_and_keeps_existing_rows(folder, monkeypatch, language, expected):
    from open_webui.routers import assistant_skill_files

    assert attach(folder).status_code == 200
    monkeypatch.setattr(assistant_skill_files, 'MAX_COMPRESSED', 1)
    result = folder.client.put(
        PREFIX + '/skill-files',
        headers={**folder.headers('owner'), 'Accept-Language': language},
        json={'path': 'assets/second.bin', 'file_id': 'owner-upload'},
    )
    assert result.status_code == 400 and expected in result.json()['detail']
    assert len(folder.client.get(PREFIX + '/skill-files', headers=folder.headers('owner')).json()) == 1


def test_entry_cap_is_checked_before_storage_and_keeps_rows(folder, monkeypatch):
    from open_webui.utils import assistant_skill_bundles

    assert attach(folder).status_code == 200
    monkeypatch.setattr(assistant_skill_bundles, 'MAX_ENTRIES', 3)
    result = attach(folder, path='assets/second.bin')
    assert result.status_code == 400 and '498' in result.json()['detail']
    assert len(folder.client.get(PREFIX + '/skill-files', headers=folder.headers('owner')).json()) == 1


def test_file_versions_include_owner_without_loading_file_content(folder):
    async def versions():
        async with folder.sessions() as db:
            return await AssistantSkillFiles.file_versions(['owner-upload'], db=db)

    assert folder.run(versions) == {'owner-upload': ['owner', None, 1, 10]}


def test_admission_checks_actual_bytes_when_size_metadata_is_wrong(folder, monkeypatch):
    from open_webui.routers import assistant_skill_files
    from sqlalchemy import update

    async def wrong_size():
        async with folder.sessions() as db:
            await db.execute(update(File).where(File.id == 'owner-upload').values(meta={'size': 0}))
            await db.commit()

    folder.run(wrong_size)
    monkeypatch.setattr(assistant_skill_files, 'MAX_COMPRESSED', 1)
    assert attach(folder).status_code == 400
    assert folder.client.get(PREFIX + '/skill-files', headers=folder.headers('owner')).json() == []


def test_existing_assistant_ids_with_slashes_can_use_the_folder_routes(folder):
    async def insert():
        async with folder.sessions() as db:
            db.add(
                Model(
                    id='invented/team-helper',
                    user_id='owner',
                    base_model_id='invented-llm',
                    name='Invented Team Helper',
                    params={'system': 'Invented instructions'},
                    meta={'description': 'Invented description'},
                    created_at=1,
                    updated_at=1,
                )
            )
            await db.commit()

    folder.run(insert)
    prefix = '/api/v1/models/id/invented%2Fteam-helper'
    headers = folder.headers('owner')
    assert (
        folder.client.put(
            prefix + '/skill-files', headers=headers, json={'path': 'scripts/invented.py', 'file_id': 'owner-upload'}
        ).status_code
        == 200
    )
    assert len(folder.client.get(prefix + '/skill-files', headers=headers).json()) == 1
    assert folder.client.get(prefix + '/skill', headers=headers).status_code == 200
    assert (
        folder.client.delete(
            prefix + '/skill-files', headers=headers, params={'path': 'scripts/invented.py'}
        ).status_code
        == 200
    )


@pytest.mark.parametrize('method', ['by_user', 'offboard_assistant', 'offboard_file', 'restore_model'])
def test_bulk_and_offboarding_deletions_clean_folder_rows_on_sqlite(folder, method):
    import json
    from types import SimpleNamespace

    from open_webui.services.deletion.offboarding_store import delete_resource_dependents
    from open_webui.soev import migrate_models
    from sqlalchemy import delete

    assert attach(folder).status_code == 200

    async def remove():
        async with folder.sessions() as db:
            if method == 'by_user':
                assert await Models.delete_models_by_user_id('owner', db=db)
            elif method == 'restore_model':
                await migrate_models._restore_prompt(
                    db,
                    migrate_models.PROMPT_CREATED,
                    'invented-helper',
                    [SimpleNamespace(new_value=json.dumps('Invented instructions'))],
                    migrate_models.Report(),
                )
            else:
                table = Model if method == 'offboard_assistant' else File
                id = 'invented-helper' if table is Model else 'owner-upload'
                await delete_resource_dependents(table, [id], db=db)
                await db.execute(delete(table).where(table.id == id))
            await db.commit()
            assert await db.scalar(select(func.count()).select_from(AssistantSkillFile)) == 0

    folder.run(remove)


@pytest.mark.parametrize('language,message', [('en', 'Ask the owner'), ('nl', 'Vraag de eigenaar')])
def test_nonowner_writer_gets_a_localized_owner_upload_refusal(folder, language, message):
    response = folder.client.put(
        PREFIX + '/skill-files',
        headers={**folder.headers('writer'), 'Accept-Language': language},
        json={'path': 'scripts/invented.py', 'file_id': 'writer-upload'},
    )
    assert response.status_code == 403
    assert message in response.json()['detail']
    assert folder.client.get(PREFIX + '/skill-files', headers=folder.headers('owner')).json() == []
