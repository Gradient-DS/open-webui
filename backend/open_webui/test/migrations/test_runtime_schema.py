import json
import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import create_engine, text


@pytest.fixture
def role_database(postgres_database):
    suffix = uuid.uuid4().hex
    owner, migrator, runtime = (f'raven_{kind}_{suffix}' for kind in ('admin', 'migrator', 'owui'))
    engine = create_engine(postgres_database)
    with engine.begin() as db:
        db.execute(text(f'CREATE ROLE {owner} NOLOGIN'))
        db.execute(text(f"CREATE ROLE {migrator} LOGIN NOINHERIT PASSWORD 'fictional-migrator'"))
        db.execute(text(f"CREATE ROLE {runtime} LOGIN PASSWORD 'fictional-runtime'"))
        db.execute(text(f'GRANT {owner} TO {migrator}'))
        db.execute(text('REVOKE CREATE ON SCHEMA public FROM PUBLIC'))
        db.execute(text(f'GRANT USAGE, CREATE ON SCHEMA public TO {owner}'))
        db.execute(text(f'GRANT USAGE ON SCHEMA public TO {runtime}'))
        db.execute(text('CREATE EXTENSION vector'))
        db.execute(text('CREATE EXTENSION pgcrypto'))
    try:
        yield (
            engine,
            postgres_database.set(username=migrator, password='fictional-migrator').update_query_dict(
                {'options': f'-c role={owner}'}
            ),
            postgres_database.set(username=runtime, password='fictional-runtime'),
            runtime,
        )
    finally:
        with engine.begin() as db:
            db.execute(text(f'DROP OWNED BY {owner}, {migrator}, {runtime} CASCADE'))
            db.execute(text(f'DROP ROLE {migrator}, {runtime}, {owner}'))
        engine.dispose()


def initialize(url, migrations, command=None, **settings):
    env = {
        **os.environ,
        'DATABASE_URL': url.render_as_string(hide_password=False),
        'PGVECTOR_DB_URL': url.render_as_string(hide_password=False),
        'ENABLE_DB_MIGRATIONS': str(migrations).lower(),
        'PGVECTOR_CREATE_EXTENSION': 'false',
        'VECTOR_DB': 'pgvector',
        'PGVECTOR_INITIALIZE_MAX_VECTOR_LENGTH': '3',
        **settings,
    }
    return subprocess.run(
        command
        or [
            sys.executable,
            '-c',
            'from open_webui.retrieval.vector.dbs.pgvector import PgvectorClient; '
            'client = PgvectorClient(); client.session.remove()',
        ],
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )


