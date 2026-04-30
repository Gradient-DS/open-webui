"""Guards Phase 5/6 wiring on the OWUI side.

- _validate_callback_base_url: misconfigured WEBUI_PUBLIC_BASE_URL fails fast.
- _LOADER_ERROR_CODE_TO_SYNC_TYPE: every loader-worker error code is mapped.
"""

from __future__ import annotations

import pytest

from open_webui.services.sync.base_worker import (
    _LOADER_ERROR_CODE_TO_SYNC_TYPE,
    ConfigurationError,
    _validate_callback_base_url,
)
from open_webui.services.sync.constants import SyncErrorType


def test_validate_callback_base_url_accepts_valid_http():
    _validate_callback_base_url('http://host.example.com')
    _validate_callback_base_url('https://host.example.com:8080/path')


def test_validate_callback_base_url_rejects_empty():
    with pytest.raises(ConfigurationError, match='WEBUI_PUBLIC_BASE_URL'):
        _validate_callback_base_url('')


def test_validate_callback_base_url_rejects_no_scheme():
    with pytest.raises(ConfigurationError, match='invalid'):
        _validate_callback_base_url('not-a-url')


def test_validate_callback_base_url_rejects_unknown_scheme():
    with pytest.raises(ConfigurationError, match='invalid'):
        _validate_callback_base_url('ftp://host.example.com')


def test_validate_callback_base_url_rejects_no_host():
    with pytest.raises(ConfigurationError, match='invalid'):
        _validate_callback_base_url('http://')


# Loader-worker error codes — duplicated here on purpose. OWUI doesn't import
# from genai-utils; if a new error code appears in the loader-worker that
# OWUI hasn't mapped, this test catches the gap during PR review.
_LOADER_WORKER_ERROR_CODES = frozenset(
    {
        'cancelled',
        'needs_token_refresh',
        'hard_source_error',
        'empty_extraction',
        'doc_processor_schema_error',
        'config_error',
        'unsupported_content_type',
        'source_access_revoked',
        'unexpected_error',
    }
)


def test_loader_error_code_map_covers_every_known_code():
    """Every loader-worker error_code must map to a SyncErrorType."""
    missing = _LOADER_WORKER_ERROR_CODES - set(_LOADER_ERROR_CODE_TO_SYNC_TYPE)
    assert not missing, f'_LOADER_ERROR_CODE_TO_SYNC_TYPE is missing entries for: {sorted(missing)}'


def test_loader_error_code_map_values_are_sync_error_types():
    for code, value in _LOADER_ERROR_CODE_TO_SYNC_TYPE.items():
        assert isinstance(value, SyncErrorType), (
            f'_LOADER_ERROR_CODE_TO_SYNC_TYPE[{code!r}] = {value!r} is not a SyncErrorType'
        )
