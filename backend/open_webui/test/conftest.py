"""Isolate application imports before any backend test module is collected."""

import os
import tempfile
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

os.environ.setdefault('STATIC_DIR', tempfile.mkdtemp(prefix='owui-test-static-'))

# Runtime security drives the configured container. The backend suite instead
# builds the real migration schema in a fresh database for each pytest worker.
if not os.environ.get('ATTACK_BASE_URL'):
    temporary = tempfile.mkdtemp(prefix='owui-backend-tests-')
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    os.environ['DATABASE_URL'] = f'sqlite:///{temporary}/test.db'
    os.environ['DATA_DIR'] = temporary
    os.environ['VECTOR_DB'] = 'weaviate'
    os.environ['OFFLINE_MODE'] = 'true'
    os.environ['ENV'] = 'prod'
    os.environ.setdefault('WEBUI_SECRET_KEY', 'test-secret-key')
    for part in ('TYPE', 'USER', 'PASSWORD', 'HOST', 'PORT', 'NAME'):
        os.environ[f'DATABASE_{part}'] = ''

    import open_webui.config  # noqa: F401
    from open_webui.retrieval.vector.dbs.weaviate_multitenancy import WeaviateClient

    # Routes construct this singleton on import. Tests supply their own vector
    # responses at the call site; an unpatched operation on this client fails.
    with patch.object(WeaviateClient, '__init__', return_value=None):
        import open_webui.retrieval.vector.factory  # noqa: F401


@pytest.fixture
def postgres_database():
    """A database per test keeps advisory locks and destructive DDL independent."""
    url = make_url(os.environ['BACKEND_TEST_DATABASE_URL'])
    name = f'owui_test_{uuid.uuid4().hex}'
    admin = create_engine(url, isolation_level='AUTOCOMMIT')
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE {name}'))
    try:
        yield url.set(database=name)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE {name} WITH (FORCE)'))
        admin.dispose()
