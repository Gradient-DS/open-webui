"""Reference attachments use durable Files, the job poller and user-scoped originals."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from open_webui.soev.client import ChatEvent
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
        'content_type': 'application/pdf',
    }


def document():
    """[Claude] The `attached-document` element the agent's attach tool records for `attachment()`."""
    record = attachment()
    return {
        'type': 'attached-document',
        'id': record['collection_key'] + '/' + record['source_id'],
        'title': 'Quarterly plan',
        'filename': record['name'],
        'source_id': record['source_id'],
        'source_url': record['web_url'],
        'collection_key': record['collection_key'],
        'file_id': record['source_id'],
        'job_id': record['job_id'],
        'document_ref': record['provider_ref'],
        'provider': record['provider'],
        'web_url': record['web_url'],
        'content_type': record['content_type'],
        'ready': True,
        'readable': True,
    }


def attached(*documents):
    """[Claude] The root `tool_output` event of an attach call whose result records `documents`."""
    return ChatEvent('tool_output', {'stream': 'root', 'payload': {'call_id': 'attach', 'elements': list(documents)}})


@pytest.mark.asyncio
async def test_event_poll_and_next_turn(env, monkeypatch):  # noqa: F811
    """Replaying an event preserves one File and a completed job supplies next-turn identity."""
    from open_webui.soev import live_documents
    from open_webui.utils import agent_v2

    messages = {}

    async def upsert(chat, message, update):
        messages.update(update)

    monkeypatch.setattr(agent_v2.Chats, 'get_message_by_id_and_message_id', AsyncMock(side_effect=lambda *a: messages))
    monkeypatch.setattr(agent_v2.Chats, 'upsert_message_to_chat_by_id_and_message_id', upsert)
    turn = agent_v2.AgentTurn(env.client, {'user_id': 'alice', 'chat_id': 'chat', 'message_id': 'm'}, 'owui:user:alice')
    turn.emitter = AsyncMock()
    event = attached(document())
    await turn.render_event(event)
    await turn.render_event(event)
    rows = await env.files.Files.get_files_with_soev_jobs()
    assert len(rows) == 1
    assert rows[0].id == attachment()['source_id'] and rows[0].path == ''
    assert len(messages['files']) == 1
    next_turn = {'user_id': 'alice', 'files': messages['files']}
    assert await agent_v2._attachments(next_turn) == {'attachment_notes': ['still processing: Quarterly plan.pdf']}
    client = SimpleNamespace(get=AsyncMock(return_value={'status': 'SUCCEEDED'}), send=AsyncMock())
    assert await env.jobs.poll_once(client, now=rows[0].meta['soev_job']['submitted_at']) == 1
    client.send.assert_not_awaited()
    row = await env.files.Files.get_file_by_id(rows[0].id)
    assert row.meta['status'] == 'completed' and row.meta['soev_job'] is None
    assert await agent_v2._attachments(next_turn) == {
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
        assert result.media_type == 'application/pdf'
        assert result.headers['content-disposition'].startswith('attachment' if named else 'inline')
        assert b''.join([part async for part in result.body_iterator]) == b'original bytes'
    assert calls == [(f'/v1/collections/owui-attachments-alice/documents/{file.id}/original', 'owui:user:alice')]
    assert closed == [True]
    storage.assert_not_called()


@pytest.mark.asyncio
async def test_every_turn_supplies_collection_and_separate_tool_states(env, monkeypatch):  # noqa: F811
    """The collection is created through the consumer and both document tools require the toggle."""
    from open_webui.utils import agent_v2

    monkeypatch.setattr(agent_v2, '_live_documents_allowed', AsyncMock(return_value=True))
    monkeypatch.setattr(agent_v2, '_web_search_allowed', AsyncMock(return_value=False))
    turn = SimpleNamespace(client=env.client, as_user='owui:user:alice', run=Mock(return_value='stream'))
    result = await agent_v2._sent(
        turn, 'hello', {'user_id': 'alice', 'features': {'live_documents': 'auto'}}, agent=None, model=None
    )
    assert result == 'stream'
    body = turn.run.call_args.args[0]['input']
    assert body['attachment_collection'] == 'owui-attachments-alice'
    assert body['tools'] == {
        'web_search': 'off',
        'fetch': 'off',
        'search_live_documents': 'auto',
        'list_live_folder': 'auto',
        'attach_live_document': 'auto',
        'search_mail': 'off',
        'read_mail': 'off',
    }
    assert agent_v2._tools({}, False, False, False)['tools']['search_live_documents'] == 'off'


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


@pytest.mark.parametrize('allowed', [True, False])
@pytest.mark.parametrize('state', ['off', 'auto', 'required', True, None])
def test_document_states_require_server_permission(allowed, state):
    from open_webui.utils import agent_v2

    expected = state if allowed and state in ('auto', 'required') else 'off'
    tools = agent_v2._tools({'features': {'live_documents': state}}, False, allowed, False)['tools']
    assert tools['search_live_documents'] == tools['attach_live_document'] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['failed', 'processing', 'gone'])
