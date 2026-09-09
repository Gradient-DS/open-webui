"""Exercise SDK sink discovery without importing cloud libraries."""

import ast

import pytest

from .test_runtime_egress import _module_fetches


@pytest.mark.parametrize(
    'source',
    [
        "import boto3\nboto3.client('s3')",
        "import boto3\nboto3.resource('s3')",
        "import boto3 as aws\naws.client('rds')",
        "from boto3 import resource as make\nmake('s3')",
        "from boto3 import client\nclient('s3vectors')",
        'from azure.storage.blob import BlobServiceClient\nBlobServiceClient(account_url=url)',
        'from azure.storage.blob import BlobServiceClient as Blob\nBlob.from_connection_string(secret)',
        'import azure.storage.blob as blob\nblob.ContainerClient(account_url=url)',
        'from azure.storage.blob import BlobClient\nBlobClient.from_blob_url(url)',
        'from azure.storage.blob.aio import BlobServiceClient as Blob\nBlob(account_url=url)',
        'from google.cloud import storage\nstorage.Client()',
        'from google.cloud import storage\nstorage.Client.from_service_account_info(info)',
        'from google.cloud.storage import Client as GCS\nGCS.from_service_account_json(path)',
        'import google.cloud.storage\ngoogle.cloud.storage.Client()',
        'from google.cloud.storage.client import Client\nClient.create_anonymous_client()',
    ],
)
def test_cloud_client_is_a_sink(source):
    assert _module_fetches(ast.parse(source))


@pytest.mark.parametrize(
    'source',
    [
        "from local_store import client, resource\nclient('s3')\nresource('s3')",
        'from local_store import storage\nstorage.Client()',
        'from local_store import BlobServiceClient\nBlobServiceClient(account_url=url)',
        'from local_store import Client\nClient.from_service_account_info(info)',
        'import boto3\nconfig = boto3.Config()',
        'from google.cloud import storage\nstorage.Bucket(client, name)',
        'from azure.storage.blob import BlobServiceClient\nfactory = BlobServiceClient',
        'from google.cloud import storage\nfactory = storage.Client',
    ],
)
def test_unrelated_names_and_non_constructor_uses_are_not_sinks(source):
    assert not _module_fetches(ast.parse(source))