@pytest.mark.parametrize(
    'settings',
    [
        {},
        {'PGVECTOR_INDEX_METHOD': 'hnsw'},
        {'PGVECTOR_USE_HALFVEC': 'true', 'PGVECTOR_INDEX_METHOD': 'hnsw'},
        {'PGVECTOR_PGCRYPTO': 'true', 'PGVECTOR_PGCRYPTO_KEY': 'fictional-encryption-key'},
    ],
)
def test_runtime_init_with_dml_only(role_database, settings):
    engine, migration_url, runtime_url, runtime = role_database
    result = initialize(migration_url, True, **settings)
    assert result.returncode == 0, result.stderr
    with engine.begin() as db:
        db.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {runtime}'))
        db.execute(text(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {runtime}'))
    restricted = create_engine(runtime_url)
    try:
        with restricted.begin() as db:
            assert not db.execute(text("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")).scalar()
            assert not db.execute(
                text(
                    "SELECT pg_has_role(current_user, relowner, 'USAGE') FROM pg_class "
                    "WHERE oid = 'document_chunk'::regclass"
                )
            ).scalar()
            db.execute(text("INSERT INTO document_chunk (id, collection_name) VALUES ('raven-chunk', 'raven')"))
            db.execute(text("UPDATE document_chunk SET collection_name = 'finch' WHERE id = 'raven-chunk'"))
            assert db.execute(text('SELECT count(*) FROM document_chunk')).scalar() == 1
            db.execute(text('DELETE FROM document_chunk'))
        result = initialize(runtime_url, False, **{**settings, 'PGVECTOR_CREATE_EXTENSION': 'true'})
        assert result.returncode == 0, result.stderr
    finally:
        restricted.dispose()


@pytest.mark.parametrize(
    ('ddl', 'missing'),
    [
        ('DROP TABLE document_chunk', 'table document_chunk'),
        ('DROP INDEX idx_document_chunk_vector', 'index idx_document_chunk_vector'),
        ('DROP INDEX idx_document_chunk_text_search', 'index idx_document_chunk_text_search'),
        ('DROP INDEX idx_document_chunk_collection_name', 'index idx_document_chunk_collection_name'),
        ('ALTER TABLE document_chunk DROP COLUMN vmetadata', 'column document_chunk.vmetadata'),
        ('DROP EXTENSION vector CASCADE', 'extension vector'),
    ],
)
def test_runtime_init_names_missing_object(role_database, ddl, missing):
    engine, migration_url, runtime_url, runtime = role_database
    result = initialize(migration_url, True)
    assert result.returncode == 0, result.stderr
    with engine.begin() as db:
        db.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {runtime}'))
        db.execute(text(ddl))
    result = initialize(runtime_url, False)
    assert result.returncode != 0
    assert f'Missing database object {missing}' in result.stderr
    assert 'migration login before starting Open WebUI' in result.stderr


def test_migration_entrypoint_then_restricted_startup(role_database, tmp_path):
    engine, migration_url, runtime_url, runtime = role_database
    # Prove the real -m entry point does not continue into application configuration.
    (tmp_path / 'sitecustomize.py').write_text(
        'import sys\n'
        'class RefuseApplicationConfig:\n'
        '    def find_spec(self, fullname, *args):\n'
        '        if fullname == "open_webui.config":\n'
        '            raise ImportError("Application config must not be imported by migration CLI")\n'
        'sys.meta_path.insert(0, RefuseApplicationConfig())\n'
    )
    command = [sys.executable, '-m', 'open_webui.soev.migrate']
    settings = {
        'PYTHONPATH': f'{tmp_path}{os.pathsep}{os.environ["PYTHONPATH"]}',
        'DATABASE_TYPE': 'postgresql',
        'DATABASE_USER': migration_url.username,
        'DATABASE_PASSWORD': migration_url.password,
        'DATABASE_HOST': migration_url.host,
        'DATABASE_PORT': str(migration_url.port),
        'DATABASE_NAME': migration_url.database,
        'PGVECTOR_DB_URL': migration_url.difference_update_query(['options']).render_as_string(hide_password=False),
        'PGOPTIONS': migration_url.query['options'],
        'AGENT_API_ENABLED': 'true',
        'SOEV_API_URL': 'https://platform.raven.invalid',
    }
    # The parts must win even when an inherited runtime URL is present.
    result = initialize(runtime_url, False, command=command, **settings)
    assert result.returncode == 0, result.stderr
    counts = json.loads(result.stdout)
    assert counts['alembic_upgrades'] > 0
    assert counts['pgvector_tables'] == 1
    assert counts['pgvector_indexes'] == 3
    assert result.stderr == ''
    result = initialize(runtime_url, False, command=command, **settings)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {**counts, 'alembic_upgrades': 0}
    with engine.begin() as db:
        db.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {runtime}'))
        db.execute(text(f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {runtime}'))
    result = initialize(
        runtime_url,
        False,
        command=[sys.executable, '-c', 'from open_webui.retrieval.vector.factory import VECTOR_DB_CLIENT'],
        AGENT_API_ENABLED='true',
        SOEV_API_URL='https://platform.raven.invalid',
        SOEV_ARCHIVE_READERS_GROUP_REF='platform:group:00000000-0000-4000-8000-000000000001',
        SOEV_GROUP_SNAPSHOT_MAX_AGE_SECONDS='5',
    )
    assert result.returncode == 0, result.stderr


def test_migration_entrypoint_does_not_initialize_other_vector_backends(role_database):
    _, migration_url, _, _ = role_database
    result = initialize(
        migration_url,
        False,
        command=[sys.executable, '-m', 'open_webui.soev.migrate'],
        VECTOR_DB='weaviate',
        WEAVIATE_HTTP_HOST='unreachable.raven.invalid',
    )
    assert result.returncode == 0, result.stderr
    counts = json.loads(result.stdout)
    assert counts['alembic_upgrades'] > 0
    assert counts['pgvector_tables'] == counts['pgvector_indexes'] == 0
    assert result.stderr == ''
