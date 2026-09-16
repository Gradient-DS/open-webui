"""Exercise the reusable fake through HTTP against the recorded B5 wire contract."""

import base64
import datetime as dt
import hashlib
import importlib
from uuid import uuid4

import httpx
import pytest

SERVICE = 'owui:service:webui'
FILE_BYTES = b'file content'


def file_document(source_id='file', **fields):
    return {
        'source_id': source_id,
        'filename': source_id + '.txt',
        'size': len(FILE_BYTES),
        'sha256': hashlib.sha256(FILE_BYTES).hexdigest(),
        **fields,
    }


def inline_document(source_id='inline', **fields):
    return {'source_id': source_id, 'filename': source_id + '.txt', 'text': 'hello', **fields}


def ingest(client, *documents, **kwargs):
    return send(client, 'POST', '/v1/jobs', {'collection_key': 'kb', 'documents': list(documents)}, **kwargs)


def assertion(ref):
    identity = importlib.import_module('open_webui.soev.identity')
    return identity.mint_assertion(ref, now=dt.datetime.now(dt.UTC))


@pytest.fixture
def api(fake_api):
    """Use a real HTTP client over MockTransport with inspectable fake state."""
    fake = fake_api
    fake.page_size = 1
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


def test_identity_link_and_group_routes_replay_freely(api):
    """Links and group replacements require valid keys without caching bodies or bypassing assertion checks."""
    fake, client = api
    link_path = '/v1/identity/links'
    group_path = '/v1/directory/groups/owui%3Agroup%3Astaff/members'
    operation = 'directory:repeated'
    for _ in range(2):
        response = send(
            client,
            'POST',
            link_path,
            {'platform_user_id': 'person', 'assertion': assertion('owui:user:alice')},
            operation=operation,
        )
        assert response.status_code == 204
    assert fake.links == {'owui:user:alice': 'person'}
    for members in (['owui:user:alice'], [], ['owui:user:alice']):
        response = send(client, 'PUT', group_path, {'members': members}, operation=operation)
        assert response.status_code == 204
        assert fake.groups['owui:group:staff'] == members
    assert not fake.replays
    body = {'platform_user_id': 'person', 'assertion': assertion('owui:user:alice')}
    assert send(client, 'POST', link_path, body, operation=operation).status_code == 204
    replayed_assertion = send(client, 'POST', link_path, body, operation=operation)
    assert replayed_assertion.status_code == 401
    assert replayed_assertion.json()['code'] == 'assertion_replayed'
    for method, path, body in (
        ('POST', link_path, {'platform_user_id': 'person', 'assertion': assertion('owui:user:alice')}),
        ('PUT', group_path, {'members': []}),
    ):
        for invalid in (None, 'short', 'x' * 256):
            headers = {'Authorization': 'Bearer test-runtime-key'}
            if invalid is not None:
                headers['Idempotency-Key'] = invalid
            response = client.request(method, path, json=body, headers=headers)
            assert response.status_code == 400
            assert response.json()['code'] == 'invalid_idempotency_key'


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


def test_an_assertion_signed_with_the_configured_kid_is_accepted_by_the_fake(api):
    """The registered public key verifies assertions minted with the configured kid."""
    fake, client = api
    response = send(
        client,
        'POST',
        '/v1/identity/links',
        {'platform_user_id': 'person', 'assertion': assertion('owui:user:alice')},
    )
    assert response.status_code == 204
    assert fake.links == {'owui:user:alice': 'person'}


def test_an_assertion_with_an_unregistered_kid_is_refused_by_the_fake(api, identity_config, monkeypatch):
    """A valid signature cannot authenticate an unregistered kid."""
    fake, client = api
    identity, _ = identity_config
    monkeypatch.setattr(identity.config, 'SOEV_API_SIGNING_KID', 'unregistered-key')
    response = send(
        client,
        'POST',
        '/v1/identity/links',
        {'platform_user_id': 'person', 'assertion': assertion('owui:user:alice')},
    )
    assert response.status_code == 401
    assert response.json()['code'] == 'credential_invalid'
    assert not fake.links


