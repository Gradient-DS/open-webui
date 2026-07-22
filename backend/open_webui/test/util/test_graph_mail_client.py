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


def _patch_from_address(monkeypatch):
    """send_mail reads ``await Config.get('email.from_address')`` (the storage
    key of EMAIL_FROM_ADDRESS); patch the per-key read so no config DB is hit."""

    async def fake_get(key, default=None):
        return {'email.from_address': 'no-reply@soev.ai'}.get(key, default)

    monkeypatch.setattr(graph_mail_client.Config, 'get', staticmethod(fake_get))


def _app():
    return MagicMock()


async def _fake_token(app):
    return 'tok'


@pytest.mark.asyncio
async def test_send_mail_includes_attachments(monkeypatch):
    monkeypatch.setattr(graph_mail_client, 'get_mail_access_token', _fake_token)
    monkeypatch.setattr(graph_mail_client.httpx, 'AsyncClient', _FakeClient)
    _patch_from_address(monkeypatch)

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
    _patch_from_address(monkeypatch)

    await graph_mail_client.send_mail(_app(), 'to@x.nl', 'subj', '<p>body</p>')
    assert 'attachments' not in _FakeClient.last_json['message']


def test_render_document_email_includes_subject_and_branding():
    html_body = graph_mail_client.render_document_email('Concept-beschikking — Parkeren <PB1>')
    # Branded heading + soev.ai footer (anti-autolink word-joiner form).
    assert 'Je concept-beschikking staat klaar' in html_body
    assert graph_mail_client.APP_NAME_HTML in html_body
    # Agent-supplied subject is shown but HTML-escaped (no raw angle brackets).
    assert 'Concept-beschikking — Parkeren &lt;PB1&gt;' in html_body
    assert '<PB1>' not in html_body
