"""Document cutover deletes only original document holdings, on SQLite and PostgreSQL."""

import importlib
import os
from pathlib import Path
from unittest.mock import Mock

import pytest
import sqlalchemy as sa

migration = importlib.import_module('open_webui.migrations.versions.f286d9b43a71_retire_document_originals')


@pytest.fixture(params=['sqlite', 'postgresql'])
def engine(request):
    if request.param == 'postgresql' and not os.getenv('BACKEND_TEST_DATABASE_URL'):
        pytest.skip('BACKEND_TEST_DATABASE_URL is not configured')
    url = request.getfixturevalue('postgres_database') if request.param == 'postgresql' else 'sqlite://'
    engine = sa.create_engine(url)
    yield engine
    engine.dispose()


def test_document_cleanup_preserves_non_documents_and_is_idempotent(engine, tmp_path):
    metadata = sa.MetaData()
    files = sa.Table(
        'file',
        metadata,
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('filename', sa.Text),
        sa.Column('path', sa.Text),
        sa.Column('meta', sa.JSON),
    )
    skills = sa.Table('skill_file', metadata, sa.Column('file_id', sa.Text))
    metadata.create_all(engine)
    rows = [
        ('legacy', {'content_type': 'application/pdf'}),
        ('old_collection_only', {'collection_name': 'file-old'}),
        ('ingested', {'soev_collection_key': 'kb', 'content_type': 'application/pdf'}),
        ('reference', {'source': {'provider': 'onedrive'}}),
        ('image', {'content_type': 'image/png'}),
        ('audio', {'content_type': 'audio/wav'}),
        ('uploaded_audio', {'content_type': 'audio/mpeg'}),
        ('skill', {'content_type': 'text/markdown'}),
        ('generated', {'content_type': 'application/octet-stream', 'local_blob': True}),
    ]
    for key, _ in rows:
        (tmp_path / key).write_bytes(b'original')
    with engine.begin() as bind:
        bind.execute(
            files.insert(),
            [
                {
                    'id': key,
                    'filename': 'generated-.wav' if key == 'audio' else key,
                    'path': str(tmp_path / key),
                    'meta': meta,
                }
                for key, meta in rows
            ],
        )
        bind.execute(skills.insert().values(file_id='skill'))
        counts = migration.retire_documents(bind, tmp_path)
        assert counts == {'legacy_documents_dropped': 3, 'document_paths_cleared': 2, 'document_objects_deleted': 5}
        saved = {r.id: r.path for r in bind.execute(sa.select(files))}
        assert set(saved) == {'ingested', 'reference', 'image', 'audio', 'skill', 'generated'}
        assert saved['ingested'] is saved['reference'] is None
        assert {p.name for p in tmp_path.iterdir()} == {'image', 'audio', 'skill', 'generated'}
        assert not any(migration.retire_documents(bind, tmp_path).values())


def test_cleanup_failure_does_not_clear_the_pointer(engine, tmp_path):
    with engine.begin() as bind:
        bind.execute(sa.text('CREATE TABLE file (id TEXT, filename TEXT, path TEXT, meta JSON)'))
        bind.execute(sa.text("INSERT INTO file VALUES ('doc', 'doc.pdf', 'secret/path', '{}')"))
        with pytest.raises(RuntimeError):
            migration.retire_documents(bind, tmp_path, delete=Mock(side_effect=RuntimeError('refused')))
        assert bind.execute(sa.text('SELECT path FROM file')).scalar() == 'secret/path'


def test_missing_local_original_is_idempotent_and_errors_are_sanitized(tmp_path, monkeypatch):
    from open_webui.soev.retire_document_bytes import delete_original

    delete_original(str(tmp_path / 'gone.pdf'), tmp_path)
    monkeypatch.setattr(Path, 'unlink', Mock(side_effect=PermissionError('secret/path')))
    with pytest.raises(RuntimeError, match='Document byte cleanup failed') as caught:
        delete_original(str(tmp_path / 'doc'), tmp_path)
    assert 'secret' not in str(caught.value)


def test_schema_command_includes_document_cleanup_counts(monkeypatch, capsys):
    import json
    from open_webui.soev import migrate

    def upgrade(*, document_counts):
        document_counts.update(legacy_documents_dropped=7, document_paths_cleared=9, document_objects_deleted=16)
        return 1

    monkeypatch.setattr(migrate.logging, 'disable', Mock())
    monkeypatch.setenv('VECTOR_DB', 'weaviate')
    monkeypatch.setattr(migrate, 'upgrade_alembic', upgrade)
    assert migrate.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        'alembic_upgrades': 1,
        'pgvector_tables': 0,
        'pgvector_indexes': 0,
        'legacy_documents_dropped': 7,
        'document_paths_cleared': 9,
        'document_objects_deleted': 16,
    }


def test_remote_cleanup_uses_the_declared_bucket_without_downloading(tmp_path, monkeypatch):
    import boto3
    from open_webui.soev.retire_document_bytes import delete_original

    client = Mock()
    factory = Mock(return_value=client)
    monkeypatch.setattr(boto3, 'client', factory)
    monkeypatch.setenv('S3_BUCKET_NAME', 'tenant')
    (tmp_path / 'document.pdf').write_bytes(b'cached')
    delete_original('s3://tenant/prefix/document.pdf', tmp_path)
    client.delete_object.assert_called_once_with(Bucket='tenant', Key='prefix/document.pdf')
    assert not (tmp_path / 'document.pdf').exists()
    client.get_object.assert_not_called()
    with pytest.raises(RuntimeError, match='Document byte cleanup failed'):
        delete_original('s3://another-tenant/document.pdf', tmp_path)
    assert factory.call_count == 1
