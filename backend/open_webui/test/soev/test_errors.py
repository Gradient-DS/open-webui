"""Platform refusals preserve their contract without disclosing problem details."""

import importlib

import httpx
import pytest
from fastapi import FastAPI
from open_webui.soev.client import SoevApiError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'status,code,constraint,english,dutch',
    [
        (
            403,
            'scope_insufficient',
            'collection:access_owner',
            'Only the owner can change access to this knowledge base.',
            'Alleen de eigenaar kan de toegang tot deze kennisbank wijzigen.',
        ),
        (
            403,
            'policy_forbids',
            'synced_collection:not_shareable',
            'Synced knowledge bases cannot be shared.',
            'Gesynchroniseerde kennisbanken kunnen niet worden gedeeld.',
        ),
        (
            422,
            'invalid_field',
            'principals:max_64',
            'You can share with up to 64 users or groups. Use a group or everyone for wider access.',
            'Je kunt delen met maximaal 64 gebruikers of groepen. Gebruik een groep of iedereen voor ruimere toegang.',
        ),
        (
            409,
            'future_code',
            None,
            'The platform could not complete this request.',
            'Het platform kon dit verzoek niet uitvoeren.',
        ),
    ],
)
@pytest.mark.parametrize('language', ['en-US', 'nl-NL'])
async def test_registered_platform_error_handler(status, code, constraint, english, dutch, language):
    main = importlib.import_module('open_webui.main')
    app = FastAPI()
    handler = main.app.exception_handlers.get(SoevApiError)
    assert handler is not None
    app.add_exception_handler(SoevApiError, handler)

    @app.get('/refusal')
    async def refusal():
        raise SoevApiError(status, code, 'private-file-cinder.pdf', constraint)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/refusal', headers={'Accept-Language': language})
    assert response.status_code == status
    assert response.json()['detail'] == {
        'code': code,
        'constraint': constraint,
        'detail': dutch if language == 'nl-NL' else english,
    }
    assert 'private-file-cinder.pdf' not in response.text