async def test_unavailable_reference_does_not_block_next_turn(env, monkeypatch, status):  # noqa: F811
    from open_webui.soev import live_documents
    from open_webui.utils import agent_v2

    row = await live_documents.register_attachment('alice', attachment())
    if status == 'gone':
        await env.files.Files.delete_file_by_id(row.id)
    else:
        await env.files.Files.set_status(row.id, status)
    monkeypatch.setattr(agent_v2, '_live_documents_allowed', AsyncMock(return_value=False))
    monkeypatch.setattr(agent_v2, '_web_search_allowed', AsyncMock(return_value=False))
    turn = SimpleNamespace(client=env.client, as_user='owui:user:alice', run=Mock(return_value='stream'))
    assert (
        await agent_v2._sent(
            turn, 'next', {'user_id': 'alice', 'files': [live_documents.chat_file(row)]}, agent=None, model=None
        )
        == 'stream'
    )
    sent = turn.run.call_args.args[0]['input']
    assert 'attachments' not in sent
    assert ('still processing: Quarterly plan.pdf' in sent['text']) == (status == 'processing')


@pytest.mark.asyncio
async def test_collection_cache_and_setup_failure(env, monkeypatch):  # noqa: F811
    from open_webui.soev import live_documents
    from open_webui.utils import agent_v2

    live_documents._collections.clear()
    setup = AsyncMock(return_value='owui-attachments-alice')
    monkeypatch.setattr(live_documents.ingest, 'ensure_attachments_collection', setup)
    assert await live_documents.attachment_collection('alice', env.client) == 'owui-attachments-alice'
    await live_documents.attachment_collection('alice', env.client)
    assert setup.await_count == 1
    live_documents._collections.clear()
    setup.side_effect = RuntimeError('unavailable')
    monkeypatch.setattr(agent_v2, '_live_documents_allowed', AsyncMock(return_value=True))
    monkeypatch.setattr(agent_v2, '_web_search_allowed', AsyncMock(return_value=False))
    turn = SimpleNamespace(client=env.client, as_user='owui:user:alice', run=Mock(return_value='stream'))
    await agent_v2._sent(
        turn, 'hello', {'user_id': 'alice', 'features': {'live_documents': 'auto'}}, agent=None, model=None
    )
    sent = turn.run.call_args.args[0]['input']
    assert 'live documents unavailable this turn' in sent['text']
    assert sent['tools']['attach_live_document'] == 'off'
    assert 'attachment_collection' not in sent


@pytest.mark.asyncio
async def test_delete_reference_skips_s3_storage(env, monkeypatch):  # noqa: F811
    from open_webui.routers import files
    from open_webui.soev import live_documents
    from open_webui.storage.provider import S3StorageProvider

    row = await live_documents.register_attachment('alice', attachment())
    monkeypatch.setattr(files.ingest, 'cancel', AsyncMock())
    monkeypatch.setattr(files.Knowledges, 'get_knowledges_by_file_id', AsyncMock(return_value=[]))
    monkeypatch.setattr(files, 'publish_event', AsyncMock())
    storage = Mock(spec=S3StorageProvider)
    storage.delete_file.side_effect = AssertionError('Empty reference path passed to S3')
    monkeypatch.setattr(files, 'Storage', storage)
    assert await files.delete_file_by_id(
        id=row.id, request=Mock(), user=SimpleNamespace(id='alice', role='user'), db=None
    )
    storage.delete_file.assert_not_called()


