"""Source guard for the coordinated subject-authority removal."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
FORBIDDEN = ('x-soev-' + 'subject', 'background' + 'soevclient')
SOURCE_SUFFIXES = {'.py', '.ts', '.js', '.svelte', '.json', '.html', '.css', '.mjs', '.cjs', '.tsx', '.jsx'}


def test_backend_and_frontend_sources_have_no_legacy_subject_authority():
    violations = []
    for root in (REPO / 'backend', REPO / 'src'):
        for path in root.rglob('*'):
            if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
                continue
            source = path.read_text().casefold()
            for forbidden in FORBIDDEN:
                if forbidden in source:
                    violations.append(f'{path.relative_to(REPO)}: {forbidden}')
    assert not violations, '\n'.join(violations)
