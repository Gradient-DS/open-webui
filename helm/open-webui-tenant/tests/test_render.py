import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parents[1]
MIGRATION = {
    'postgres.migration.user': 'raven_migrator',
    'postgres.migration.passwordSecretRef.name': 'raven-migration',
    'postgres.migration.passwordSecretRef.key': 'password',
    'postgres.migration.role': 'raven_admin',
}


def render(settings=None):
    command = ['helm', 'template', 'raven', str(CHART)]
    for key, value in (settings or {}).items():
        command.extend(['--set-string', f'{key}={value}'])
    return subprocess.run(command, text=True, capture_output=True, check=False)


def objects(settings=None):
    result = render(settings)
    assert result.returncode == 0, result.stderr
    manifests = list(yaml.safe_load_all(result.stdout))
    deployment = next(m for m in manifests if m and m['kind'] == 'Deployment')
    config = next(m for m in manifests if m and m['kind'] == 'ConfigMap' and 'DATABASE_USER' in m['data'])
    return deployment['spec']['template']['spec'], config['data']


def env(container):
    return {entry['name']: entry for entry in container['env']}


def test_default_single_login_migrates_at_startup():
    pod, config = objects()
    assert all(c['name'] != 'migrate' for c in pod['initContainers'])
    assert config['ENABLE_DB_MIGRATIONS'] == 'true'
    assert env(pod['containers'][0])['DATABASE_PASSWORD']['valueFrom']['secretKeyRef']['key'] == 'postgres-password'


def test_separate_login_is_only_in_init_and_overrides_database_parts():
    pod, config = objects(MIGRATION)
    init = env(next(c for c in pod['initContainers'] if c['name'] == 'migrate'))
    app = env(pod['containers'][0])
    assert config['DATABASE_USER'] == 'openwebui'
    assert config['ENABLE_DB_MIGRATIONS'] == 'false'
    assert init['ENABLE_DB_MIGRATIONS']['value'] == 'true'
    assert app['ENABLE_DB_MIGRATIONS']['value'] == 'false'
    assert init['DATABASE_USER']['value'] == 'raven_migrator'
    assert init['DATABASE_PASSWORD']['valueFrom']['secretKeyRef'] == {'name': 'raven-migration', 'key': 'password'}
    for part in ('TYPE', 'HOST', 'PORT', 'NAME'):
        assert f'DATABASE_{part}' in init
    assert init['PGOPTIONS']['value'] == '-c role=raven_admin'
    assert init['PGVECTOR_DB_URL']['value'] == '$(DATABASE_URL)'
    assert 'PGOPTIONS' not in app
    assert 'raven_migrator' not in str(app)
    assert 'raven-migration' not in str(app)


def test_migration_role_is_optional():
    pod, _ = objects({**MIGRATION, 'postgres.migration.role': ''})
    assert 'PGOPTIONS' not in env(next(c for c in pod['initContainers'] if c['name'] == 'migrate'))


@pytest.mark.parametrize(
    'settings',
    [
        {'postgres.migration.user': 'raven_migrator'},
        {'postgres.migration.role': 'raven_admin'},
        {**MIGRATION, 'postgres.migration.role': 'raven_admin -c superuser=on'},
    ],
)
def test_incomplete_or_invalid_migration_values_are_rejected(settings):
    result = render(settings)
    assert result.returncode != 0
    assert 'postgres.migration.' in result.stderr
