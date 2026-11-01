"""Owner browser requests settle durable jobs through the person-scoped platform fake."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Depends
from open_webui.soev import jobs
from open_webui.test.soev.fake_api import person_ref
from open_webui.test.soev.test_jobs import env  # noqa: F401
from open_webui.test.soev.test_keycloak_token import token_app  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ['status', 'stream', 'knowledge', 'other'])
@pytest.mark.parametrize('terminal', ['SUCCEEDED', 'COMPLETED_WITH_ERRORS'])
async def test_owner_browser_settles_job_and_emits_status(token_app, env, monkeypatch, route, terminal):  # noqa: F811
    from open_webui.internal.db import get_async_session
    from open_webui.models.users import User
    from open_webui.routers import files, knowledge
    from open_webui.soev import pending_sync
    from open_webui.soev.knowledge_store import SoevKnowledgeTable

    ctx = token_app
    ctx.user.person_id = person_ref(ctx.user.id)
    async with env.files.get_async_db_context() as db:
        connection = await db.connection()
        await connection.run_sync(User.__table__.create)
        db.add(User(**ctx.user.model_dump()))
        await db.commit()
    ctx.app.include_router(files.router, prefix='/api/v1/files')
    ctx.app.include_router(knowledge.router, prefix='/api/v1/knowledge')

    @ctx.app.get('/authenticated')
    async def authenticated(user=Depends(ctx.auth.get_verified_user)):
        return {}

    async def no_db():
        yield None

    ctx.app.dependency_overrides[get_async_session] = no_db
    monkeypatch.setattr(pending_sync, 'on_request', AsyncMock())
    monkeypatch.setattr('open_webui.internal.db.get_async_db_context', env.files.get_async_db_context)
    monkeypatch.setattr(knowledge, 'Knowledges', SoevKnowledgeTable(client=env.client))
    env.api.user_tokens['fictional-access-token'] = 'owui:user:fern'
    async with type(ctx.browser)(transport=httpx.MockTransport(env.api.handle)) as transport:
        monkeypatch.setattr('open_webui.soev.client._shared_client', lambda: transport)
        await env.client.send(
            'POST',
            '/v1/collections',
            {
                'key': 'browser-kb',
                'name': 'Browser files',
                'visibility': 'restricted',
                'principals': [person_ref('fern')],
                'writers': [person_ref('fern')],
            },
            as_user='owui:user:fern',
            idempotency_key='collection:browser-kb',
        )
        file = await env.files.Files.insert_new_file(
            'fern', env.files.FileForm(id='browser-file', filename='report.txt', path='', data={})
        )
        job_id = await env.module.submit(
            file, collection_key='browser-kb', user_id='fern', text='report text', client=env.client
        )
        env.api.advance(job_id, terminal, item_code='internal_error', item_detail='private detail')
        if terminal != 'SUCCEEDED':
            env.api.jobs[job_id]['items'][0]['reason'] = 'unreadable_file'
        env.api.requests.clear()
        assert await jobs.poll_once(env.client.as_service(), now=0) == 0
        assert not env.api.requests
        if route == 'knowledge':
            response = await ctx.browser.get('/api/v1/knowledge/browser-kb/files?metadata_only=true')
        elif route == 'other':
            response = await ctx.browser.get('/authenticated')
        else:
            response = await ctx.browser.get(
                '/api/v1/files/browser-file/process/status', params={'stream': route == 'stream'}
            )
    assert response.status_code == 200, response.text
    expected = 'completed' if terminal == 'SUCCEEDED' else 'failed'
    error = None if terminal == 'SUCCEEDED' else 'internal_error: unreadable_file'
    if route == 'knowledge':
        assert response.json()['items'][0]['status'] == expected
    elif route == 'stream':
        assert json.loads(response.text.removeprefix('data: '))['status'] == expected
    elif route == 'status':
        assert response.json()['status'] == expected
    row = await env.files.Files.get_file_by_id(file.id)
    assert row.data['status'] == row.meta['status'] == expected
    assert row.data['error'] == row.meta['error'] == error
    assert row.meta['soev_job'] is None
    reads = [r for r in env.api.requests if r.url.path == f'/v1/jobs/{job_id}']
    assert len(reads) == 1
    assert reads[0].headers['X-Soev-User-Token'] == 'fictional-access-token'
    env.events.assert_awaited_once_with(
        user_id='fern', file_id=file.id, status=expected, error=error, collection_name='browser-kb'
    )


@pytest.mark.asyncio
async def test_open_status_stream_reconciles_when_job_finishes(env, monkeypatch):  # noqa: F811
    from open_webui.routers import files
    from open_webui.soev.request_cache import request_cache
    from open_webui.test.soev.test_jobs import owner_login, submitted

    file, job = await submitted(env)
    clock = [job['submitted_at']]
    monkeypatch.setattr(jobs, 'time', SimpleNamespace(time=lambda: clock[0]))

    async def advance(_):
        clock[0] += 5
        env.api.advance(job['job_id'], 'SUCCEEDED')

    monkeypatch.setattr(files, 'asyncio', SimpleNamespace(sleep=advance))
    with owner_login(), request_cache():
        response = await files.get_file_process_status(file.id, stream=True, user=SimpleNamespace(id='alice'))
        events = [event async for event in response.body_iterator]
    assert len(events) == 2
    assert 'processing' in events[0]
    assert 'completed' in events[1]
    assert (await env.files.Files.get_file_by_id(file.id)).meta['soev_job'] is None
    env.events.assert_awaited_once()
