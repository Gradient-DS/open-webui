"""The assertion that makes the plane a gate rather than a report.

Every operation in the committed OpenAPI document must have been entered by some
pass, or be waived in security/route-coverage.toml with a written reason. A route
nobody drives is a route no security test covers, and until this file existed the
plane measured that and printed it.

Two properties matter more than the assertion itself:

- The hits artefact is read at RUN time, never at collection time. A `skipif` on
  "the artefact does not exist" is evaluated before the tests that produce it have
  run, so the gate would skip silently in CI and measure nothing.
- With ATTACK_BASE_URL set, a missing artefact FAILS. The stack was up, the plane
  was supposed to run, and no evidence is the one result that must never pass.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

import pytest

from .attack import plane
from .attack.hits_path import hits_path

SECURITY = Path(__file__).resolve().parents[3].parent / 'security'
WAIVERS = SECURITY / 'route-coverage.toml'
SPEC = SECURITY / 'openapi.json'
METHODS = {'get', 'put', 'post', 'delete', 'patch', 'head', 'options', 'trace'}


def operations() -> set[str]:
    spec = json.loads(SPEC.read_text())
    return {f'{method.upper()} {path}' for path, item in spec['paths'].items() for method in item if method in METHODS}


def waivers() -> dict[str, str]:
    if not WAIVERS.exists():
        return {}
    parsed = tomllib.loads(WAIVERS.read_text())
    return {entry['id']: entry['reason'] for entry in parsed.get('waived', [])}


def evidence():
    """The artefact, read now rather than at import. See the module docstring."""
    path = hits_path()
    if path.exists():
        data = json.loads(path.read_text())
        # A leftover from an earlier run is not evidence about this one. The
        # plane writes the artefact in the same invocation as this gate, so a
        # different id means the plane did not run here.
        if data.get('run_id') == plane.RUN_ID:
            return data
    if os.getenv('ATTACK_BASE_URL'):
        pytest.fail(
            f'The stack was configured at {os.getenv("ATTACK_BASE_URL")} but this run wrote no '
            f'route-hit artefact to {path}. The plane did not run, did not run before this gate, '
            'or the file is left over from another run. No evidence is not the same as no findings.'
        )
    return None


def test_every_route_is_driven_or_waived():
    hits = evidence()
    if hits is None:
        pytest.skip('offline: no stack configured, so there is no run to measure')
    uncovered = operations() - set(hits['hits']) - waivers().keys()
    detail = '\n'.join(f'  {route}' for route in sorted(uncovered))
    assert not uncovered, (
        f'{len(uncovered)} route(s) no security test drives, and no waiver explains:\n{detail}\n'
        f'Drive them, or waive each in {WAIVERS.name} with a reason from observed behaviour.'
    )


def test_every_waiver_carries_a_reason():
    empty = sorted(route for route, reason in waivers().items() if not reason.strip())
    assert not empty, f'waivers with no reason: {empty}'


def test_no_waiver_names_a_route_that_no_longer_exists():
    # A waiver outliving its route is a hole that reads as a decision.
    stale = sorted(waivers().keys() - operations())
    assert not stale, f'waivers for routes not in the spec: {stale}'


def test_no_waiver_covers_a_route_the_plane_actually_reached():
    # Waiving something already driven hides it from the gate for no reason, and
    # would keep hiding it if driving it ever stopped working.
    hits = evidence()
    if hits is None:
        pytest.skip('offline: no stack configured, so there is no run to measure')
    unnecessary = sorted(waivers().keys() & set(hits['hits']))
    assert not unnecessary, f'waived but actually driven, so the waiver should go: {unnecessary}'


def write_artefact(path, hits, *, run_id):
    path.write_text(json.dumps({'run_id': run_id, 'hits': sorted(hits), 'passes': {}}))


def test_a_missing_artefact_fails_when_the_stack_was_configured(monkeypatch, tmp_path):
    # The failure this gate exists to prevent: the stack was up, the plane was
    # meant to drive it, and nothing came out. Silence must not read as success.
    monkeypatch.setenv('ROUTE_HITS_PATH', str(tmp_path / 'absent.json'))
    monkeypatch.setenv('ATTACK_BASE_URL', 'http://localhost:8080')
    with pytest.raises(pytest.fail.Exception, match='No evidence is not the same as no findings'):
        evidence()


def test_an_artefact_from_another_run_is_not_evidence(monkeypatch, tmp_path):
    path = tmp_path / 'hits.json'
    write_artefact(path, operations(), run_id='some-earlier-run')
    monkeypatch.setenv('ROUTE_HITS_PATH', str(path))
    monkeypatch.setenv('ATTACK_BASE_URL', 'http://localhost:8080')
    # Full coverage in the file, but not from this run: it proves nothing here.
    with pytest.raises(pytest.fail.Exception, match='left over from another run'):
        evidence()


def test_the_gate_is_never_silently_skipped_offline(monkeypatch, tmp_path):
    # Offline there is genuinely no run to measure, so skipping is right -- but
    # only because ATTACK_BASE_URL is unset. Collection-time skipif would have
    # skipped in CI too, before the plane had written anything.
    monkeypatch.setenv('ROUTE_HITS_PATH', str(tmp_path / 'absent.json'))
    monkeypatch.delenv('ATTACK_BASE_URL', raising=False)
    assert evidence() is None


def test_an_uncovered_route_fails_the_gate(monkeypatch, tmp_path):
    path = tmp_path / 'hits.json'
    covered = sorted(operations())
    write_artefact(path, covered[1:], run_id=plane.RUN_ID)
    monkeypatch.setenv('ROUTE_HITS_PATH', str(path))
    monkeypatch.setattr('security.test_route_coverage.waivers', dict)
    with pytest.raises(AssertionError, match=covered[0].replace('{', '.').replace('}', '.')):
        test_every_route_is_driven_or_waived()


def test_a_waiver_covers_an_uncovered_route(monkeypatch, tmp_path):
    # The negative control: without it, an always-passing gate would look fine.
    path = tmp_path / 'hits.json'
    covered = sorted(operations())
    write_artefact(path, covered[1:], run_id=plane.RUN_ID)
    monkeypatch.setenv('ROUTE_HITS_PATH', str(path))
    monkeypatch.setattr('security.test_route_coverage.waivers', lambda: {covered[0]: 'a written reason'})
    test_every_route_is_driven_or_waived()
