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


def _owui_checkout_root() -> Path:
    """Return the root of the open-webui git checkout (not the worktree).

    When running inside a git worktree the checkout root is encoded in the
    ``.git`` file as ``gitdir: <common>``; for a plain checkout the root is
    just the directory that contains the ``.git`` directory.  Walking up from
    this file and resolving through the worktree pointer gives us the actual
    checkout root regardless of which worktree the tests are run from.
    """
    # This file lives at backend/open_webui/test/util/ — parents[4] is the
    # worktree (or checkout) root in all layouts.
    candidate = Path(__file__).resolve().parents[4]
    git_path = candidate / '.git'
    if git_path.is_file():
        # Inside a worktree: .git is a file like
        # "gitdir: /path/to/main/.git/worktrees/<name>"
        # Walk up from git_common until we find the directory whose parent
        # is the checkout root (i.e. the first ancestor that isn't called
        # 'worktrees' and whose name ends with '.git' or is '.git').
        content = git_path.read_text().strip()
        if content.startswith('gitdir:'):
            git_common = Path(content.split(':', 1)[1].strip()).resolve()
            # git_common is <checkout>/.git/worktrees/<name>
            # parents[0] = <checkout>/.git/worktrees
            # parents[1] = <checkout>/.git
            # parents[2] = <checkout>   ← what we want
            return git_common.parents[2]
    # Plain checkout: candidate itself is the root.
    return candidate


def _resolve_genai_utils_client() -> Path:
    """Find the genai-utils ingest_client.py from common locations.

    Tried in order:
    1. $GENAI_UTILS_INGEST_CLIENT env override (CI / non-standard layouts)
    2. Sibling `genai-utils` checkout at the same monorepo level as the
       open-webui checkout (the standard soev layout).
    3. Sibling worktree path (genai-utils/.worktrees/feat/bim-agent).
    """
    import os

    override = os.environ.get('GENAI_UTILS_INGEST_CLIENT')
    if override:
        return Path(override)

    # In the soev monorepo, genai-utils sits next to open-webui.
    monorepo_root = _owui_checkout_root().parent
    candidates = [
        # Worktree-of-genai-utils variant (checked first — carries the
        # IngestAttachment dataclass that is being co-developed here)
        monorepo_root
        / 'genai-utils'
        / '.worktrees'
        / 'feat'
        / 'bim-agent'
        / 'api'
        / 'gateway'
        / 'loader_worker'
        / 'ingest_client.py',
        # Standard layout: sibling genai-utils checkout (post-merge)
        monorepo_root / 'genai-utils' / 'api' / 'gateway' / 'loader_worker' / 'ingest_client.py',
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[1]  # return the standard location for the SKIP message


GENAI_UTILS_CLIENT = _resolve_genai_utils_client()


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
