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


def render(settings=None, values=None):
    command = ['helm', 'template', 'raven', str(CHART)]
    if values:
        command.extend(['-f', str(CHART / 'ci' / values)])
    for key, value in (settings or {}).items():
        command.extend(['--set-string', f'{key}={value}'])
    return subprocess.run(command, text=True, capture_output=True, check=False)


def objects(settings=None, values=None):
    result = render(settings, values)
    assert result.returncode == 0, result.stderr
    manifests = list(yaml.safe_load_all(result.stdout))
    deployment = next(
        m
        for m in manifests
        if m
        and m['kind'] == 'Deployment'
        and any(c['name'] == 'open-webui' for c in m['spec']['template']['spec']['containers'])
    )
    config = next(m for m in manifests if m and m['kind'] == 'ConfigMap' and 'DATABASE_USER' in m['data'])
    return deployment['spec']['template']['spec'], config['data']


def env(container):
    return {entry['name']: entry for entry in container.get('env', [])}


def test_retired_settings_cannot_restore_owui_controls_or_credentials():
    pod, config = objects(
        {
            'openWebui.config.enable2fa': 'true',
            'openWebui.config.enableForgotPassword': 'true',
            'openWebui.config.enableOnedrivePersonal': 'true',
            'openWebui.config.enableConfluenceSync': 'true',
            'openWebui.config.distributedDocPipelineEnabled': 'true',
            'openWebui.config.featureTools': 'true',
            'openWebui.config.featureToolServers': 'true',
            'openWebui.config.dataRetentionTtlDays': '7',
            'secrets.confluenceOauthClientSecret': 'fictional-unused-secret',
            'secrets.pipelineApiKey': 'fictional-unused-key',
            'secrets.loaderIngestApiKey': 'fictional-worker-only-key',
            'secrets.syncApiKey': 'fictional-worker-only-key',
        }
    )
    assert (
        not {
            'ENABLE_2FA',
            'ENABLE_FORGOT_PASSWORD',
            'ENABLE_ONEDRIVE_PERSONAL',
            'ENABLE_CONFLUENCE_SYNC',
            'DISTRIBUTED_DOC_PIPELINE_ENABLED',
            'FEATURE_TOOLS',
            'FEATURE_TOOL_SERVERS',
            'DATA_RETENTION_TTL_DAYS',
        }
        & config.keys()
    )
    for container in (pod['containers'][0], next(c for c in pod['initContainers'] if c['name'] == 'migrate')):
        assert (
            not {'CONFLUENCE_OAUTH_CLIENT_SECRET', 'PIPELINE_API_KEY', 'LOADER_INGEST_API_KEY', 'SYNC_API_KEY'}
            & env(container).keys()
        )
    assert config['FEATURE_VOICE'] == 'False'
    assert config['ENABLE_SKILL_EXECUTION'] == 'False'
    assert config['ENABLE_WEB_SEARCH'] == 'true'
    assert config['ENABLE_API_KEYS'] == 'false'
    assert config['RAG_FILE_SNIFF_MODE'] == 'enforce'
    assert config['TENANT_NAME'] == 'default'


def test_default_single_login_migrates_only_in_init():
    pod, config = objects()
    init_container = next(c for c in pod['initContainers'] if c['name'] == 'migrate')
    init, app = env(init_container), env(pod['containers'][0])
    assert init_container['command'] == ['python', '-m', 'open_webui.soev.migrate']
    assert config['ENABLE_DB_MIGRATIONS'] == 'false'
    assert init['ENABLE_DB_MIGRATIONS']['value'] == 'true'
    assert app['ENABLE_DB_MIGRATIONS']['value'] == 'false'
    assert init['DATABASE_PASSWORD'] == app['DATABASE_PASSWORD']
    assert init['DATABASE_URL'] == app['DATABASE_URL']
    assert init['DATABASE_PASSWORD']['valueFrom']['secretKeyRef']['key'] == 'postgres-password'


@pytest.mark.parametrize(
    'values',
    [None, 'external-postgres-values.yaml', 'strict-egress-values.yaml', 'v2-values.yaml', 'migration-values.yaml'],
)
def test_only_migration_init_can_migrate_in_each_values_file(values):
    pod, config = objects(values=values)
    allowed = []
    for container in pod['initContainers'] + pod['containers']:
        effective = {}
        if container.get('envFrom'):
            effective.update(config)
        effective.update({name: item.get('value') for name, item in env(container).items()})
        if effective.get('ENABLE_DB_MIGRATIONS') == 'true':
            allowed.append(container['name'])
    assert allowed == ['migrate']
    init = next(c for c in pod['initContainers'] if c['name'] == 'migrate')
    assert init['command'] == ['python', '-m', 'open_webui.soev.migrate']
    assert config['ENABLE_DB_MIGRATIONS'] == 'false'


