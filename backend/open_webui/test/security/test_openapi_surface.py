"""The committed attack surface must come directly from the configured app."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
OUTPUT_PATH = REPO / 'security/openapi.json'
METHODS = {'get', 'put', 'post', 'delete', 'options', 'head', 'patch', 'trace'}


def operations(spec):
    return {
        f'{method.upper()} {path}': operation
        for path, item in spec['paths'].items()
        for method, operation in item.items()
        if method in METHODS
    }


def drift_message(committed, fresh):
    before, after = operations(committed), operations(fresh)
    lines = ['security/openapi.json is stale; run python scripts/security/export_openapi.py.']
    for label, ids in (
        ('Added operations', after.keys() - before.keys()),
        ('Removed operations', before.keys() - after.keys()),
        ('Changed operations', {key for key in before.keys() & after.keys() if before[key] != after[key]}),
    ):
        lines.append(f'{label}:')
        lines.extend(f'  {key}' for key in sorted(ids))
        if not ids:
            lines.append('  (none)')
    for section in sorted(committed.keys() | fresh.keys()):
        if section != 'paths' and committed.get(section) != fresh.get(section):
            lines.append(f'Changed document section: {section}')
    return '\n'.join(lines)


@pytest.fixture(scope='module')
def exporter():
    spec = importlib.util.spec_from_file_location('export_openapi', REPO / 'scripts/security/export_openapi.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_spec_is_the_same_surface_the_gate_measures(request):
    assert OUTPUT_PATH.exists(), (
        'security/openapi.json is missing; run python scripts/security/export_openapi.py '
        '(see its module docstring for the offline environment).'
    )
    exporter = request.getfixturevalue('exporter')
    committed = json.loads(OUTPUT_PATH.read_text(encoding='utf-8'))
    fresh = exporter.build()
    assert isinstance(fresh.get('paths'), dict) and operations(fresh), 'Expected a nonempty OpenAPI surface'
    assert committed == fresh, drift_message(committed, fresh)


def test_export_preserves_static_assets(exporter, tmp_path):
    exporter.export(tmp_path / 'openapi.json')
    status = subprocess.run(
        ['git', 'status', '--porcelain', '--', 'backend/open_webui/static/'],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == '', f'Export changed static assets:\n{status.stdout}'