def test_an_assertion_with_a_forged_signature_is_refused_by_the_fake(api):
    """A registered kid cannot authenticate a forged signature."""
    fake, client = api
    header, payload, _ = assertion('owui:user:alice').split('.')
    forged = base64.urlsafe_b64encode(bytes(64)).decode().rstrip('=')
    response = send(
        client,
        'POST',
        '/v1/identity/links',
        {'platform_user_id': 'person', 'assertion': f'{header}.{payload}.{forged}'},
    )
    assert response.status_code == 401
    assert response.json()['code'] == 'credential_invalid'
    assert not fake.links


@pytest.mark.parametrize('setting', ['SOEV_API_CREDENTIAL_ID', 'SOEV_API_AUDIENCE'])
def test_an_assertion_with_wrong_claims_is_refused_by_the_fake(api, identity_config, monkeypatch, setting):
    """A valid signature still requires the credential issuer and configured audience."""
    _, client = api
    identity, _ = identity_config
    monkeypatch.setattr(identity.config, setting, 'wrong-value')
    response = send(
        client,
        'POST',
        '/v1/identity/links',
        {'platform_user_id': 'person', 'assertion': assertion('owui:user:alice')},
    )
    assert response.status_code == 401
    assert response.json()['code'] == 'credential_invalid'


def test_a_file_document_opens_an_awaiting_upload_job_with_a_presigned_put(api):
    """File proposals publish an absolute PUT URL with S3 checksum and length headers."""
    fake, client = api
    seed(client)
    document = file_document()
    response = ingest(client, document)
    assert response.status_code == 201
    job = response.json()
    assert job['kind'] == 'ingest' and job['status'] == 'AWAITING_UPLOAD'
    assert job['progress'] == {'total': 1, 'succeeded': 0, 'failed': 0, 'pending': 1, 'skipped': 0}
    assert job['uploads'] == [
        {
            'source_id': 'file',
            'method': 'PUT',
            'url': 'https://soev.invalid/uploads/' + document['sha256'],
            'headers': {
                'x-amz-checksum-sha256': base64.b64encode(hashlib.sha256(FILE_BYTES).digest()).decode(),
                'Content-Length': str(len(FILE_BYTES)),
            },
            'expires_at': '2026-09-11T12:15:00Z',
        }
    ]
    assert fake.jobs[job['job_id']]['documents'] == [document]
    assert 'documents' not in job and 'items' not in job


def test_an_inline_document_is_queued_at_once(api):
    """An inline batch has no uploads and accepts exactly the byte budget."""
    _, client = api
    seed(client)
    response = ingest(client, inline_document(text='é' * 131072))
    assert response.status_code == 201
    assert response.json()['status'] == 'QUEUED'
    assert response.json()['uploads'] == []


def test_inline_text_over_the_budget_is_413(api):
    """The budget counts UTF-8 bytes across every inline document."""
    fake, client = api
    seed(client)
    response = ingest(client, inline_document(text='é' * 131072), inline_document('second', text='a'))
    assert response.status_code == 413
    assert response.json()['code'] == 'inline_budget_exceeded'
    assert not fake.jobs


def test_ingest_requires_the_credential_capability(api):
    """A readable collection alone does not grant the ingest capability."""
    fake, client = api
    seed(client)
    operation = str(uuid4())
    job = ingest(client, inline_document(), operation=operation).json()
    fake.capabilities['test-runtime-key'] = {'read'}
    for response in (
        ingest(client, inline_document()),
        ingest(client, inline_document(), operation=operation),
        send(client, 'POST', f'/v1/jobs/{job["job_id"]}/commit'),
        send(client, 'POST', f'/v1/jobs/{job["job_id"]}/cancel'),
    ):
        assert response.status_code == 403
        assert response.json()['code'] == 'scope_insufficient'
        assert response.json()['constraint'] == 'capability:ingest'


def test_a_non_writer_subject_is_403_scope_insufficient(api):
    """Writer membership, including group membership, is checked again on replay."""
    fake, client = api
    seed(client)
    fake.collections['kb']['writers'] = []
    response = ingest(client, inline_document(), user='owui:user:alice')
    assert response.status_code == 403
    assert response.json()['code'] == 'scope_insufficient'
    assert response.json()['constraint'] == 'collection:writers'
    fake.groups['owui:group:staff'] = ['owui:user:alice']
    fake.collections['kb']['writers'] = ['owui:group:staff']
    operation = str(uuid4())
    assert ingest(client, inline_document(), user='owui:user:alice', operation=operation).status_code == 201
    fake.collections['kb']['writers'] = []
    assert ingest(client, inline_document(), user='owui:user:alice', operation=operation).status_code == 403


