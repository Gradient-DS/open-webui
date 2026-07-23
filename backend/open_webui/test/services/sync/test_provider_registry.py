"""Guards the provider_slug -> file_id_prefix registry.

The registry in services.sync.provider is the single source of truth for the
ingest endpoint. If the registry value and the file_id prefix stamped on stub
File rows ever drift, the ingest endpoint reconstructs the wrong file_id and
creates duplicate File rows on every successful sync (the 2026-04-29 incident).

The literal prefixes below ARE that contract — the external sync-daemon stamps
exactly these on its stub rows, so this test pins them without importing any
worker (the in-pod workers were removed when sync moved to the daemon).
"""

import pytest

from open_webui.services.sync.provider import (
    PROVIDER_FILE_ID_PREFIXES,
    file_id_prefix_for,
)

# The file_id prefix each managed-sync provider stamps on its stub File rows.
# The sync-daemon and the /ingest reconstruction must agree on these exact
# strings — note google_drive's slug (google_drive) differs from its prefix
# (googledrive-), the original source of the 2026-04-29 twin-row incident.
_EXPECTED_PREFIXES = {
    'onedrive': 'onedrive-',
    'google_drive': 'googledrive-',
    'confluence': 'confluence-',
    'owui_upload': '',
}


@pytest.mark.parametrize('slug,prefix', list(_EXPECTED_PREFIXES.items()))
def test_registry_matches_expected_prefix(slug, prefix):
    """The registry pins the exact prefix each provider stamps."""
    assert PROVIDER_FILE_ID_PREFIXES[slug] == prefix


@pytest.mark.parametrize('slug', list(_EXPECTED_PREFIXES))
def test_file_id_prefix_for_returns_registry_value(slug):
    assert file_id_prefix_for(slug) == PROVIDER_FILE_ID_PREFIXES[slug]


def test_registry_has_no_unexpected_entries():
    """No provider may enter the registry without pinning its prefix here."""
    assert set(PROVIDER_FILE_ID_PREFIXES) == set(_EXPECTED_PREFIXES)


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


def test_owui_upload_slug_has_empty_prefix():
    """The direct-upload provider slug maps to an EMPTY prefix.

    Direct-upload File rows already exist with a bare UUID id (no provider
    prefix). The distributed-doc-pipeline path POSTs the parsed chunks back
    through the same /ingest endpoint with acting_provider='owui_upload' and
    document.source_id=<file_id>. The empty prefix makes the ingest-side
    reconstruction f'{prefix}{source_id}' an identity, so warren updates the
    existing upload row instead of creating a twin (the 2026-04-29 failure
    mode, inverted)."""
    assert file_id_prefix_for('owui_upload') == ''


def test_owui_upload_reconstruction_is_identity():
    """f'{prefix}{file_id}' == file_id for the direct-upload slug."""
    file_id = 'a1b2c3d4-0000-0000-0000-abcdef012345'
    assert f'{file_id_prefix_for("owui_upload")}{file_id}' == file_id


def test_round_trip_stub_vs_ingest_file_id():
    """Stub-side f'{prefix}{item_id}' must equal ingest-side reconstruction.

    This is the exact failure mode of the 2026-04-29 incident: stubs were
    inserted with file_id_prefix='googledrive-' but ingest reconstructed
    with f'{provider_slug}-...' = 'google_drive-...', creating a twin row
    per file. The stub prefix now comes from the daemon; both sides must
    resolve through the same registry.
    """
    item_id = 'abc123'
    for slug, prefix in _EXPECTED_PREFIXES.items():
        stub_file_id = f'{prefix}{item_id}'
        ingest_file_id = f'{file_id_prefix_for(slug)}{item_id}'
        assert stub_file_id == ingest_file_id, (
            f'round-trip mismatch for slug={slug!r}: stub={stub_file_id!r} ingest={ingest_file_id!r}'
        )
