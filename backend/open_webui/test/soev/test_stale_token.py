from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
@pytest.mark.parametrize('auth_row', [None, SimpleNamespace(active=False)])
async def test_a_token_without_an_active_account_is_invalid(monkeypatch, auth_row):
    """A deleted or deactivated account's token is rejected as invalid instead of raising."""
    from open_webui.utils import auth, mfa

    monkeypatch.setattr(mfa.Auths, 'get_auth_by_id', AsyncMock(return_value=auth_row))

    assert await auth.is_valid_token({'id': 'gone-user', 'session_stamp': None}) is False


@pytest.mark.asyncio
async def test_a_database_failure_still_fails_closed(monkeypatch):
    """A failing account lookup propagates its 503 rather than reading as a valid or invalid token."""
    from open_webui.utils import auth, mfa
    from sqlalchemy.exc import OperationalError

    monkeypatch.setattr(mfa.Auths, 'get_auth_by_id', AsyncMock(side_effect=OperationalError('select', {}, None)))

    with pytest.raises(HTTPException) as e:
        await auth.is_valid_token({'id': 'any-user'})
    assert e.value.status_code == 503