@pytest.mark.parametrize('user', [None, 'owui:user:alice'])
def test_an_unreadable_collection_is_404(api, user):
    """An unreadable collection is concealed for both credential and subject calls."""
    fake, client = api
    seed(client)
    fake.collections['kb']['principals'] = []
    response = ingest(client, inline_document(), user=user)
    assert response.status_code == 404
    assert response.json()['code'] == 'collection_not_found'
    assert not fake.jobs


def test_a_replayed_job_create_returns_no_uploads(api):
    """Create replay returns the current job without reminting uploads."""
    fake, client = api
    seed(client)
    operation = str(uuid4())
    first = ingest(client, file_document(), operation=operation).json()
    replay = ingest(client, file_document(), operation=operation)
    assert replay.status_code == 200
    assert replay.json() == {**first, 'uploads': []}
    fake.advance(first['job_id'], 'RUNNING')
    assert ingest(client, file_document(), operation=operation).json()['status'] == 'RUNNING'
    assert ingest(client, file_document('changed'), operation=operation).status_code == 409
    assert len(fake.jobs) == 1


def test_the_presigned_put_refuses_a_bearer(api):
    """Presigned uploads reject authorization even when it names a valid credential."""
    fake, client = api
    seed(client)
    upload = ingest(client, file_document()).json()['uploads'][0]
    response = client.put(
        upload['url'], content=FILE_BYTES, headers={**upload['headers'], 'Authorization': 'Bearer test-runtime-key'}
    )
    assert response.status_code == 400
    assert response.json()['code'] == 'bearer_on_presigned_put'
    assert not fake.uploads


@pytest.mark.parametrize(
    'length,content,code',
    [
        ('1', FILE_BYTES, 'upload_length_mismatch'),
        (str(len(FILE_BYTES)), b'short', 'upload_length_mismatch'),
        (str(len(FILE_BYTES)), b'x' * len(FILE_BYTES), 'upload_digest_mismatch'),
    ],
)
def test_the_presigned_put_checks_length_and_digest(api, length, content, code):
    """Neither a dishonest length nor bytes with another digest can land."""
    fake, client = api
    seed(client)
    upload = ingest(client, file_document()).json()['uploads'][0]
    response = client.put(upload['url'], content=content, headers={**upload['headers'], 'Content-Length': length})
    assert response.status_code == 400
    assert response.json()['code'] == code
    assert not fake.uploads


def test_commit_before_upload_is_409_upload_missing(api):
    """Commit leaves an incomplete file batch awaiting its missing upload."""
    fake, client = api
    seed(client)
    job = ingest(client, file_document()).json()
    response = send(client, 'POST', f'/v1/jobs/{job["job_id"]}/commit')
    assert response.status_code == 409
    assert response.json()['code'] == 'upload_missing'
    assert fake.jobs[job['job_id']]['status'] == 'AWAITING_UPLOAD'


def test_commit_after_upload_queues_the_job(api):
    """A valid absolute PUT stores bytes and commit only queues the mixed batch."""
    fake, client = api
    seed(client)
    job = ingest(client, file_document(), inline_document()).json()
    upload = job['uploads'][0]
    response = client.put(upload['url'], content=FILE_BYTES, headers=upload['headers'])
    assert response.status_code == 200 and response.content == b''
    assert fake.uploads[job['job_id'], 'file'] == FILE_BYTES
    operation = str(uuid4())
    response = send(client, 'POST', f'/v1/jobs/{job["job_id"]}/commit', operation=operation)
    assert response.status_code == 200 and response.json()['status'] == 'QUEUED'
    assert not fake.documents
    fake.advance(job['job_id'], 'RUNNING')
    assert send(client, 'POST', f'/v1/jobs/{job["job_id"]}/commit', operation=operation).json()['status'] == 'RUNNING'


