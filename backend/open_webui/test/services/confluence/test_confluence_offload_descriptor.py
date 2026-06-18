"""ConfluenceSyncWorker._item_from_file_info — loader-worker offload descriptor.

Pins the per-mode credential contract the loader-worker's ConfluenceSourceClient
consumes: ``scoped`` emits ``credential_type='scoped_token'`` + ``cloud_id`` +
the ``email:token`` pair; ``basic`` emits ``basic_auth`` + ``site_url``; ``oauth``
keeps the base ``user_oauth`` shape. The worker is built with ``__new__`` to skip
``BaseSyncWorker.__init__``; the base item shape is stubbed so only the
Confluence-specific descriptor logic is exercised.
"""

from __future__ import annotations

from unittest.mock import patch

from open_webui.services.confluence.sync_worker import ConfluenceSyncWorker
from open_webui.services.sync.base_worker import BaseSyncWorker


def _base_item(self, file_info, access_token):
    # Minimal stand-in for BaseSyncWorker._item_from_file_info — the confluence
    # override mutates this dict in place.
    return {
        'source': 'confluence',
        'source_credential': access_token,
        'credential_type': 'user_oauth',
        'file_id': 'confluence-p1',
    }


def _worker(auth_mode: str) -> ConfluenceSyncWorker:
    worker = ConfluenceSyncWorker.__new__(ConfluenceSyncWorker)
    worker._auth_mode = auth_mode
    return worker


def _item_for(auth_mode: str, file_info: dict):
    worker = _worker(auth_mode)
    with patch.object(BaseSyncWorker, '_item_from_file_info', new=_base_item):
        return worker._item_from_file_info(file_info, 'sentinel-token')


def test_scoped_emits_scoped_token_descriptor():
    file_info = {'page_id': 'p1', 'cloud_id': 'cloud-9', 'item': {}}
    with patch(
        'open_webui.services.confluence.sync_worker.scoped_auth_credential',
        return_value='svc@acme.com:scoped-secret',
    ):
        item = _item_for('scoped', file_info)

    assert item['credential_type'] == 'scoped_token'
    assert item['source_credential'] == 'svc@acme.com:scoped-secret'
    assert item['source_descriptor']['cloud_id'] == 'cloud-9'
    assert item['source_descriptor']['page_id'] == 'p1'
    # Scoped uses the gateway (cloudId), never a site_url like basic.
    assert 'site_url' not in item['source_descriptor']
    assert item['content_type'] == 'text/markdown'


def test_basic_still_emits_basic_auth_descriptor():
    file_info = {'page_id': 'p1', 'cloud_id': 'ignored', 'item': {}}
    with (
        patch(
            'open_webui.services.confluence.sync_worker.basic_auth_credential',
            return_value='svc@acme.com:classic-token',
        ),
        patch(
            'open_webui.services.confluence.sync_worker.get_basic_site',
            return_value={'url': 'https://acme.atlassian.net', 'cloud_id': 'acme.atlassian.net'},
        ),
    ):
        item = _item_for('basic', file_info)

    assert item['credential_type'] == 'basic_auth'
    assert item['source_credential'] == 'svc@acme.com:classic-token'
    assert item['source_descriptor']['site_url'] == 'https://acme.atlassian.net'


def test_oauth_keeps_user_oauth_descriptor():
    file_info = {'page_id': 'p1', 'cloud_id': 'cloud-9', 'item': {}}
    item = _item_for('oauth', file_info)

    assert item['credential_type'] == 'user_oauth'
    assert item['source_descriptor']['cloud_id'] == 'cloud-9'
