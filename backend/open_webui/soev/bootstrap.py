"""Mint OWUI runtime authority and register its public signing key.
Run with python -m open_webui.soev.bootstrap; save the single JSON output as deployment secrets.
If the key is lost, revoke the credential and bootstrap again under a new SOEV_API_SIGNING_KID.
"""

import asyncio
import base64
import json
import os
import sys
from urllib.parse import quote
from uuid import uuid5

from cryptography.hazmat.primitives import serialization

from open_webui import config
from open_webui.soev import identity
from open_webui.soev.client import SoevApiError, SoevClient


async def bootstrap() -> None:
    mint_key = os.environ.get('SOEV_BOOTSTRAP_MINT_KEY', '')
    if not mint_key:
        raise ValueError('SOEV_BOOTSTRAP_MINT_KEY is required for this run')
    if not all((config.SOEV_API_URL, config.SOEV_API_SERVICE_PRINCIPAL, config.SOEV_API_SIGNING_KID)):
        raise ValueError('SOEV_API_URL, SOEV_API_SERVICE_PRINCIPAL and SOEV_API_SIGNING_KID are required')
    public_bytes = (
        identity.signing_key().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    jwk = {
        'kty': 'OKP',
        'crv': 'Ed25519',
        'x': base64.urlsafe_b64encode(public_bytes).decode().rstrip('='),
        'kid': config.SOEV_API_SIGNING_KID,
        'alg': 'Ed25519',
        'use': 'sig',
    }
    client = SoevClient(config.SOEV_API_URL, mint_key)
    operation = uuid5(
        identity.OWUI_PLATFORM_NAMESPACE,
        json.dumps([config.SOEV_API_SERVICE_PRINCIPAL, config.SOEV_API_SIGNING_KID]),
    )
    credential = await client.send(
        'POST',
        '/v1/credentials',
        {
            'label': 'Open WebUI',
            'principal': config.SOEV_API_SERVICE_PRINCIPAL,
            'claimable_principals': [config.SOEV_API_SERVICE_PRINCIPAL],
            'capabilities': ['read', 'ingest', 'delete', 'directory'],
            'claimable_sources': ['owui'],
        },
        idempotency_key=f'owui-bootstrap:{operation}',
    )
    credential_id = credential.get('id') if credential else None
    secret = credential.get('secret') if credential else None
    if not isinstance(credential_id, str) or not credential_id or not isinstance(secret, str):
        raise ValueError('soev-api did not return a credential id and plaintext key')
    if secret:
        print(json.dumps({'SOEV_API_KEY': secret, 'SOEV_API_CREDENTIAL_ID': credential_id}), flush=True)
    try:
        await client.send(
            'POST',
            f'/v1/credentials/{quote(credential_id, safe="")}/signing-keys',
            {'public_jwk': jwk},
            idempotency_key=f'owui-signing-key:{operation}',
        )
    except SoevApiError as error:
        if secret:
            raise ValueError(
                f'Signing-key registration failed (HTTP {error.status}); '
                'the credential was minted and printed; rerunning will register the key'
            ) from None
        raise
    if not secret:
        print(json.dumps({'SOEV_API_CREDENTIAL_ID': credential_id}), flush=True)
        print('The key was issued on the first run.', file=sys.stderr)


def main() -> None:
    try:
        asyncio.run(bootstrap())
    except SoevApiError as error:
        print(f'soev-api bootstrap failed (HTTP {error.status})', file=sys.stderr)
        raise SystemExit(1) from None
    except ValueError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
