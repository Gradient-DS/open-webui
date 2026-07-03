import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from open_webui.routers import internal_retrieval as internal_retrieval_router
from open_webui.utils.service_auth import AgentPrincipal, get_agent_principal


def _principal(email='lex@gradient-ds.com'):
    user = MagicMock()
    user.id = 'user-uuid-1'
    user.email = email
    return AgentPrincipal(agent_id='langgraph_dev', user=user)


def _build_app(principal):
    app = FastAPI()
    app.include_router(internal_retrieval_router.router, prefix='/api/v1/internal/retrieval')
    app.state.config = SimpleNamespace()
    app.dependency_overrides[get_agent_principal] = lambda: principal
    return app


def test_email_document_sends_attachment(monkeypatch):
    send_mail_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(internal_retrieval_router, 'send_mail', send_mail_mock)

    client = TestClient(_build_app(_principal()))
    resp = client.post(
        '/api/v1/internal/retrieval/email-document',
        json={
            'subject': 'Concept-beschikking — Parkeren PB1',
            'document_markdown': '# Beschikking\nHallo',
        },
    )

    assert resp.status_code == 200
    assert resp.json() == {'ok': True, 'to': 'lex@gradient-ds.com'}
    send_mail_mock.assert_awaited_once()
    kwargs = send_mail_mock.await_args.kwargs
    assert kwargs['to_address'] == 'lex@gradient-ds.com'
    assert kwargs['subject'] == 'Concept-beschikking — Parkeren PB1'
    attachments = kwargs['attachments']
    assert len(attachments) == 1
    att = attachments[0]
    assert att['@odata.type'] == '#microsoft.graph.fileAttachment'
    assert att['name'] == 'concept-beschikking.md'
    assert att['contentType'] == 'text/markdown'
    assert base64.b64decode(att['contentBytes']).decode('utf-8') == '# Beschikking\nHallo'
    # Endpoint sends the branded HTML template (not the old one-liner).
    assert 'Je concept-beschikking staat klaar' in kwargs['html_body']


def test_email_document_no_email_is_400(monkeypatch):
    send_mail_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(internal_retrieval_router, 'send_mail', send_mail_mock)

    client = TestClient(_build_app(_principal(email='')))
    resp = client.post(
        '/api/v1/internal/retrieval/email-document',
        json={'subject': 'x', 'document_markdown': 'y'},
    )

    assert resp.status_code == 400
    send_mail_mock.assert_not_awaited()
