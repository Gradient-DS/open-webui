"""Tests for ``ExportService._get_local_file_ids`` cloud-KB exclusion.

The GDPR export zip only embeds the *content* of local KBs; cloud-synced KBs
(onedrive/google_drive/confluence) are re-syncable, so their file bytes
are excluded (metadata is still exported elsewhere). These tests pin every
cloud type plus the local case.

``_get_local_file_ids`` is a pure static method over a plain dict, so no DB or
app is needed.
"""

import pytest

from open_webui.services.export.service import ExportService


def _data(kb_type: str):
    """Export-data dict: one KB of ``kb_type`` owning file ``f-cloud`` plus a
    free-standing local file ``f-local``."""
    return {
        'knowledge_bases': [
            {'id': 'kb-1', 'type': kb_type, 'data': {'file_ids': ['f-cloud']}},
        ],
        'files': [
            {'id': 'f-cloud'},
            {'id': 'f-local'},
        ],
    }


@pytest.mark.parametrize('kb_type', ['onedrive', 'google_drive', 'confluence'])
def test_cloud_kb_files_excluded_from_local(kb_type):
    local_ids = ExportService._get_local_file_ids(_data(kb_type))
    # Cloud KB file is excluded; the unrelated local file remains.
    assert 'f-cloud' not in local_ids
    assert 'f-local' in local_ids


def test_local_kb_files_included():
    local_ids = ExportService._get_local_file_ids(_data('local'))
    # A local KB's files are NOT excluded.
    assert set(local_ids) == {'f-cloud', 'f-local'}
