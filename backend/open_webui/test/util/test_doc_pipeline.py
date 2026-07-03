"""Tests for the distributed doc-pipeline (warren) job-submit client."""

import json

import httpx
import pytest

from open_webui.utils import doc_pipeline


def test_build_job_submission_targets_existing_file_row():
    """The job body reconstructs the *existing* upload file_id at /ingest.

    owui.document.source_id == item.uuid == file_id and acting_provider is the
    empty-prefix slug, so /ingest's f'{prefix}{source_id}' lands on the
    existing row instead of creating a twin (the #1 correctness invariant)."""
    body = doc_pipeline.build_job_submission(
        file_id='file-uuid-1',
        filename='report.pdf',
        content_type='application/pdf',
        file_format='pdf',
        presigned_url='https://s3.example/report.pdf?sig=abc',
        kb_id='kb-uuid-9',
        kb_name='My KB',
        acting_user_id='user-7',
        ingest_url='http://owui:8080',
        chunk_size=1000,
        chunk_overlap=100,
    )

    items = body['metadata']['items']
    assert len(items) == 1, 'one warren job carries exactly one document'
    item = items[0]
    assert item['type'] == 'file'
    assert item['format'] == 'pdf'
    assert item['url'] == 'https://s3.example/report.pdf?sig=abc'
    assert item['uuid'] == 'file-uuid-1'

    params = body['parameters']
    assert params['chunk_size'] == 1000
    assert params['chunk_overlap'] == 100

    owui = params['owui']
    assert owui['ingest_url'] == 'http://owui:8080'
    assert owui['acting_user_id'] == 'user-7'
    assert owui['acting_provider'] == 'owui_upload'
    # target defaults to 'knowledge' (KB path); the worker echoes the key through.
    assert owui['collection'] == {'source_id': 'kb-uuid-9', 'name': 'My KB', 'target': 'knowledge'}
    assert owui['document']['source_id'] == 'file-uuid-1'  # identity reconstruction
    assert owui['document']['filename'] == 'report.pdf'
    assert owui['document']['content_type'] == 'application/pdf'


def test_build_job_submission_collection_target_file():
    """collection_target='file' → per-file chat-attachment target in the owui block."""
    body = doc_pipeline.build_job_submission(
        file_id='file-uuid-1',
        filename='report.pdf',
        content_type='application/pdf',
        file_format='pdf',
        presigned_url='https://s3.example/report.pdf?sig=abc',
        kb_id='file-uuid-1',
        kb_name='report.pdf',
        acting_user_id='user-7',
        ingest_url='http://owui:8080',
        chunk_size=1000,
        chunk_overlap=100,
        collection_target='file',
    )
    collection = body['parameters']['owui']['collection']
    assert collection['target'] == 'file'
    # document.source_id stays the file_id so /ingest's per-file branch derives
    # file-{file_id} and updates the existing row (owui_upload identity).
    assert body['parameters']['owui']['document']['source_id'] == 'file-uuid-1'


def test_build_job_submission_omits_final_data_type():
    """final_data_type omitted → the staging default (owui_ingested) applies."""
    body = doc_pipeline.build_job_submission(
        file_id='f',
        filename='a.docx',
        content_type='x',
        file_format='docx',
        presigned_url='https://u',
        kb_id='k',
        kb_name='n',
        acting_user_id='u',
        ingest_url='http://o',
        chunk_size=1000,
        chunk_overlap=100,
    )
    assert 'final_data_type' not in body


@pytest.mark.parametrize(
    'filename,expected',
    [
        ('Report.PDF', 'pdf'),
        ('sheet.xlsx', 'xlsx'),
        ('deck.PPTX', 'pptx'),
        ('a.b.csv', 'csv'),
        ('noext', ''),
    ],
)
def test_format_from_filename(filename, expected):
    assert doc_pipeline.format_from_filename(filename) == expected


