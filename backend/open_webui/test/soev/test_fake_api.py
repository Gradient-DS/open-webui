"""Exercise the reusable fake through HTTP against the recorded B5 wire contract."""

import base64
import importlib
import json
from uuid import uuid4

import httpx
import pytest

SERVICE = 'owui:service:webui'


def assertion(ref):
    payload = json.dumps({'sub': ref, 'jti': str(uuid4())}).encode()
    return 'e30.' + base64.urlsafe_b64encode(payload).decode().rstrip('=') + '.signature'


@pytest.fixture
def api():
    """Use a real HTTP client over MockTransport with inspectable fake state."""
    module = importlib.import_module('open_webui.test.soev.fake_api')
    fake = module.FakeSoevApi(page_size=1)
    with httpx.Client(transport=httpx.MockTransport(fake.handle), base_url='https://soev.invalid') as client:
        yield fake, client


def send(client, method, path, body=None, *, user=None, operation=None, credential='test-runtime-key'):
    headers = {'Authorization': f'Bearer {credential}'}
    if user:
        headers['X-Soev-Subject'] = assertion(user)
    if method != 'GET':
        headers['Idempotency-Key'] = operation or str(uuid4())
    return client.request(method, path, json=body, headers=headers)


def seed(client, key='kb', user='owui:user:alice'):
    response = send(client, 'POST', '/v1/identity/links', {'platform_user_id': 'person', 'assertion': assertion(user)})
    assert response.status_code == 204
    body = {
        'key': key,
        'name': key,
        'description': None,
        'visibility': 'restricted',
        'principals': [SERVICE],
        'writers': [],
    }
    response = send(client, 'POST', '/v1/collections', body, user=user)
    assert response.status_code == 201
    return body, response.json()


def test_collection_replays_and_read_write_decisions(api):
    """Collection creation is replayable, while unreadable rows stay 404 and read does not imply write."""
    fake, client = api
    body, collection = seed(client)
    assert collection['created_by'] == 'owui:user:alice'
    assert collection['principals'] == [SERVICE, 'owui:user:alice']
    assert collection['writers'] == ['owui:user:alice']
    assert send(client, 'POST', '/v1/collections', body, user='owui:user:alice').status_code == 200
    assert send(client, 'GET', '/v1/collections/kb').json()['caller_may_write'] is None
    assert send(client, 'GET', '/v1/collections/kb', user='owui:user:alice').json()['caller_may_write'] is True
    seed(client, 'other', 'owui:user:bob')
    assert send(client, 'GET', '/v1/collections/kb', user='owui:user:bob').status_code == 404
    fake.collections['kb']['principals'].append('owui:user:bob')
    assert send(client, 'GET', '/v1/collections/kb', user='owui:user:bob').json()['caller_may_write'] is False
    refused = send(client, 'PATCH', '/v1/collections/kb', {'name': 'bad'}, user='owui:user:bob')
    assert refused.status_code == 403
    assert refused.json()['constraint'] == 'collection:writers'
    assert refused.headers['content-type'].startswith('application/problem+json')
    operation = str(uuid4())
    for _ in range(2):
        assert send(client, 'PATCH', '/v1/collections/kb', {'name': 'renamed'}, operation=operation).status_code == 200
    assert send(client, 'PATCH', '/v1/collections/kb', {'name': 'different'}, operation=operation).status_code == 409
    assert fake.collections['kb']['name'] == 'renamed'
    assert send(client, 'PATCH', '/v1/collections/kb', {'principals': []}).status_code == 400


def test_access_and_delete_jobs_advance_only_when_requested(api):
    """Queued access and deletion jobs preserve catalog state until a test advances them to success."""
    fake, client = api
    seed(client)
    operation = str(uuid4())
    responses = [
        send(
            client,
            'PUT',
            '/v1/collections/kb/access',
            {'visibility': 'public', 'principals': [SERVICE]},
            operation=operation,
        )
        for _ in range(2)
    ]
    assert [r.status_code for r in responses] == [202, 202]
    job = responses[0].json()
    assert job == responses[1].json()
    assert job['kind'] == 'update_collection_access' and job['status'] == 'QUEUED'
    assert fake.collections['kb']['visibility'] == 'restricted'
    fake.advance(job['job_id'], 'RUNNING')
    assert send(client, 'GET', '/v1/jobs?status=QUEUED').json()['data'] == []
    assert send(client, 'GET', '/v1/jobs?status=RUNNING&collection_key=kb').json()['data'][0]['job_id'] == job['job_id']
    fake.advance(job['job_id'], 'SUCCEEDED')
    assert fake.collections['kb']['visibility'] == 'public'
    operation = str(uuid4())
    first = send(client, 'DELETE', '/v1/collections/kb', operation=operation)
    second = send(client, 'DELETE', '/v1/collections/kb', operation=operation)
    assert first.status_code == second.status_code == 202 and first.json() == second.json()
    delete = first.json()
    assert 'kb' in fake.collections
    fake.advance(delete['job_id'], 'FAILED')
    assert 'kb' in fake.collections
    delete = send(client, 'DELETE', '/v1/collections/kb').json()
    fake.advance(delete['job_id'], 'SUCCEEDED')
    assert send(client, 'GET', '/v1/collections/kb').status_code == 404
    assert send(client, 'GET', '/v1/jobs/' + delete['job_id']).json()['status'] == 'SUCCEEDED'


