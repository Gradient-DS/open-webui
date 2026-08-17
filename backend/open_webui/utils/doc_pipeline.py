"""Client for the distributed document pipeline (warren) job API.

When ``DISTRIBUTED_DOC_PIPELINE_ENABLED``, KB ingestion is delegated to the
external pipeline instead of native parse+embed: this server presigns the
uploaded file, submits a job here, and warren parses + chunks it and POSTs
``chunked_text`` back to this server's ``/ingest`` endpoint (which embeds +
inserts exactly as today). Warren never touches the vector DB on this path.

One warren job carries exactly **one** document — the ``OwuiIngestWorker``
reads a single ``owui.document`` from ``job_parameters`` — so callers submit
one job per file (the batch upload route loops).

The body mirrors soev-solutions' ``JobSubmissionRequest`` plus the
``OwuiIngestWorker`` ``owui`` contract; keep it in sync if that changes.
See thoughts/shared/plans/2026-06-30-doc-pipeline-previder-staging-tierb.md.
"""

import contextlib
import os
from typing import Any, AsyncIterator, Optional

import httpx

# Empty-prefix provider slug for direct uploads — see
# services/sync/provider.PROVIDER_FILE_ID_PREFIXES. With document.source_id
# set to the existing file_id, /ingest's f'{prefix}{source_id}' reconstruction
# is an identity, so warren updates the existing upload row, not a twin.
ACTING_PROVIDER = 'owui_upload'

_JOBS_PATH = '/jobs'
_DEFAULT_TIMEOUT_SECONDS = 30.0

# Extensions the warren parser can actually parse — mirrors the distributed
# pipeline's parser registry (pdf / OOXML office incl. dotx/xltx/potx templates
# via the ZIP-magic sniffer / text / html / xml). Formats warren can't parse
# (OpenDocument odt/ods/odp, epub, legacy .doc, .msg) stay on OWUI's native
# path, which has real loaders for them. Anything routed here that warren still
# can't handle now hard-fails fast (parser raises -> job FAILED, reconciler
# marks the file 'error') instead of the old silent-'pending' hang.
PIPELINE_SUPPORTED_FORMATS = frozenset(
    {
        'pdf',
        'docx',
        'dotx',
        'xlsx',
        'xltx',
        'pptx',
        'potx',
        'csv',
        'html',
        'htm',
        'xml',
        'txt',
        'md',
        'markdown',
        'text',
    }
)


def format_from_filename(filename: str) -> str:
    """Return the lowercase file extension without the dot.

    e.g. ``'Report.PDF' -> 'pdf'``, ``'noext' -> ''``. This is the warren
    item ``format`` token (pdf/docx/xlsx/pptx/csv/...)."""
    return os.path.splitext(filename)[1].lstrip('.').lower()


# Content types that map to a warren format token when the filename carries no
# extension. Sync-daemon-staged files use human display names (e.g. a
# Confluence page title) as the filename; the format then rides content_type.
CONTENT_TYPE_FORMATS: dict[str, str] = {
    'application/pdf': 'pdf',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'pptx',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
    'application/xml': 'xml',
    'text/csv': 'csv',
    'text/html': 'html',
    'text/markdown': 'md',
    'text/plain': 'txt',
    'text/xml': 'xml',
}


def resolve_format(filename: Optional[str], content_type: Optional[str]) -> str:
    """Warren format token from the extension, falling back to content_type.

    The extension wins when present (authoritative for real files); an
    extension-less filename resolves via ``CONTENT_TYPE_FORMATS`` so display
    names without extensions still route to the pipeline. Unknown → ''."""
    from_name = format_from_filename(filename or '')
    if from_name:
        return from_name
    normalized = (content_type or '').split(';')[0].strip().lower()
    return CONTENT_TYPE_FORMATS.get(normalized, '')


def should_route_to_pipeline(
    *,
    enabled: bool,
    collection_name: Optional[str],
    file_path: Optional[str],
    file_format: Optional[str],
) -> bool:
    """Whether this file should be handed to the distributed pipeline.

    Requires the feature flag on, a **KB-bound** ingestion (``collection_name``
    set — not the per-file ``file-{id}`` chat-with-file cache), a storage
    ``file_path`` to presign, and a ``file_format`` warren can parse
    (``PIPELINE_SUPPORTED_FORMATS``). Any miss → caller runs the native path."""
    return bool(enabled and collection_name and file_path and file_format in PIPELINE_SUPPORTED_FORMATS)


def should_route_chat_to_pipeline(
    *,
    enabled: bool,
    file_path: Optional[str],
    file_format: Optional[str],
) -> bool:
    """Whether a non-KB **chat attachment** should be handed to warren.

    Same format gate as KB routing (``PIPELINE_SUPPORTED_FORMATS``) and the same
    storage-``file_path`` requirement, but WITHOUT the ``collection_name`` term —
    a chat attachment has no KB; its chunks land in the per-file ``file-{id}``
    cache. Gated by its own flag (``DISTRIBUTED_DOC_PIPELINE_CHAT_ENABLED``) so
    chat routing toggles independently of KB routing. Any miss → native path."""
    return bool(enabled and file_path and file_format in PIPELINE_SUPPORTED_FORMATS)