@pytest.mark.parametrize('status', ['SUCCEEDED', 'COMPLETED_WITH_ERRORS', 'FAILED', 'CANCELLED', 'EXPIRED'])
def test_cancel_of_a_terminal_job_is_409(api, status):
    """Every terminal status refuses cancellation with the published code."""
    fake, client = api
    seed(client)
    job = ingest(client, inline_document()).json()
    fake.advance(job['job_id'], status)
    response = send(client, 'POST', f'/v1/jobs/{job["job_id"]}/cancel')
    assert response.status_code == 409
    assert response.json()['code'] == 'job_already_terminal'


@pytest.mark.parametrize('document', [inline_document(), file_document()])
def test_cancel_skips_pending_items(api, document):
    """Queued and awaiting jobs cancel with all outstanding items skipped."""
    _, client = api
    seed(client)
    job = ingest(client, document).json()
    operation = str(uuid4())
    response = send(client, 'POST', f'/v1/jobs/{job["job_id"]}/cancel', operation=operation)
    assert response.status_code == 200
    assert response.json()['status'] == 'CANCELLED'
    assert response.json()['progress'] == {'total': 1, 'succeeded': 0, 'failed': 0, 'pending': 0, 'skipped': 1}
    items = send(client, 'GET', f'/v1/jobs/{job["job_id"]}?include_items=true').json()['items']
    assert items[0]['status'] == 'skipped'
    assert send(client, 'POST', f'/v1/jobs/{job["job_id"]}/cancel', operation=operation).status_code == 409


def test_cancel_of_a_running_job_leaves_it_unchanged(api):
    """A running job cannot be cancelled and is returned as it stands."""
    fake, client = api
    seed(client)
    job = ingest(client, inline_document()).json()
    fake.advance(job['job_id'], 'RUNNING')
    before = send(client, 'GET', f'/v1/jobs/{job["job_id"]}').json()
    response = send(client, 'POST', f'/v1/jobs/{job["job_id"]}/cancel')
    assert response.status_code == 200 and response.json() == before


@pytest.mark.parametrize('suffix,method', [('', 'GET'), ('/commit', 'POST'), ('/cancel', 'POST')])
def test_job_routes_conceal_unknown_and_other_credentials_jobs(api, suffix, method):
    """Job ownership follows the credential even when subjects may read the collection."""
    fake, client = api
    seed(client)
    job = ingest(client, inline_document()).json()
    fake.credentials['other-key'] = SERVICE
    fake.capabilities['other-key'] = {'*'}
    for job_id, credential in [('missing', 'test-runtime-key'), (job['job_id'], 'other-key')]:
        response = send(client, method, f'/v1/jobs/{job_id}{suffix}', credential=credential, user='owui:user:alice')
        assert response.status_code == 404
        assert response.json()['code'] == 'job_not_found'


def test_get_job_with_items_lists_each_document(api):
    """Items are opt-in and subjects do not impose collection access on job reads."""
    fake, client = api
    seed(client)
    seed(client, 'other', 'owui:user:bob')
    job = ingest(client, inline_document(), file_document()).json()
    path = f'/v1/jobs/{job["job_id"]}'
    assert 'items' not in send(client, 'GET', path, user='owui:user:bob').json()
    assert 'items' not in send(client, 'GET', path + '?include_items=false').json()
    response = send(client, 'GET', path + '?include_items=true', user='owui:user:bob')
    assert response.status_code == 200
    assert response.json()['items'] == [
        {'source_id': source_id, 'status': 'pending', 'code': None, 'detail': None, 'chunk_count': None}
        for source_id in ('inline', 'file')
    ]
    listed = send(client, 'GET', '/v1/jobs?collection_key=kb&status=AWAITING_UPLOAD').json()['data']
    assert listed == [send(client, 'GET', path).json()]
    assert fake.jobs[job['job_id']]['progress']['total'] == 2


def test_get_delete_document_job_with_items_names_its_document(api):
    fake, client = api
    seed(client)
    fake.add_document('kb', 'file')
    job = send(client, 'DELETE', '/v1/collections/kb/documents/file').json()
    path = f'/v1/jobs/{job["job_id"]}'
    assert 'items' not in send(client, 'GET', path).json()
    response = send(client, 'GET', path + '?include_items=true')
    assert response.status_code == 200
    assert response.json()['items'] == [
        {'source_id': 'file', 'status': 'pending', 'code': None, 'detail': None, 'chunk_count': None}
    ]
    listed = send(client, 'GET', '/v1/jobs?collection_key=kb&status=QUEUED&include_items=true').json()['data']
    assert listed == [send(client, 'GET', path).json()]


