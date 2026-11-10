"""Copy audited response schemas from the published soev-api OpenAPI export.

Pass services/soev_api/openapi.json from the matching platform checkout;
--check verifies the committed snapshot without writing it.
"""

import argparse
import json
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[2] / 'backend/open_webui/test/soev/soev_api_contract.json'
OPERATIONS = (
    ('/v1/products/owui/config', 'get'),
    ('/v1/offboarding', 'get'),
    ('/v1/offboarding/{job_id}/acknowledgement', 'post'),
    ('/v1/bug-reports', 'post'),
    ('/v1/threads/{thread_id}/feedback', 'post'),
    ('/v1/feedback', 'get'),
    ('/v1/feedback/summary', 'get'),
    ('/v1/threads/usage', 'get'),
)


def extract(spec: dict) -> dict:
    """Keep success responses and their transitive schema references unchanged."""
    paths = {
        path: {
            method: {
                'responses': {
                    status: response
                    for status, response in spec['paths'][path][method]['responses'].items()
                    if status.startswith('2')
                }
            }
        }
        for path, method in OPERATIONS
    }
    schemas: dict = {}

    def collect(value: object) -> None:
        if isinstance(value, dict):
            if '$ref' in value:
                prefix = '#/components/schemas/'
                reference = value['$ref']
                if not reference.startswith(prefix):
                    raise ValueError('Expected a local schema reference: ' + reference)
                name = reference.removeprefix(prefix)
                if name not in schemas:
                    schemas[name] = spec['components']['schemas'][name]
                    collect(schemas[name])
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(paths)
    return {'openapi': spec['openapi'], 'info': spec['info'], 'paths': paths, 'components': {'schemas': schemas}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('published_spec', type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    expected = json.dumps(extract(json.loads(args.published_spec.read_text())), indent=2, sort_keys=True) + '\n'
    if args.check:
        if OUTPUT.read_text() != expected:
            raise SystemExit('soev-api response contract drift')
        print('Verified eight response contracts against published OpenAPI')
        return
    OUTPUT.write_text(expected)
    print(f'Wrote {OUTPUT}')


if __name__ == '__main__':
    main()