def test_folder_and_document_routes_preserve_paths_and_replays(api):
    """Folders paginate separately from documents and support idempotent mkdir, moves, and empty-only deletion."""
    fake, client = api
    seed(client)
    operation = str(uuid4())
    for status in (201, 200):
        assert (
            send(client, 'POST', '/v1/collections/kb/folders', {'path': 'a/b'}, operation=operation).status_code
            == status
        )
    fake.add_document('kb', 'file', path='a/b')
    fake.add_document('kb', 'loose')
    listing = send(client, 'GET', '/v1/collections/kb/folders').json()
    assert listing['folders'] == [{'path': 'a', 'created_at': fake.now}]
    assert listing['documents'] == [] and listing['next_cursor']
    next_page = send(client, 'GET', '/v1/collections/kb/folders?cursor=' + listing['next_cursor']).json()
    assert next_page['folders'] == [] and next_page['documents'][0]['source_id'] == 'loose'
    assert send(client, 'DELETE', '/v1/collections/kb/folders?path=a').status_code == 409
    operation = str(uuid4())
    for _ in range(2):
        assert (
            send(
                client, 'POST', '/v1/collections/kb/folders/move', {'from': 'a', 'to': 'z'}, operation=operation
            ).status_code
            == 204
        )
    assert fake.documents['kb', 'file']['path'] == 'z/b'
    assert send(client, 'GET', '/v1/collections/kb/folders?under=z').json()['folders'][0]['path'] == 'z/b'
    operation = str(uuid4())
    for _ in range(2):
        assert (
            send(client, 'POST', '/v1/collections/kb/documents/file/move', {'to': ''}, operation=operation).status_code
            == 204
        )
    assert fake.documents['kb', 'file']['path'] is None
    assert send(client, 'DELETE', '/v1/collections/kb/folders?path=z/b').status_code == 204
    assert send(client, 'DELETE', '/v1/collections/kb/folders?path=z/b').status_code == 404
    operation = str(uuid4())
    first = send(client, 'DELETE', '/v1/collections/kb/documents/file', operation=operation)
    assert first.status_code == 202
    assert send(client, 'DELETE', '/v1/collections/kb/documents/file', operation=operation).json() == first.json()
    assert ('kb', 'file') in fake.documents
    fake.advance(first.json()['job_id'], 'SUCCEEDED')
    assert ('kb', 'file') not in fake.documents
    assert send(client, 'GET', '/v1/collections/kb/documents').json()['data'][0]['source_id'] == 'loose'


def test_identity_groups_and_credential_scoped_jobs(api):
    """Membership replacement affects closure, assertions are single-use, and subjects never widen job visibility."""
    fake, client = api
    seed(client)
    seed(client, 'other', 'owui:user:bob')
    fake.collections['kb']['principals'].append('owui:group:staff')
    fake.collections['kb']['writers'].append('owui:group:staff')
    operation = str(uuid4())
    for _ in range(2):
        assert (
            send(
                client,
                'PUT',
                '/v1/directory/groups/owui%3Agroup%3Astaff/members',
                {'members': ['owui:user:bob']},
                operation=operation,
            ).status_code
            == 204
        )
    assert fake.groups['owui:group:staff'] == ['owui:user:bob']
    assert send(client, 'GET', '/v1/collections/kb', user='owui:user:bob').json()['caller_may_write'] is True
    job = send(client, 'DELETE', '/v1/collections/kb', user='owui:user:alice').json()
    assert send(client, 'GET', '/v1/jobs/' + job['job_id'], user='owui:user:bob').status_code == 200
    fake.credentials['other-key'] = SERVICE
    assert (
        send(client, 'GET', '/v1/jobs/' + job['job_id'], credential='other-key', user='owui:user:alice').status_code
        == 404
    )
    assert send(client, 'GET', '/v1/jobs', credential='other-key').json()['data'] == []
    token = assertion('owui:user:alice')
    headers = {'Authorization': 'Bearer test-runtime-key', 'X-Soev-Subject': token}
    assert client.get('/v1/collections/kb', headers=headers).status_code == 200
    assert client.get('/v1/collections/kb', headers=headers).status_code == 401
    send(client, 'PUT', '/v1/directory/groups/owui%3Agroup%3Astaff/members', {'members': []})
    assert send(client, 'GET', '/v1/collections/kb', user='owui:user:bob').status_code == 404


def test_pages_and_document_acl_follow_the_wire_contract(api):
    """Pages cap at 200 and independently unreadable documents never enter a readable collection's listing."""
    fake, client = api
    seed(client)
    seed(client, 'second')
    page = send(client, 'GET', '/v1/collections').json()
    assert len(page['data']) == 1 and page['next_cursor']
    assert len(send(client, 'GET', '/v1/collections?cursor=' + page['next_cursor']).json()['data']) == 1
    assert send(client, 'GET', '/v1/collections?limit=201').status_code == 422
    fake.add_document('kb', 'private', principals=['owui:user:bob'])
    assert send(client, 'GET', '/v1/collections/kb/documents', user='owui:user:alice').json()['data'] == []
    assert send(client, 'DELETE', '/v1/collections/kb/documents/private', user='owui:user:alice').status_code == 404
    missing_key = client.post('/v1/collections', json={}, headers={'Authorization': 'Bearer test-runtime-key'})
    assert missing_key.status_code == 400