def test_advance_to_succeeded_materialises_the_documents(api):
    """Success lands metadata and one successful item per proposal at the fake clock."""
    fake, client = api
    seed(client)
    document = file_document(
        title='Report', content_type='application/pdf', path='folder', created_at='2020-01-01T00:00:00Z'
    )
    job = ingest(client, document, inline_document()).json()
    fake.now = '2026-09-12T12:00:00Z'
    fake.advance(job['job_id'], 'SUCCEEDED')
    landed = fake.documents['kb', 'file']
    for field in ('filename', 'title', 'content_type', 'path', 'created_at', 'sha256'):
        assert landed[field] == document[field]
    assert landed['byte_size'] == len(FILE_BYTES)
    assert landed['ingested_at'] == fake.now
    assert landed['last_job_id'] == job['job_id']
    assert len(fake.documents) == 2 and 'folder' in fake.folders['kb']
    result = send(client, 'GET', f'/v1/jobs/{job["job_id"]}?include_items=true').json()
    assert result['progress'] == {'total': 2, 'succeeded': 2, 'failed': 0, 'pending': 0, 'skipped': 0}
    assert all(item['status'] == 'succeeded' and item['chunk_count'] == 1 for item in result['items'])
    fake.now = '2026-09-13T12:00:00Z'
    fake.advance(job['job_id'], 'SUCCEEDED')
    assert fake.documents['kb', 'file'] is landed


def test_advance_with_errors_marks_the_item_and_creates_nothing(api):
    """An explicitly failed batch publishes each refusal without landing documents."""
    fake, client = api
    seed(client)
    job = ingest(client, inline_document(), file_document()).json()
    fake.advance(job['job_id'], 'COMPLETED_WITH_ERRORS', item_code='content_type_rejected', item_detail='Not supported')
    result = send(client, 'GET', f'/v1/jobs/{job["job_id"]}?include_items=true').json()
    assert result['progress'] == {'total': 2, 'succeeded': 0, 'failed': 2, 'pending': 0, 'skipped': 0}
    assert result['items'] == [
        {
            'source_id': source_id,
            'status': 'failed',
            'code': 'content_type_rejected',
            'detail': 'Not supported',
            'chunk_count': None,
        }
        for source_id in ('inline', 'file')
    ]
    assert not fake.documents


@pytest.mark.parametrize('status', ['FAILED', 'CANCELLED', 'EXPIRED'])
def test_unsuccessful_terminal_jobs_create_no_documents(api, status):
    """Failure, cancellation, and expiry never materialise a proposal."""
    fake, client = api
    seed(client)
    job = ingest(client, inline_document()).json()
    fake.advance(job['job_id'], status)
    assert not fake.documents


@pytest.mark.parametrize('user', [None, 'owui:user:alice'])
def test_the_source_lookup_is_gated_like_the_document_read(api, user):
    """Lookup checks collection and document access and sorts all matches without pagination."""
    fake, client = api
    for key in ('z', 'a', 'hidden_collection', 'hidden_document'):
        seed(client, key)
        fake.add_document(key, 'shared')
    fake.collections['hidden_collection']['principals'] = []
    fake.documents['hidden_document', 'shared']['principals'] = ['owui:user:bob']
    fake.add_document('a', 'different')
    result = send(client, 'GET', '/v1/documents?source_id=shared', user=user)
    assert result.status_code == 200
    assert result.json() == {
        'data': [fake.documents['a', 'shared'], fake.documents['z', 'shared']],
        'next_cursor': None,
    }
    for row in result.json()['data']:
        assert send(client, 'GET', f'/v1/collections/{row["collection_key"]}/documents/shared', user=user).json() == row
    assert send(client, 'GET', '/v1/documents?source_id=missing', user=user).json()['data'] == []
    for path in ('/v1/documents', '/v1/documents?source_id='):
        response = send(client, 'GET', path, user=user)
        assert response.status_code == 400 and response.json()['code'] == 'malformed_request'


