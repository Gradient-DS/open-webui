"""Tests for ``StorageProvider.get_presigned_url`` (distributed doc-pipeline).

Lives in its own module rather than in ``test_provider.py`` because the
``TestS3StorageProvider`` class there carries an ``__init__`` constructor,
which pytest refuses to collect — so methods added to it never run. These
are plain module-level ``@mock_aws`` functions, which pytest does collect.
"""

import boto3
import pytest
from moto import mock_aws

from open_webui.storage import provider


@mock_aws
def test_s3_get_presigned_url_targets_the_stored_object(monkeypatch, tmp_path):
    """The presigned GET URL points at the same bucket + key as the stored object."""
    monkeypatch.setattr(provider, 'UPLOAD_DIR', str(tmp_path))
    storage = provider.S3StorageProvider()
    storage.bucket_name = 'my-bucket'
    boto3.client('s3', region_name='us-east-1').create_bucket(Bucket='my-bucket')
    storage.s3_client.put_object(Bucket='my-bucket', Key='folder/doc.txt', Body=b'hello')

    url = storage.get_presigned_url('s3://my-bucket/folder/doc.txt', expires_in=900)

    assert 'my-bucket' in url
    # The full key (prefix + filename) must be presigned, not just the basename.
    assert 'folder/doc.txt' in url
    # A presigned query is present (SigV2 ``Signature=`` or SigV4 ``X-Amz-Signature=``).
    assert 'Signature' in url


def test_local_get_presigned_url_not_implemented():
    """Providers without out-of-band fetch raise NotImplementedError by default."""
    storage = provider.LocalStorageProvider()
    with pytest.raises(NotImplementedError):
        storage.get_presigned_url('s3://x/y.txt', expires_in=900)


def test_s3_get_presigned_put_url(monkeypatch):
    provider_ = provider.S3StorageProvider()
    called = {}

    def fake_generate(op, Params, ExpiresIn):
        called.update(op=op, Params=Params, ExpiresIn=ExpiresIn)
        return 'https://s3/put?sig=1'

    monkeypatch.setattr(provider_.s3_client, 'generate_presigned_url', fake_generate)
    url = provider_.get_presigned_put_url('s3://bucket/key.pdf', 3600, 'application/pdf')
    assert url == 'https://s3/put?sig=1'
    assert called['op'] == 'put_object'
    assert called['Params']['Key'].endswith('key.pdf')
