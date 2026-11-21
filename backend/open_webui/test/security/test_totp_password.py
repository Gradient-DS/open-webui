"""Removed second-factor routes cannot mutate secrets, even with credentials."""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from importlib import import_module

main = import_module('open_webui.main')
from open_webui.models.auths import Auths
from open_webui.models.recovery_codes import RecoveryCodes


@pytest.mark.parametrize('operation', ['totp/enable', 'totp/disable', 'recovery/regenerate'])
@pytest.mark.parametrize('password', ['wrong-password', 'Fictional-Valid-Password!'])
def test_password_cannot_restore_second_factor_mutations(monkeypatch, operation, password):
    mutation = AsyncMock(side_effect=AssertionError('Second factor changed'))
    monkeypatch.setattr(Auths, 'update_totp', mutation)
    monkeypatch.setattr(RecoveryCodes, 'generate_codes', mutation)
    monkeypatch.setattr(RecoveryCodes, 'delete_all', mutation)
    app = FastAPI()
    app.router.routes = list(main.app.router.routes)
    result = TestClient(app).post('/api/v1/auths/2fa/' + operation, json={'password': password})
    assert (result.status_code, result.json()) == (404, {'detail': 'Not Found'})
    assert 'set-cookie' not in result.headers
    mutation.assert_not_called()