def test_content_serves_the_rendition_or_409(api):
    """Markdown content, including empty renditions, shares the document read gate."""
    fake, client = api
    seed(client)
    fake.add_document('kb', 'rendered', rendition='# Héllo')
    fake.add_document('kb', 'empty', rendition='')
    fake.add_document('kb', 'pending')
    fake.add_document('kb', 'private', rendition='secret', principals=['owui:user:bob'])
    for user in (None, 'owui:user:alice'):
        response = send(client, 'GET', '/v1/collections/kb/documents/rendered/content', user=user)
        assert response.status_code == 200 and response.text == '# Héllo'
        assert response.headers['content-type'].startswith('text/markdown')
        assert send(client, 'GET', '/v1/collections/kb/documents/empty/content', user=user).content == b''
        response = send(client, 'GET', '/v1/collections/kb/documents/pending/content', user=user)
        assert response.status_code == 409 and response.json()['code'] == 'rendition_missing'
        for source_id in ('missing', 'private'):
            response = send(client, 'GET', f'/v1/collections/kb/documents/{source_id}/content', user=user)
            assert response.status_code == 404 and response.json()['code'] == 'document_not_found'
    fake.collections['kb']['principals'] = []
    response = send(client, 'GET', '/v1/collections/kb/documents/rendered/content')
    assert response.status_code == 404 and response.json()['code'] == 'document_not_found'


@pytest.mark.parametrize(
    'body',
    [
        {'collection_key': 'kb', 'documents': [inline_document()], 'typo': True},
        {'collection_key': 'kb', 'documents': [inline_document(typo=True)]},
        {'collection_key': 'kb', 'documents': [file_document(typo=True)]},
        {'collection_key': 'kb', 'documents': [file_document(text='mixed arms')]},
    ],
)
def test_undeclared_keys_are_400_unknown_field(api, body):
    """Extra keys follow the real validation handler's unknown_field mapping."""
    fake, client = api
    seed(client)
    response = send(client, 'POST', '/v1/jobs', body)
    assert response.status_code == 400
    assert response.json()['code'] == 'unknown_field'
    assert response.json()['constraint'] == 'request:extra_forbidden'
    assert not fake.jobs


@pytest.mark.parametrize(
    'documents',
    [
        [],
        [inline_document()] * 1001,
        [None],
        [{'source_id': 'file', 'filename': 'file.txt', 'size': 1}],
        [{'source_id': 'file', 'filename': 'file.txt', 'sha256': 'a' * 64}],
        [file_document(size=0)],
        [file_document(sha256='A' * 64)],
        [file_document(sha256='a' * 63)],
        [inline_document(source_id='')],
        [inline_document(filename='')],
        [inline_document(text=None)],
    ],
)
def test_invalid_document_shapes_are_422(api, documents):
    """Batch bounds, required fields, and file arm constraints fail before creating a job."""
    fake, client = api
    seed(client)
    response = send(client, 'POST', '/v1/jobs', {'collection_key': 'kb', 'documents': documents})
    assert response.status_code == 422
    assert response.json()['code'] == 'invalid_field'
    assert not fake.jobs


def test_the_fake_never_advances_a_job_on_its_own(api):
    """Polling, clock changes, upload, and commit never run or finish a job."""
    fake, client = api
    seed(client)
    file_job = ingest(client, file_document()).json()
    inline_job = ingest(client, inline_document()).json()
    fake.now = '2030-01-01T00:00:00Z'
    for _ in range(3):
        assert send(client, 'GET', f'/v1/jobs/{file_job["job_id"]}').json()['status'] == 'AWAITING_UPLOAD'
        assert send(client, 'GET', f'/v1/jobs/{inline_job["job_id"]}').json()['status'] == 'QUEUED'
    upload = file_job['uploads'][0]
    assert client.put(upload['url'], content=FILE_BYTES, headers=upload['headers']).status_code == 200
    for job in (file_job, inline_job):
        for _ in range(3):
            response = send(client, 'POST', f'/v1/jobs/{job["job_id"]}/commit')
            assert response.status_code == 200 and response.json()['status'] == 'QUEUED'
            assert send(client, 'GET', f'/v1/jobs/{job["job_id"]}').json()['status'] == 'QUEUED'
    assert not fake.documents
