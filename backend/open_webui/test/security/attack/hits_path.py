"""Shared artefact location, importable by coverage gates without the corpus."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def hits_path() -> Path:
    override = os.getenv('ROUTE_HITS_PATH')
    return Path(override) if override else Path(tempfile.gettempdir()) / 'route-hits.json'
