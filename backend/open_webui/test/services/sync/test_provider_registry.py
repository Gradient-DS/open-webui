"""Guards the provider_slug -> file_id_prefix registry.

The registry in services.sync.provider is the single source of truth for the
ingest endpoint. If a worker's file_id_prefix property and the registry value
ever drift, the ingest endpoint reconstructs the wrong file_id and creates
duplicate File rows on every successful sync (the 2026-04-29 incident).
"""

import pytest

from open_webui.services.confluence.sync_worker import ConfluenceSyncWorker
from open_webui.services.google_drive.sync_worker import GoogleDriveSyncWorker
from open_webui.services.onedrive.sync_worker import OneDriveSyncWorker
from open_webui.services.sync.provider import (
    PROVIDER_FILE_ID_PREFIXES,
    file_id_prefix_for,
)

_WORKERS_BY_SLUG = {
    'onedrive': OneDriveSyncWorker,
    'google_drive': GoogleDriveSyncWorker,
    'confluence': ConfluenceSyncWorker,
}


def _read_worker_prefix(worker_cls):
    """Read file_id_prefix off the class without instantiating.

    Each worker's getter returns a literal string and never touches self,
    so calling fget(None) is safe and avoids the heavyweight constructor
    (which wants knowledge_id, sources, access_token, user_id, app).
    """
    descriptor = worker_cls.__dict__.get('file_id_prefix')
    if descriptor is None:
        for base in worker_cls.__mro__[1:]:
            descriptor = base.__dict__.get('file_id_prefix')
            if descriptor is not None:
                break
    assert isinstance(descriptor, property), f'{worker_cls.__name__}.file_id_prefix is not a property'
    return descriptor.fget(None)


@pytest.mark.parametrize('slug,worker_cls', list(_WORKERS_BY_SLUG.items()))
def test_registry_matches_worker_prefix(slug, worker_cls):
    """Each worker's file_id_prefix property must equal the registry value."""
    assert PROVIDER_FILE_ID_PREFIXES[slug] == _read_worker_prefix(worker_cls)


@pytest.mark.parametrize('slug', list(PROVIDER_FILE_ID_PREFIXES))
def test_file_id_prefix_for_returns_registry_value(slug):
    assert file_id_prefix_for(slug) == PROVIDER_FILE_ID_PREFIXES[slug]


def test_file_id_prefix_for_unknown_falls_back_to_slug_dash():
    """External push providers (no worker class, not in registry) get
    ``f'{slug}-'`` — the pre-cc24c435b slug-as-prefix convention. The
    helper must not raise on slugs missing from the registry; admin-
    configured providers (e.g. ``gradient``, ``octobox``) need to ingest
    too, and their auth is enforced elsewhere (get_integration_provider
    + allowed_kb_types)."""
    assert file_id_prefix_for('gradient') == 'gradient-'
    assert file_id_prefix_for('dropbox') == 'dropbox-'
    # Even an empty string is total — the helper has no business deciding
    # which slugs exist; that's the auth layer's job.
    assert file_id_prefix_for('') == '-'


def test_round_trip_stub_vs_ingest_file_id():
    """Stub-side f'{prefix}{item_id}' must equal ingest-side reconstruction.

    This is the exact failure mode of the 2026-04-29 incident: stubs were
    inserted with file_id_prefix='googledrive-' but ingest reconstructed
    with f'{provider_slug}-...' = 'google_drive-...', creating a twin row
    per file.
    """
    item_id = 'abc123'
    for slug, worker_cls in _WORKERS_BY_SLUG.items():
        worker_prefix = _read_worker_prefix(worker_cls)
        stub_file_id = f'{worker_prefix}{item_id}'
        ingest_file_id = f'{file_id_prefix_for(slug)}{item_id}'
        assert stub_file_id == ingest_file_id, (
            f'round-trip mismatch for slug={slug!r}: stub={stub_file_id!r} ingest={ingest_file_id!r}'
        )