@pytest.mark.parametrize('settings', [{}, MIGRATION])
def test_extra_env_cannot_change_migration_flags(settings):
    pod, config = objects({**settings, 'extraEnv[0].name': 'ENABLE_DB_MIGRATIONS', 'extraEnv[0].value': 'true'})
    assert config['ENABLE_DB_MIGRATIONS'] == 'false'
    for container in (next(c for c in pod['initContainers'] if c['name'] == 'migrate'), pod['containers'][0]):
        flags = [e for e in container['env'] if e['name'] == 'ENABLE_DB_MIGRATIONS']
        assert flags == [{'name': 'ENABLE_DB_MIGRATIONS', 'value': str(container['name'] == 'migrate').lower()}]


def test_separate_login_is_only_in_init_and_overrides_database_parts():
    pod, config = objects(MIGRATION)
    init_container = next(c for c in pod['initContainers'] if c['name'] == 'migrate')
    assert init_container['command'] == ['python', '-m', 'open_webui.soev.migrate']
    init = env(init_container)
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


def test_init_shares_application_settings_and_secret_refs():
    pod, _ = objects(
        {
            **MIGRATION,
            'openWebui.config.telemetry.otel.enabled': 'true',
            'extraEnv[0].name': 'VECTOR_DB',
            'extraEnv[0].value': 'pgvector',
            'extraEnv[1].name': 'PGVECTOR_PGCRYPTO_KEY',
            'extraEnv[1].valueFrom.secretKeyRef.name': 'raven-vector',
            'extraEnv[1].valueFrom.secretKeyRef.key': 'key',
            'extraEnv[2].name': 'ENABLE_DB_MIGRATIONS',
            'extraEnv[2].value': 'true',
            'extraEnv[3].name': 'DATABASE_USER',
            'extraEnv[3].value': 'raven_runtime',
        }
    )
    init_container = next(c for c in pod['initContainers'] if c['name'] == 'migrate')
    app_container = pod['containers'][0]
    init, app = env(init_container), env(app_container)
    assert init_container['envFrom'] == app_container['envFrom']
    for name, entry in app.items():
        if name.startswith('DATABASE_') or name in ('ENABLE_DB_MIGRATIONS', 'POD_NAME', 'OTEL_RESOURCE_ATTRIBUTES'):
            continue
        assert init[name] == entry
    assert init['VECTOR_DB']['value'] == 'pgvector'
    assert init['PGVECTOR_PGCRYPTO_KEY']['valueFrom']['secretKeyRef']['name'] == 'raven-vector'
    assert init['DATABASE_USER']['value'] == 'raven_migrator'
    assert app['ENABLE_DB_MIGRATIONS']['value'] == 'false'
    assert init['ENABLE_DB_MIGRATIONS']['value'] == 'true'
    for container in (init_container, app_container):
        names = [entry['name'] for entry in container['env']]
        assert len(names) == len(set(names))


@pytest.mark.parametrize('settings', [{}, MIGRATION])
@pytest.mark.parametrize('attributes', ['', 'deployment.environment=raven'])
def test_app_keeps_original_otel_identity_without_giving_it_to_init(settings, attributes):
    pod, _ = objects(
        {
            **settings,
            'openWebui.config.telemetry.otel.enabled': 'true',
            'openWebui.config.telemetry.otel.resourceAttributes': attributes,
        }
    )
    app = env(pod['containers'][0])
    assert app['POD_NAME'] == {'name': 'POD_NAME', 'valueFrom': {'fieldRef': {'fieldPath': 'metadata.name'}}}
    assert app['OTEL_RESOURCE_ATTRIBUTES'] == {
        'name': 'OTEL_RESOURCE_ATTRIBUTES',
        'value': 'service.instance.id=$(POD_NAME)' + (f',{attributes}' if attributes else ''),
    }
    names = [entry['name'] for entry in pod['containers'][0]['env']]
    assert names.index('POD_NAME') < names.index('OTEL_RESOURCE_ATTRIBUTES')
    init = env(next(c for c in pod['initContainers'] if c['name'] == 'migrate'))
    assert 'POD_NAME' not in init
    assert 'OTEL_RESOURCE_ATTRIBUTES' not in init


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


def test_strict_egress_allows_public_platform_and_transfer_store():
    result = render(values='strict-egress-values.yaml')
    assert result.returncode == 0, result.stderr
    policies = [m for m in yaml.safe_load_all(result.stdout) if m and m['kind'] == 'CiliumNetworkPolicy']
    selectors = [s for p in policies for e in p['spec']['egress'] for s in e.get('toFQDNs', [])]
    names = {s.get('matchName') for s in selectors}
    assert {'api.tenant.example.invalid', 'transfers.example.invalid'} <= names
    assert '8020' not in result.stdout
    pod, _ = objects(values='strict-egress-values.yaml')
    assert all('retire_document_bytes' not in str(c) for c in pod['initContainers'])
