"""Export the configured FastAPI app's OpenAPI document without editing it.

Run with the real backend dependencies and this checkout on PYTHONPATH:

    PYTHONPATH="$PWD/backend" python scripts/security/export_openapi.py

Importing open_webui.main runs database migrations and constructs the vector
client; it requires reachable services. For a disposable offline export, run:

    export_dir=$(mktemp -d)
    DATABASE_TYPE= DATABASE_HOST= DATABASE_PORT= DATABASE_NAME= \
    DATABASE_USER= DATABASE_PASSWORD= \
    DATABASE_URL="sqlite:///$export_dir/webui.db" DATA_DIR="$export_dir" \
    VECTOR_DB=chroma CHROMA_HTTP_HOST= OFFLINE_MODE=true \
    WEBUI_SECRET_KEY=disposable-offline-openapi-export-key \
    PYTHONPATH="$PWD/backend" python scripts/security/export_openapi.py

All six DATABASE_* parts must be explicitly empty: the repository .env can
otherwise rebuild DATABASE_URL and silently override the SQLite URL. DATA_DIR
keeps Chroma and other import-time data disposable; CHROMA_HTTP_HOST= selects
embedded Chroma. The disposable secret satisfies direct app import's auth
requirement; do not use it for a running deployment. Remove export_dir after
the process exits. Use the same
environment with python -m pytest -c backend/open_webui/test/security/pytest.ini
backend/open_webui/test/security/test_openapi_surface.py to check drift.

Route flags (including FEATURE_SKILL_FILES, ENABLE_ADMIN_ANALYTICS and
ENABLE_SCIM) are left as configured. Generate and test in the environment the
gate measures. No lifespan/startup hooks are run. STATIC_DIR is redirected to
a temporary directory because config.py deletes static assets during import.
"""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_PATH = REPO_ROOT / 'security/openapi.json'


_HTTP_METHODS = ('get', 'put', 'post', 'delete', 'options', 'head', 'patch', 'trace')


def _canonical_operation_ids(spec: dict) -> dict:
    """Give every operation a stable, unique operationId.

    FastAPI derives one operationId per *route*, from ``route.methods`` -- a
    set. A route registered with several methods therefore borrows whichever
    method name the set happened to yield first, which varies between
    processes under hash randomisation. Two consequences, both fatal here:
    the document is not byte-stable, so a drift gate reports staleness on an
    unchanged application; and the shared id is duplicated across operations,
    which the OpenAPI specification forbids. Re-suffix each operation with its
    own method.
    """
    for item in spec.get('paths', {}).values():
        for method, operation in item.items():
            if method not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            operation_id = operation.get('operationId')
            if not operation_id:
                continue
            for candidate in _HTTP_METHODS:
                if operation_id.endswith(f'_{candidate}'):
                    operation_id = operation_id[: -len(candidate) - 1]
                    break
            operation['operationId'] = f'{operation_id}_{method}'
    return spec


def build() -> dict:
    previous_static = os.environ.get('STATIC_DIR')
    with TemporaryDirectory(prefix='owui-openapi-static-') as static_dir:
        os.environ['STATIC_DIR'] = static_dir
        try:
            from open_webui.main import app

            # FastAPI caches the schema; clear it so in-process route changes
            # cannot silently pass the drift gate using a previous document.
            app.openapi_schema = None
            return _canonical_operation_ids(app.openapi())
        finally:
            if previous_static is None:
                os.environ.pop('STATIC_DIR', None)
            else:
                os.environ['STATIC_DIR'] = previous_static


def export(output_path: Path = OUTPUT_PATH) -> dict:
    spec = build()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(spec, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return spec


def main() -> None:
    try:
        spec = export()
    except Exception as exc:
        raise SystemExit(
            f'OpenAPI export failed ({type(exc).__name__}): {exc}\n'
            'App import requires a reachable database and vector service. For an offline export, '
            'set DATABASE_TYPE= DATABASE_HOST= DATABASE_PORT= DATABASE_NAME= '
            'DATABASE_USER= DATABASE_PASSWORD= and DATABASE_URL=sqlite:////tmp/<fresh>.db, '
            'VECTOR_DB=chroma CHROMA_HTTP_HOST= DATA_DIR=<temporary-directory> OFFLINE_MODE=true. '
            'See the exporter module docstring for the complete command. '
            'Missing Python dependencies must be supplied by the backend environment.'
        ) from None
    methods = {'get', 'put', 'post', 'delete', 'options', 'head', 'patch', 'trace'}
    operations = [
        operation for item in spec['paths'].values() for method, operation in item.items() if method in methods
    ]
    print(
        f'Wrote {OUTPUT_PATH}: {len(operations)} operations, '
        f'{sum("requestBody" in op for op in operations)} with requestBody, '
        f'{sum(bool(op.get("parameters")) for op in operations)} with parameters, '
        f'{len(spec["paths"])} paths'
    )


if __name__ == '__main__':
    main()
