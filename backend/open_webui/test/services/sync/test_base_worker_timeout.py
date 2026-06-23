"""Guards the runtime-read wall-clock timeout accessor.

``_max_job_wall_clock_seconds`` must read ``SYNC_MAX_JOB_WALL_CLOCK_SECONDS``
at *call* time, not at import time.  This test proves that by setting the env
var after the module is already imported and asserting the accessor reflects
the new value — something a frozen module-level constant cannot do.
"""
# pylint: disable=protected-access  # tests intentionally access module-private helpers

from __future__ import annotations

import os

import open_webui.services.sync.base_worker as bw


def test_default_is_1800():
    """Accessor returns 1800 when the env var is absent."""
    env_backup = os.environ.pop('SYNC_MAX_JOB_WALL_CLOCK_SECONDS', None)
    try:
        assert bw._max_job_wall_clock_seconds() == 1800
    finally:
        if env_backup is not None:
            os.environ['SYNC_MAX_JOB_WALL_CLOCK_SECONDS'] = env_backup


def test_runtime_env_override(monkeypatch):
    """Setting the env var at runtime — *after* import — is reflected immediately.

    A module-level constant (read once at import) would still return 1800 here.
    The accessor must return the new value to prove runtime-read behaviour.
    """
    monkeypatch.setenv('SYNC_MAX_JOB_WALL_CLOCK_SECONDS', '7200')
    assert bw._max_job_wall_clock_seconds() == 7200


def test_runtime_env_override_different_value(monkeypatch):
    """Accessor picks up any integer value set at runtime."""
    monkeypatch.setenv('SYNC_MAX_JOB_WALL_CLOCK_SECONDS', '3600')
    assert bw._max_job_wall_clock_seconds() == 3600


def test_module_has_no_bare_constant():
    """The frozen module-level constant ``MAX_JOB_WALL_CLOCK_SECONDS`` must not exist.

    Presence of the bare name would mean import-time snapshot is still active.
    """
    assert not hasattr(bw, 'MAX_JOB_WALL_CLOCK_SECONDS'), (
        'MAX_JOB_WALL_CLOCK_SECONDS still exists as a module-level constant — '
        'it must be removed and replaced by the _max_job_wall_clock_seconds() accessor.'
    )
