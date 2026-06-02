"""list_shared_kb_spaces — the OAuth enumeration branch (Confluence shared KB).

The shared-KB owner is intrinsic to the KB row (``kb.user_id``); pre-provision
the calling admin is the implicit owner (they are about to provision and
become the KB owner). These tests pin the OAuth branch of ``/shared/spaces``:
an unconnected effective owner yields a clear 400, and a connected effective
owner's spaces are aggregated across every site their token can reach. They
run without pytest-asyncio via asyncio.run.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi import HTTPException

from open_webui.routers import confluence_sync


class _FakeClient:
    """Stand-in for a ConfluenceClient — returns canned spaces, tracks close()."""

    def __init__(self, spaces: list):
        self._spaces = spaces
        self.closed = False

    async def list_all_spaces(self) -> list:
        return self._spaces

    async def close(self) -> None:
        self.closed = True


def _call(user_id: str = 'admin-1') -> dict:
    """Invoke the router coroutine directly with a placeholder admin user."""
    user = SimpleNamespace(id=user_id)
    return asyncio.run(confluence_sync.list_shared_kb_spaces(user=user))


def test_oauth_caller_not_connected_returns_400():
    """The calling admin has no stored token — surfaces as a clear 400."""

    async def _picker_client(_owner):
        raise HTTPException(401, 'No valid Confluence token. Please re-authorize.')

    with (
        mock.patch.object(confluence_sync, 'resolve_auth_mode', return_value='oauth'),
        # No shared KB exists yet → effective owner = calling admin.
        mock.patch.object(confluence_sync, '_find_shared_kb', return_value=None),
        mock.patch.object(confluence_sync.Users, 'get_user_by_id', return_value=object()),
        mock.patch.object(confluence_sync, '_picker_client', new=_picker_client),
    ):
        with pytest.raises(HTTPException) as exc:
            _call()

    assert exc.value.status_code == 400
    assert 'connect' in exc.value.detail.lower()


def test_oauth_invalid_owner_returns_400():
    """The effective owner id resolves to no user — clear 400."""
    with (
        mock.patch.object(confluence_sync, 'resolve_auth_mode', return_value='oauth'),
        mock.patch.object(confluence_sync, '_find_shared_kb', return_value=None),
        mock.patch.object(confluence_sync.Users, 'get_user_by_id', return_value=None),
    ):
        with pytest.raises(HTTPException) as exc:
            _call()

    assert exc.value.status_code == 400
    assert 'valid' in exc.value.detail.lower()


def test_oauth_connected_owner_aggregates_spaces_across_sites():
    """A connected owner's spaces are aggregated across every accessible site."""
    sites = [{'cloud_id': 'c1'}, {'cloud_id': 'c2'}]
    spaces_by_cloud = {
        'c1': [{'id': '1', 'key': 'A', 'name': 'Alpha', 'type': 'global'}],
        'c2': [{'id': '2', 'key': 'B', 'name': 'Beta', 'type': 'global'}],
    }
    built: list = []

    async def _picker_client(_owner):
        return 'token', sites

    async def _browse_client(_owner, cloud_id):
        client = _FakeClient(spaces_by_cloud[cloud_id])
        built.append(client)
        return client, 'https://example.atlassian.net'

    with (
        mock.patch.object(confluence_sync, 'resolve_auth_mode', return_value='oauth'),
        # KB exists → effective owner = kb.user_id ('owner-99'), distinct from
        # the calling admin to assert that we resolve against the KB row.
        mock.patch.object(
            confluence_sync,
            '_find_shared_kb',
            return_value=SimpleNamespace(id='kb-1', user_id='owner-99'),
        ),
        mock.patch.object(confluence_sync.Users, 'get_user_by_id', return_value=object()),
        mock.patch.object(confluence_sync, '_picker_client', new=_picker_client),
        mock.patch.object(confluence_sync, '_browse_client', new=_browse_client),
    ):
        result = _call()

    spaces = result['spaces']
    assert {s['id'] for s in spaces} == {'1', '2'}
    by_id = {s['id']: s for s in spaces}
    # Each space carries the cloud_id of the site it came from.
    assert by_id['1']['cloud_id'] == 'c1'
    assert by_id['2']['cloud_id'] == 'c2'
    # Every per-site client is closed, even though they all succeeded.
    assert built and all(c.closed for c in built)