@pytest.mark.parametrize(
    'enabled,collection_name,file_path,file_format,expected',
    [
        (True, 'kb-1', 's3://b/k', 'pdf', True),  # flag on, KB-bound, path, supported → route
        (True, 'kb-1', 's3://b/k', 'docx', True),  # another supported format
        (True, 'kb-1', 's3://b/k', 'htm', True),  # htm parses via warren's HtmlProcessor, same as html
        (False, 'kb-1', 's3://b/k', 'pdf', False),  # flag off → native
        (True, None, 's3://b/k', 'pdf', False),  # per-file cache (no KB) → native
        (True, '', 's3://b/k', 'pdf', False),  # no KB → native
        (True, 'kb-1', '', 'pdf', False),  # no storage path to presign → native
        (True, 'kb-1', None, 'pdf', False),  # no storage path → native
        (True, 'kb-1', 's3://b/k', 'svg', False),  # unsupported (image) → native, not warren
        (True, 'kb-1', 's3://b/k', 'png', False),  # unsupported → native
        (True, 'kb-1', 's3://b/k', 'dmg', False),  # unsupported binary → native
        (True, 'kb-1', 's3://b/k', '', False),  # no extension → native
    ],
)
def test_should_route_to_pipeline(enabled, collection_name, file_path, file_format, expected):
    assert (
        doc_pipeline.should_route_to_pipeline(
            enabled=enabled,
            collection_name=collection_name,
            file_path=file_path,
            file_format=file_format,
        )
        is expected
    )


@pytest.mark.parametrize('fmt', sorted(doc_pipeline.PIPELINE_SUPPORTED_FORMATS))
def test_should_route_chat_to_pipeline_true_for_every_supported_format(fmt):
    """Every warren-parseable format routes a chat attachment (flag on, path set)."""
    assert doc_pipeline.should_route_chat_to_pipeline(enabled=True, file_path='s3://b/k', file_format=fmt) is True


@pytest.mark.parametrize(
    'enabled,file_path,file_format,expected',
    [
        (True, 's3://b/k', 'pdf', True),  # supported format, flag on, path → route
        (True, 's3://b/k', 'docx', True),
        (True, 's3://b/k', 'htm', True),  # htm parses via warren's HtmlProcessor, same as html
        (False, 's3://b/k', 'pdf', False),  # flag off → native (independent of KB flag)
        (True, 's3://b/k', 'png', False),  # image → native
        (True, 's3://b/k', 'jpg', False),  # image → native
        (True, 's3://b/k', 'mp3', False),  # audio → native (STT path)
        (True, 's3://b/k', 'dmg', False),  # unknown binary → native
        (True, 's3://b/k', '', False),  # no extension → native
        (True, '', 'pdf', False),  # no storage path to presign → native
        (True, None, 'pdf', False),  # no storage path → native
    ],
)
def test_should_route_chat_to_pipeline(enabled, file_path, file_format, expected):
    assert (
        doc_pipeline.should_route_chat_to_pipeline(enabled=enabled, file_path=file_path, file_format=file_format)
        is expected
    )


@pytest.mark.parametrize(
    'job_status,age,cap,expected',
    [
        ('failed', 10, 21600, 'fail'),  # warren job failed → error now
        ('partial', 10, 21600, 'fail'),  # 1-doc job 'partial' == failure
        ('running', 10, 21600, 'wait'),  # in progress → keep waiting
        ('pending', 10, 21600, 'wait'),  # queued → keep waiting
        ('completed', 10, 21600, 'empty'),  # done on warren, file still processing → zero-chunk (no /ingest)
        ('running', 21601, 21600, 'timeout'),  # hung past the generous backstop → error
        ('completed', 99999, 21600, 'empty'),  # completed-but-empty wins over the wall-clock backstop
        ('failed', 99999, 21600, 'fail'),  # failure reason wins over timeout
    ],
)
def test_reconcile_action(job_status, age, cap, expected):
    assert doc_pipeline.reconcile_action(job_status=job_status, age_seconds=age, max_wall_clock_seconds=cap) == expected


@pytest.mark.asyncio
async def test_submit_job_posts_to_jobs_with_bearer():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['url'] = str(request.url)
        captured['auth'] = request.headers.get('authorization')
        captured['body'] = json.loads(request.content)
        return httpx.Response(201, json={'job_id': 'job-42', 'created_at': '2026-06-30T00:00:00Z'})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    job_id = await doc_pipeline.submit_job(
        base_url='http://pipe:8080/',
        api_key='secret',
        submission={'hello': 'world'},
        client=client,
    )
    assert job_id == 'job-42'
    assert captured['url'] == 'http://pipe:8080/jobs'
    assert captured['auth'] == 'Bearer secret'
    assert captured['body'] == {'hello': 'world'}


@pytest.mark.asyncio
async def test_get_job_status_returns_status():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/jobs/job-42'
        assert request.headers.get('authorization') == 'Bearer secret'
        return httpx.Response(
            200, json={'job_id': 'job-42', 'status': 'completed', 'created_at': '2026-06-30T00:00:00Z'}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    status = await doc_pipeline.get_job_status(
        base_url='http://pipe:8080',
        api_key='secret',
        job_id='job-42',
        client=client,
    )
    assert status == 'completed'
