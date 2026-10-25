"""Local user references and product credential validation."""

import httpx
import pytest


@pytest.fixture
def user(identity_config):
    """Use the real user model with a local identity."""
    identity, _ = identity_config
    return identity.UserModel(
        id='alice', email='alice@example.invalid', name='Alice', last_active_at=0, updated_at=0, created_at=0
    )


@pytest.mark.asyncio
async def test_acting_ref_uses_the_login_without_publishing_a_link(identity_config, identity_http, user):
    identity, _ = identity_config
    requests, responses = identity_http
    responses.extend(
        [
            httpx.Response(200, json={'data': [{'key': 'a'}], 'next_cursor': 'next'}),
            httpx.Response(200, json={'data': [{'key': 'b'}], 'next_cursor': None}),
        ]
    )
    client = identity.build_client()
    ref = identity.external_ref(user)
    assert ref == 'owui:user:alice'
    assert [item async for item in client.pages('/v1/collections', as_user=ref)] == [{'key': 'a'}, {'key': 'b'}]
    assert len(requests) == 2
    assert all(request.headers['X-Soev-User-Token'] == f'fictional-login:{ref}' for request in requests)
    assert all('X-Soev-' + 'Subject' not in request.headers for request in requests)


@pytest.mark.parametrize('secret', ['secret', 'secret_with_underscores'])
def test_credential_id_from_key(identity_config, secret):
    identity, _ = identity_config
    assert identity.credential_id_from_key(f'soev_test_cred-example_{secret}') == 'cred-example'


@pytest.mark.parametrize(
    'key',
    [
        '',
        'soev',
        'soev_test_cred-example',
        'other_test_cred-example_secret',
        '_test_cred-example_secret',
        'soev__cred-example_secret',
        'soev_test__secret',
        'soev_test_cred-example_',
        'soev_test_example_secret',
    ],
)
def test_malformed_credential_keys_are_rejected(identity_config, key):
    identity, _ = identity_config
    with pytest.raises(ValueError, match='SOEV_API_KEY must have the form'):
        identity.credential_id_from_key(key)
