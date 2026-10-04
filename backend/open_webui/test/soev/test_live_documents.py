"""Reference attachments use durable Files, the job poller and user-scoped originals."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from open_webui.test.soev.test_jobs import env  # noqa: F401


def attachment():
    return {
        'source_id': 'reference-source',
        'job_id': 'reference-job',
        'collection_key': 'owui-attachments-alice',
        'name': 'Quarterly plan.pdf',
        'web_url': 'https://example.sharepoint.com/personal/alice/plan.pdf',
        'provider': 'onedrive',
        'provider_ref': {'grant_id': 'g', 'drive_id': 'd', 'item_id': 'i', 'etag': 'e'},
        'attached_by': 'agent',
        'status': 'ready',
        'call_id': 'call',
        'elements': [],
    }


@pytest.mark.asyncio
async def test_event_poll_and_next_turn(env, monkeypatch):  # noqa: F811
    """Replaying an event preserves one File and a completed job supplies next-turn identity."""
    from open_webui.soev import live_documents
    from open_webui.soev.client import ChatEvent
    from open_webui.utils import agent_v2

    messages = {}

    async def upsert(chat, message, update):
        messages.update(update)

    monkeypatch.setattr(agent_v2.Chats, 'get_message_by_id_and_message_id', AsyncMock(side_effect=lambda *a: messages))
    monkeypatch.setattr(agent_v2.Chats, 'upsert_message_to_chat_by_id_and_message_id', upsert)
    turn = agent_v2.AgentTurn(env.client, {'user_id': 'alice', 'chat_id': 'chat', 'message_id': 'm'}, 'owui:user:alice')
    turn.emitter = AsyncMock()
    event = ChatEvent('attached', {'stream': 'root', 'payload': attachment()})
    await turn.render_event(event)
    await turn.render_event(event)
    rows = await env.files.Files.get_files_with_soev_jobs()
    assert len(rows) == 1
    assert rows[0].id == attachment()['source_id'] and rows[0].path == ''
    assert len(messages['files']) == 1
    with pytest.raises(agent_v2.AttachmentsUnavailable):
        await agent_v2._attachments(messages)
    client = SimpleNamespace(get=AsyncMock(return_value={'status': 'SUCCEEDED'}), send=AsyncMock())
    assert await env.jobs.poll_once(client, now=rows[0].meta['soev_job']['submitted_at']) == 1
    client.send.assert_not_awaited()
    row = await env.files.Files.get_file_by_id(rows[0].id)
    assert row.meta['status'] == 'completed' and row.meta['soev_job'] is None
    assert await agent_v2._attachments(messages) == {
        'attachments': [
            {
                'collection_key': attachment()['collection_key'],
                'file_id': row.id,
                'name': row.filename,
                'document_ref': attachment()['provider_ref'],
            }
        ]
    }
    await turn.render_event(event)
    assert (await live_documents.register_attachment('alice', attachment())).meta['status'] == 'completed'
    assert await env.files.Files.get_files_with_soev_jobs() == []


@pytest.mark.asyncio
@pytest.mark.parametrize('named', [False, True])
@pytest.mark.parametrize('denied', [False, True])
async def test_original_proxy_rechecks_subject_without_storage(env, monkeypatch, named, denied):  # noqa: F811
    """Both download routes stream the original under the requester and preserve API denial."""
    from open_webui.routers import files
    from open_webui.soev import live_documents
    from open_webui.soev.client import SoevApiError

    file = await live_documents.register_attachment('alice', attachment())
    calls, closed = [], []

    async def stream(path, *, as_user):
        calls.append((path, as_user))
        try:
            if denied:
                raise SoevApiError(403, 'forbidden', 'Access revoked')
            yield b'original'
            yield b' bytes'
        finally:
            closed.append(True)

    monkeypatch.setattr(live_documents.identity, 'build_client', lambda: SimpleNamespace(stream=stream))
    monkeypatch.setattr(live_documents.identity, 'acting_ref', AsyncMock(return_value='owui:user:alice'))
    storage = Mock(side_effect=AssertionError('Reference originals must not use OWUI storage'))
    monkeypatch.setattr(files.Storage, 'get_file', storage)
    user = SimpleNamespace(id='alice', role='user')
    response = (
        files.get_file_content_by_id(file.id, 'plan.pdf', user=user, db=None)
        if named
        else files.get_file_content_by_id_inline(file.id, user=user, attachment=False, db=None)
    )
    if denied:
        with pytest.raises(HTTPException) as error:
            await response
        assert error.value.status_code == 403
    else:
        result = await response
        assert b''.join([part async for part in result.body_iterator]) == b'original bytes'
    assert calls == [(f'/v1/collections/owui-attachments-alice/documents/{file.id}/original', 'owui:user:alice')]
    assert closed == [True]
    storage.assert_not_called()


@pytest.mark.asyncio
async def test_every_turn_supplies_collection_and_separate_tool_states(env, monkeypatch):  # noqa: F811
    """The collection is created through the consumer and both document tools require the toggle."""
    from open_webui.utils import agent_v2

    turn = SimpleNamespace(client=env.client, run=Mock(return_value='stream'))
    result = await agent_v2._sent(
        turn, 'hello', {'user_id': 'alice', 'features': {'live_documents': True}}, agent=None, model=None
    )
    assert result == 'stream'
    body = turn.run.call_args.args[0]['input']
    assert body['attachment_collection'] == 'owui-attachments-alice'
    assert body['tools'] == {'search_live_documents': 'auto', 'attach_live_document': 'auto'}
    assert agent_v2._tools({}) == {}


@pytest.mark.asyncio
async def test_connect_action_is_emitted(env):  # noqa: F811
    """Connect action elements reach the product instead of disappearing into tool text."""
    from open_webui.utils.agent_v2 import AgentTurn

    turn = AgentTurn(env.client, {'user_id': 'alice'}, 'owui:user:alice')
    turn.emitter = AsyncMock()
    await turn.record_output(
        {
            'call_id': 'call',
            'elements': [{'id': 'connect', 'type': 'action-required', 'kind': 'connect', 'provider': 'onedrive'}],
        }
    )
    turn.emitter.assert_any_await({'type': 'action_required', 'data': {'kind': 'connect', 'provider': 'onedrive'}})