def reconcile_action(
    *,
    job_status: str,
    age_seconds: float,
    max_wall_clock_seconds: float,
) -> str:
    """Decide what the reconciler should do for a file still 'processing'.

    Keyed off the real pipeline-api job status, not a clock — a healthy job
    is waited on for as long as it runs. Returns:
    - ``'fail'``  — the job reported failure (``failed``/``partial``); mark the
      file 'error' now.
    - ``'empty'`` — the job ``completed`` yet the file is still 'processing',
      meaning ``/ingest`` was never called: warren parsed zero chunks (scanned
      / no-text document). One warren job carries exactly one document, and a
      document that produced chunks commits the file to 'completed' via the
      synchronous ``/ingest`` call *before* the job finishes — so a completed
      job whose file is still 'processing' can only be the zero-chunk case.
      Mark it terminal now instead of waiting out the wall-clock backstop.
    - ``'timeout'`` — the job never reached a terminal within the generous
      wall-clock backstop (hung worker); mark 'error'.
    - ``'wait'``  — still running/pending; leave the file as-is.

    Failure takes precedence, then the completed-but-empty terminal, then the
    wall-clock backstop. The caller (the reconciler) only ever passes files
    still in 'processing', which is what makes the 'empty' inference sound."""
    if job_status in ('failed', 'partial'):
        return 'fail'
    if job_status == 'completed':
        return 'empty'
    if age_seconds > max_wall_clock_seconds:
        return 'timeout'
    return 'wait'


def build_job_submission(
    *,
    file_id: str,
    filename: str,
    content_type: str,
    file_format: str,
    presigned_url: str,
    kb_id: str,
    kb_name: str,
    acting_user_id: str,
    ingest_url: str,
    chunk_size: int,
    chunk_overlap: int,
    acting_provider: str = ACTING_PROVIDER,
    collection_target: str = 'knowledge',
) -> dict[str, Any]:
    """Build the ``POST /jobs`` body for one uploaded file.

    ``final_data_type`` is intentionally omitted so the pipeline's configured
    default (``owui_ingested``) applies. ``item.uuid`` and
    ``owui.document.source_id`` are both the OWUI ``file_id`` so the /ingest
    callback reconstructs the existing file row.

    ``collection_target`` is echoed opaquely by warren into the /ingest
    ``collection`` block: ``'knowledge'`` (default) is the KB path; ``'file'`` is
    the per-file chat-attachment path that lands chunks in ``file-{file_id}``
    with no KB link (see IngestCollection.target)."""
    return {
        'metadata': {
            'metadata': {},
            'items': [
                {
                    'source': filename,
                    'uuid': file_id,
                    'url': presigned_url,
                    'type': 'file',
                    'format': file_format,
                    'title': filename,
                }
            ],
        },
        'parameters': {
            'chunk_size': chunk_size,
            'chunk_overlap': chunk_overlap,
            'owui': {
                'ingest_url': ingest_url,
                'acting_user_id': acting_user_id,
                'acting_provider': acting_provider,
                'collection': {'source_id': kb_id, 'name': kb_name, 'target': collection_target},
                'document': {
                    'source_id': file_id,
                    'filename': filename,
                    'content_type': content_type,
                    'metadata': {},
                },
            },
        },
    }


@contextlib.asynccontextmanager
async def _client(injected: Optional[httpx.AsyncClient]) -> AsyncIterator[httpx.AsyncClient]:
    """Yield the injected client (caller-owned) or a temporary one."""
    if injected is not None:
        yield injected
        return
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS) as client:
        yield client


async def submit_job(
    *,
    base_url: str,
    api_key: str,
    submission: dict[str, Any],
    client: Optional[httpx.AsyncClient] = None,
) -> str:
    """POST the job to pipeline-api ``/jobs``; return the created ``job_id``.

    :raises httpx.HTTPStatusError: on a non-2xx response."""
    url = f'{base_url.rstrip("/")}{_JOBS_PATH}'
    headers = {'Authorization': f'Bearer {api_key}'}
    async with _client(client) as c:
        response = await c.post(url, json=submission, headers=headers)
        response.raise_for_status()
        return response.json()['job_id']


async def get_job_status(
    *,
    base_url: str,
    api_key: str,
    job_id: str,
    client: Optional[httpx.AsyncClient] = None,
) -> str:
    """GET pipeline-api ``/jobs/{id}``; return the status string.

    Status is one of pending/running/completed/partial/failed.
    :raises httpx.HTTPStatusError: on a non-2xx response."""
    url = f'{base_url.rstrip("/")}{_JOBS_PATH}/{job_id}'
    headers = {'Authorization': f'Bearer {api_key}'}
    async with _client(client) as c:
        response = await c.get(url, headers=headers)
        response.raise_for_status()
        return response.json()['status']
