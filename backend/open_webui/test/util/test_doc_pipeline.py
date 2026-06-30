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
    assert owui['collection'] == {'source_id': 'kb-uuid-9', 'name': 'My KB'}
    assert owui['document']['source_id'] == 'file-uuid-1'  # identity reconstruction
    assert owui['document']['filename'] == 'report.pdf'
    assert owui['document']['content_type'] == 'application/pdf'


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
    'enabled,collection_name,file_path,expected',
    [
        (True, 'kb-1', 's3://b/k', True),  # flag on, KB-bound, has a path → route
        (False, 'kb-1', 's3://b/k', False),  # flag off → native
        (True, None, 's3://b/k', False),  # per-file cache (no KB) → native
        (True, '', 's3://b/k', False),  # no KB → native
        (True, 'kb-1', '', False),  # no storage path to presign → native
        (True, 'kb-1', None, False),  # no storage path → native
    ],
)
def test_should_route_to_pipeline(enabled, collection_name, file_path, expected):
    assert (
        doc_pipeline.should_route_to_pipeline(enabled=enabled, collection_name=collection_name, file_path=file_path)
        is expected
    )


@pytest.mark.parametrize(
    'job_status,age,cap,expected',
    [
        ('failed', 10, 21600, 'fail'),  # warren job failed → error now
        ('partial', 10, 21600, 'fail'),  # 1-doc job 'partial' == failure
        ('running', 10, 21600, 'wait'),  # in progress → keep waiting
        ('pending', 10, 21600, 'wait'),  # queued → keep waiting
        ('completed', 10, 21600, 'wait'),  # done on warren; give /ingest a moment
        ('running', 21601, 21600, 'timeout'),  # hung past the generous backstop → error
        ('completed', 99999, 21600, 'timeout'),  # /ingest never landed within cap → error
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
