import base64
from unittest.mock import MagicMock

import pytest
from open_webui.services.email import graph_mail_client


class _Resp:
    status_code = 202
    headers: dict = {}

    def raise_for_status(self):
        return None


class _FakeClient:
    last_json: dict = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeClient.last_json = json
        return _Resp()


def _app():
    app = MagicMock()
    app.state.config.EMAIL_FROM_ADDRESS = 'no-reply@soev.ai'
    return app


async def _fake_token(app):
    return 'tok'


@pytest.mark.asyncio
async def test_send_mail_includes_attachments(monkeypatch):
    monkeypatch.setattr(graph_mail_client, 'get_mail_access_token', _fake_token)
    monkeypatch.setattr(graph_mail_client.httpx, 'AsyncClient', _FakeClient)

    attachments = [
        {
            '@odata.type': '#microsoft.graph.fileAttachment',
            'name': 'x.md',
            'contentType': 'text/markdown',
            'contentBytes': base64.b64encode(b'hi').decode('ascii'),
        }
    ]
    ok = await graph_mail_client.send_mail(_app(), 'to@x.nl', 'subj', '<p>body</p>', attachments=attachments)
    assert ok is True
    assert _FakeClient.last_json['message']['attachments'] == attachments


@pytest.mark.asyncio
async def test_send_mail_without_attachments_has_no_key(monkeypatch):
    monkeypatch.setattr(graph_mail_client, 'get_mail_access_token', _fake_token)
    monkeypatch.setattr(graph_mail_client.httpx, 'AsyncClient', _FakeClient)

    await graph_mail_client.send_mail(_app(), 'to@x.nl', 'subj', '<p>body</p>')
    assert 'attachments' not in _FakeClient.last_json['message']
