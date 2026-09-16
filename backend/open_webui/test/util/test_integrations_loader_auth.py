"""Shared loader-principal fixtures for integration route tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from open_webui.routers import integrations as integrations_router
from open_webui.utils.service_auth import LoaderPrincipal, get_integration_principal


@pytest.fixture
def acting_user_id() -> str:
    return 'lex-uuid-42'


@pytest.fixture
def loader_principal(acting_user_id):
    user = MagicMock()
    user.id = acting_user_id
    user.email = 'lex@gradient-ds.com'
    user.role = 'user'
    user.name = 'Lex'
    user.info = {'integration_provider': 'should-be-ignored'}
    return LoaderPrincipal(user=user, provider_slug='onedrive')


@pytest.fixture
def app(loader_principal, monkeypatch):
    """FastAPI app with the integrations router and the auth dep overridden."""
    app = FastAPI()
    app.include_router(integrations_router.router, prefix='/api/v1/integrations')

    # Keep provider reads independent of the config database.
    providers = {
        'onedrive': {
            'max_documents_per_request': 50,
            'max_files_per_kb': 1000,
            'custom_metadata_fields': [],
        }
    }

    async def fake_get(key, default=None):
        return {'integrations.providers': providers}.get(key, default)

    monkeypatch.setattr(integrations_router.Config, 'get', staticmethod(fake_get))

    # Inject the LoaderPrincipal directly — bypass the bearer/header check
    # (covered by test_service_auth.py) so this test focuses on user_id flow.
    app.dependency_overrides[get_integration_principal] = lambda: loader_principal

    return app
