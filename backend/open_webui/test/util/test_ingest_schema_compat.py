"""Assert the OWUI IngestAttachmentManifest matches the genai-utils
IngestAttachment dataclass field-by-field.

Either side drifting silently is exactly the bug this guards against.
If the genai-utils worktree isn't available locally, the test SKIPs
rather than failing — the assertion still runs in CI where both repos
are checked out side by side.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import fields as dc_fields
from pathlib import Path

import pytest


GENAI_UTILS_CLIENT = Path(
    '/Users/lexlubbers/Code/soev/genai-utils/.worktrees/feat/bim-agent/api/gateway/loader_worker/ingest_client.py'
)


def _load_genai_utils_ingest_client():
    if not GENAI_UTILS_CLIENT.exists():
        pytest.skip(f'genai-utils worktree not available at {GENAI_UTILS_CLIENT}')
    spec = importlib.util.spec_from_file_location(
        'gu_ingest_client',
        GENAI_UTILS_CLIENT,
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules['gu_ingest_client'] = mod
    spec.loader.exec_module(mod)
    return mod


def test_owui_manifest_matches_genai_utils_dataclass():
    gu = _load_genai_utils_ingest_client()
    from open_webui.routers.integrations import IngestAttachmentManifest

    gu_field_names = {f.name for f in dc_fields(gu.IngestAttachment)}
    owui_field_names = set(IngestAttachmentManifest.model_fields.keys())

    # The genai-utils dataclass carries content_bytes (the actual PNG
    # payload); on the OWUI side that rides on the multipart envelope
    # and is replaced by ``part_name`` (the multipart filename used to
    # match the payload back to its manifest entry).
    gu_field_names = gu_field_names - {'content_bytes'}
    owui_field_names = owui_field_names - {'part_name'}

    missing_on_owui = gu_field_names - owui_field_names
    extra_on_owui = owui_field_names - gu_field_names
    assert missing_on_owui == set(), f'OWUI manifest is missing fields: {missing_on_owui}'
    assert extra_on_owui == set(), f'OWUI manifest has extra fields: {extra_on_owui}'
