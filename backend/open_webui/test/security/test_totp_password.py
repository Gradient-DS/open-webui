"""Password re-verification must finish before changing a second factor."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from open_webui.routers import totp


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['enable_totp', 'disable_totp', 'regenerate_recovery_codes'])
async def test_invalid_password_cannot_change_totp_or_recovery_codes(monkeypatch, operation):
    monkeypatch.setattr(totp.Config, 'get', AsyncMock(return_value=True))
    monkeypatch.setattr(
        totp.Auths,
        'get_auth_by_user_id',
        AsyncMock(
            return_value=SimpleNamespace(
                password='fictional-hash',
                totp_enabled=operation != 'enable_totp',
            )
        ),
    )
    verify = AsyncMock(return_value=False)
    monkeypatch.setattr(totp, 'verify_password', verify)
    mutation = AsyncMock(side_effect=AssertionError('Second factor changed'))
    monkeypatch.setattr(totp.Auths, 'update_totp', mutation)
    monkeypatch.setattr(totp.RecoveryCodes, 'generate_codes', mutation)
    monkeypatch.setattr(totp.RecoveryCodes, 'delete_all', mutation)
    code = Mock(side_effect=AssertionError('TOTP checked before the password'))
    monkeypatch.setattr(totp, 'verify_totp', code)
    kwargs = {
        'request': None,
        'user': SimpleNamespace(id='fictional-fern', oauth=None),
        'db': None,
        'form_data': SimpleNamespace(password='wrong-password'),
    }
    if operation == 'enable_totp':
        kwargs['response'] = None
    with pytest.raises(HTTPException) as caught:
        await getattr(totp, operation)(**kwargs)
    assert (caught.value.status_code, caught.value.detail) == (403, 'Invalid password')
    verify.assert_awaited_once_with('wrong-password', 'fictional-hash')
    mutation.assert_not_called()
    code.assert_not_called()
