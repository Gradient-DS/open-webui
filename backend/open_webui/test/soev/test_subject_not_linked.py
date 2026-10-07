"""soev-api's subject_not_linked reaches the browser as a typed code, never a generic error."""

import pytest
from fastapi import HTTPException
from open_webui.soev.client import SoevApiError


@pytest.mark.asyncio
async def test_cloud_sync_passes_subject_not_linked_through_as_its_code(chat_http, monkeypatch):
    from open_webui.routers import cloud_sync

    async def acting_ref(user, client):
        return f'owui:user:{user.id}'

    monkeypatch.setattr(cloud_sync.identity, 'acting_ref', acting_ref)
    dependency = cloud_sync.cloud_sync(user=type('User', (), {'id': 'alice'})())
    await anext(dependency)
    with pytest.raises(HTTPException) as raised:
        await dependency.athrow(SoevApiError(403, 'subject_not_linked', 'Subject is not linked'))
    assert raised.value.status_code == 403
    assert raised.value.detail == {'code': 'subject_not_linked', 'detail': 'Subject is not linked'}