@pytest.mark.asyncio
async def test_mismatched_agent_event_is_skipped(env):  # noqa: F811
    from open_webui.utils.agent_v2 import AgentTurn

    turn = AgentTurn(env.client, {'user_id': 'alice'}, 'owui:user:alice')
    turn.emitter = AsyncMock()
    await turn.attached({'elements': [{**document(), 'collection_key': 'foreign'}]})
    turn.emitter.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_agent_processing_event_allows_next_turn(env, monkeypatch):  # noqa: F811
    """A captured real TicketAttacher event stays pending without blocking the next turn."""
    import json
    from pathlib import Path

    from open_webui.utils import agent_v2

    payload = json.loads((Path(__file__).parent / 'fixtures' / 'live_document_processing.json').read_text())
    message = {}

    async def upsert(chat, message_id, update):
        message.update(update)

    monkeypatch.setattr(agent_v2.Chats, 'get_message_by_id_and_message_id', AsyncMock(side_effect=lambda *a: message))
    monkeypatch.setattr(agent_v2.Chats, 'upsert_message_to_chat_by_id_and_message_id', upsert)
    turn = agent_v2.AgentTurn(env.client, {'user_id': 'alice', 'chat_id': 'slow', 'message_id': 'm'}, 'owui:user:alice')
    turn.emitter = AsyncMock()
    await turn.render_event(ChatEvent('tool_output', {'stream': 'root', 'payload': payload}))
    (document,) = payload['elements']
    row = await env.files.Files.get_file_by_id(document['file_id'])
    assert row.meta['status'] == 'processing'
    assert row.meta['content_type'] == document['content_type']
    monkeypatch.setattr(agent_v2, '_live_documents_allowed', AsyncMock(return_value=False))
    monkeypatch.setattr(agent_v2, '_web_search_allowed', AsyncMock(return_value=False))
    next_turn = SimpleNamespace(client=env.client, as_user='owui:user:alice', run=Mock(return_value='stream'))
    assert (
        await agent_v2._sent(
            next_turn, 'Read it', {'user_id': 'alice', 'files': message['files']}, agent=None, model=None
        )
        == 'stream'
    )
    sent = next_turn.run.call_args.args[0]['input']
    assert 'attachments' not in sent
    assert 'still processing: ' + document['filename'] in sent['text']


@pytest.mark.asyncio
async def test_completed_reference_recovers_missing_mime(env, monkeypatch):  # noqa: F811
    """A legacy attachment event gets its MIME from the completed document."""
    from open_webui.soev import live_documents

    event = {**attachment(), 'content_type': None}
    row = await live_documents.register_attachment('alice', event)
    client = SimpleNamespace(get=AsyncMock(side_effect=[{'status': 'SUCCEEDED'}, {'content_type': 'application/pdf'}]))
    monkeypatch.setattr(env.jobs.identity, 'ensure_link', AsyncMock())
    assert await env.jobs.poll_once(client, now=row.meta['soev_job']['submitted_at']) == 1
    updated = await env.files.Files.get_file_by_id(row.id)
    assert updated.meta['content_type'] == 'application/pdf'
    assert updated.meta['status'] == 'completed'
    assert client.get.call_args.kwargs['as_user'] == 'owui:user:alice'


@pytest.mark.asyncio
@pytest.mark.parametrize('attached_by', ['agent', 'user'])
async def test_reference_citation_provider_survives_thread_replay(env, monkeypatch, attached_by):  # noqa: F811
    from open_webui.soev import live_documents
    from open_webui.utils import agent_v2

    record = {**attachment(), 'attached_by': attached_by}
    elements = [
        {
            'id': 'document',
            'type': 'document',
            'source_id': record['source_id'],
            'filename': record['name'],
            'source_url': 'https://unrelated.example.test/document',
        },
        {'id': 'passage', 'type': 'document-text', 'ref': 'document', 'text': 'Quarterly plan'},
    ]
    turn = agent_v2.AgentTurn(env.client, {'user_id': 'alice'}, 'owui:user:alice')
    turn.emitter = AsyncMock()
    if attached_by == 'user':
        # Picker attachment File exists before the agent reads it.
        await live_documents.register_attachment('alice', record)
        payload = {'elements': elements}
    else:
        payload = {'elements': [*elements, document()]}
    await turn.record_output(payload)
    assert turn.citations.sources['passage']['source']['provider'] == 'onedrive'
    assert turn.citations.sources['passage']['metadata'][0]['file_id'] == record['source_id']

    replay = agent_v2.AgentTurn(env.client, {'user_id': 'alice'}, 'owui:user:alice')
    await replay._seed_sources([{'position': 1, 'stream': 'root', 'type': 'tool_output', 'payload': payload}], 1)
    assert replay.citations.sources == turn.citations.sources
