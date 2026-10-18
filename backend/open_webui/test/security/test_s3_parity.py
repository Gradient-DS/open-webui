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


def test_app_and_gateway_share_credentials_and_bucket(compose):
    services = compose['services']
    app = services['open-webui']['environment']
    s3 = services['s3']['environment']
    assert app['STORAGE_PROVIDER'] == 's3'
    assert app['S3_ENDPOINT_URL'] == 'http://s3:9000'
    assert app['S3_ADDRESSING_STYLE'] == 'path'
    assert app['S3_REGION_NAME'] == 'us-east-1'
    assert app['S3_ACCESS_KEY_ID'] == s3['ROOT_ACCESS_KEY_ID']
    assert app['S3_SECRET_ACCESS_KEY'] == s3['ROOT_SECRET_ACCESS_KEY']
    assert app['S3_BUCKET_NAME'] == s3['S3_BUCKET_NAME'] == 'ci-uploads'
    assert app['S3_ACCESS_KEY_ID'] and len(app['S3_SECRET_ACCESS_KEY']) >= 8
    assert app['DATABASE_ENABLE_IAM_TOKEN_AUTH'] == 'false'
    assert app['VECTOR_DB'] == 'weaviate'


def test_bucket_exists_before_the_gateway_listens_and_before_app_start(compose):
    services = compose['services']
    s3 = services['s3']
    assert services['open-webui']['depends_on']['s3']['condition'] == 'service_healthy'
    # On the posix backend a bucket is a directory under the gateway root. -e
    # makes a failed mkdir fatal, and exec hands the process to the gateway
    # only after the directory exists.
    assert s3['entrypoint'] == ['/bin/sh', '-ec']
    assert len(s3['command']) == 1
    commands = [shlex.split(line) for line in s3['command'][0].splitlines() if line.strip()]
    assert commands == [
        ['mkdir', '-p', '/data/$$S3_BUCKET_NAME'],
        ['exec', 'versitygw', '--port', ':9000', '--health', '/health', 'posix', '/data'],
    ]
    # Health requires both the listener and the bucket directory, so readiness
    # alone cannot release app startup.
    assert s3['healthcheck']['test'] == [
        'CMD-SHELL',
        'wget -q -O /dev/null http://127.0.0.1:9000/health && test -d /data/ci-uploads',
    ]


def test_gateway_image_pin_and_ephemeral_storage(compose):
    s3 = compose['services']['s3']
    assert s3['image'].startswith('ghcr.io/versity/versitygw@sha256:')
    assert '/data' in s3['tmpfs']
