"""Cutover cleanup, independent of application config and its Alembic startup."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def delete_original(path: str, upload_dir: Path) -> None:
    try:
        target = urlsplit(path)
        if target.scheme == 's3':
            import boto3
            from botocore.config import Config

            if target.netloc != os.environ['S3_BUCKET_NAME']:
                raise ValueError
            client = boto3.client(
                's3',
                aws_access_key_id=os.getenv('S3_ACCESS_KEY_ID'),
                aws_secret_access_key=os.getenv('S3_SECRET_ACCESS_KEY'),
                region_name=os.getenv('S3_REGION_NAME'),
                endpoint_url=os.getenv('S3_ENDPOINT_URL'),
                verify=os.getenv('S3_VERIFY_SSL', 'true').lower() == 'true',
                config=Config(
                    s3={
                        'use_accelerate_endpoint': os.getenv('S3_USE_ACCELERATE_ENDPOINT', 'false').lower() == 'true',
                        'addressing_style': os.getenv('S3_ADDRESSING_STYLE') or 'auto',
                    }
                ),
            )
            client.delete_object(Bucket=target.netloc, Key=target.path.lstrip('/'))
        elif target.scheme == 'gs':
            from google.cloud import storage
            from google.cloud.exceptions import NotFound

            if target.netloc != os.environ['GCS_BUCKET_NAME']:
                raise ValueError
            credentials = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
            client = (
                storage.Client.from_service_account_info(json.loads(credentials)) if credentials else storage.Client()
            )
            try:
                client.bucket(target.netloc).blob(target.path.lstrip('/')).delete()
            except NotFound:
                pass
        elif target.scheme in ('http', 'https'):
            from azure.core.exceptions import ResourceNotFoundError
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient

            if target.netloc != urlsplit(os.environ['AZURE_STORAGE_ENDPOINT']).netloc or not target.path.startswith(
                '/' + os.environ['AZURE_STORAGE_CONTAINER_NAME'] + '/'
            ):
                raise ValueError
            client = BlobServiceClient(
                account_url=os.environ['AZURE_STORAGE_ENDPOINT'],
                credential=os.getenv('AZURE_STORAGE_KEY') or DefaultAzureCredential(),
            )
            try:
                client.get_blob_client(os.environ['AZURE_STORAGE_CONTAINER_NAME'], Path(target.path).name).delete_blob()
            except ResourceNotFoundError:
                pass
        elif target.scheme:
            raise ValueError
        # Storage providers cache under the basename, never under a caller-selected path.
        (upload_dir / Path(target.path).name).unlink(missing_ok=True)
    except Exception:
        raise RuntimeError('Document byte cleanup failed') from None
