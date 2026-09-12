"""Fresh interpreter checks for both knowledge singleton import orders."""

import os
import subprocess
import sys
from pathlib import Path


def assert_binding(tmp_path, *, config_first):
    backend = Path(__file__).resolve().parents[3]
    environment = {
        'PATH': os.defpath,
        'PYTHONPATH': str(backend),
        'WEBUI_SECRET_KEY': 't',
        'DATABASE_URL': f'sqlite:///{tmp_path}/imports.db',
        'STATIC_DIR': str(tmp_path / 'static'),
        'DATA_DIR': str(tmp_path / 'data'),
        'VECTOR_DB': 'weaviate',
    }
    for name in ('TYPE', 'USER', 'PASSWORD', 'HOST', 'PORT', 'NAME'):
        environment[f'DATABASE_{name}'] = ''
    code = 'import open_webui.config; ' if config_first else ''
    code += (
        'import open_webui.models.knowledge as k; '
        'from open_webui.soev.knowledge_store import SoevKnowledgeTable; '
        'assert type(k.Knowledges) is SoevKnowledgeTable; '
        'assert not isinstance(k.Knowledges, k.KnowledgeTable); '
        'print(type(k.Knowledges).__name__)'
    )
    result = subprocess.run(
        [sys.executable, '-c', code], cwd=backend, env=environment, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == 'SoevKnowledgeTable'


def test_importing_the_knowledge_model_first_binds_the_soev_store(tmp_path):
    """Model-first initialization binds the store without a circular import or runtime configuration lookup."""
    assert_binding(tmp_path, config_first=False)


def test_importing_config_first_binds_the_soev_knowledge_store(tmp_path):
    """Config-first initialization binds the same store after upstream migrations initialize their models."""
    assert_binding(tmp_path, config_first=True)
