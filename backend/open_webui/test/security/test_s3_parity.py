"""Check S3 startup and isolation contracts without running Docker or cloud SDKs."""

import shlex

from .test_deployed_config import DIVERGENCES, config_mismatches, deployed_values
from .test_runtime_egress import compose


def test_s3_provider_cannot_be_waived_back_to_local(compose):
    deployed = deployed_values()
    assert deployed['STORAGE_PROVIDER'] == 's3'
    assert 'STORAGE_PROVIDER' not in DIVERGENCES
    env = compose['services']['open-webui']['environment'].copy()
    env['STORAGE_PROVIDER'] = 'local'
    assert any(error.startswith('STORAGE_PROVIDER:') for error in config_mismatches(deployed, env))


def test_app_and_bucket_init_use_the_same_s3_credentials_and_bucket(compose):
    services = compose['services']
    app = services['open-webui']['environment']
    init = services['minio-init']['environment']
    minio = services['minio']['environment']
    assert app['STORAGE_PROVIDER'] == 's3'
    assert app['S3_ENDPOINT_URL'] == 'http://minio:9000'
    assert app['S3_ADDRESSING_STYLE'] == 'path'
    assert app['S3_REGION_NAME'] == 'us-east-1'
    assert app['S3_ACCESS_KEY_ID'] == init['S3_ACCESS_KEY_ID'] == minio['MINIO_ROOT_USER']
    assert app['S3_SECRET_ACCESS_KEY'] == init['S3_SECRET_ACCESS_KEY'] == minio['MINIO_ROOT_PASSWORD']
    assert app['S3_BUCKET_NAME'] == init['S3_BUCKET_NAME'] == 'ci-uploads'
    assert app['S3_ACCESS_KEY_ID'] and len(app['S3_SECRET_ACCESS_KEY']) >= 8
    assert app['DATABASE_ENABLE_IAM_TOKEN_AUTH'] == 'false'
    assert app['VECTOR_DB'] == 'weaviate'


def test_bucket_creation_and_verification_must_succeed_before_app_start(compose):
    services = compose['services']
    init = services['minio-init']
    assert services['open-webui']['depends_on']['minio-init']['condition'] == 'service_completed_successfully'
    assert services['open-webui']['depends_on']['minio']['condition'] == 'service_healthy'
    assert init['depends_on']['minio']['condition'] == 'service_healthy'
    assert init['restart'] == 'no'
    # -e makes alias, creation, and existence-check failures fatal. Pin the
    # command sequence so readiness alone or a swallowed error cannot pass.
    assert init['entrypoint'] == ['/bin/sh', '-ec']
    assert len(init['command']) == 1
    commands = [shlex.split(line) for line in init['command'][0].splitlines() if line.strip()]
    assert commands == [
        [
            'mc',
            'alias',
            'set',
            'ci',
            'http://minio:9000',
            '$$S3_ACCESS_KEY_ID',
            '$$S3_SECRET_ACCESS_KEY',
            '--api',
            'S3v4',
            '--path',
            'on',
        ],
        ['mc', 'mb', '--ignore-existing', 'ci/$$S3_BUCKET_NAME'],
        ['mc', 'stat', 'ci/$$S3_BUCKET_NAME'],
    ]


def test_minio_readiness_and_ephemeral_storage(compose):
    services = compose['services']
    minio = services['minio']
    assert minio['image'] == services['minio-init']['image']
    assert minio['image'].startswith('minio/minio:RELEASE.')
    assert minio['command'] == ['server', '/data']
    assert '/data' in minio['tmpfs']
    assert minio['healthcheck']['test'] == [
        'CMD',
        'curl',
        '--fail',
        '--silent',
        'http://127.0.0.1:9000/minio/health/ready',
    ]
