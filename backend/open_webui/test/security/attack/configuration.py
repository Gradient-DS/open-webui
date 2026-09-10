"""Snapshot all exported configuration and contain corpus writes through HTTP."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import requests
from hostile_corpus import FETCH_URLS, REFLECTION_PROBE, fetch_payloads

from .client import json_body

EXPORT = '/api/v1/configs/export'
IMPORT = '/api/v1/configs/import'
# The guard runs twice around every write the plane drives, and exports several
# hundred keys each time. It is instrumentation, not a measurement, so it gets
# far longer than a driven route: a timeout here loses evidence rather than
# producing any.
CONFIG_TIMEOUT_SECONDS = float(os.getenv('ATTACK_CONFIG_TIMEOUT_SECONDS', '300'))


def _request(admin, method, path, **kwargs):
    kwargs.setdefault('timeout', CONFIG_TIMEOUT_SECONDS)
    response = admin.request(method, path, **kwargs)
    try:
        body = json_body(response)
        if not 200 <= response.status_code < 300 or not isinstance(body, dict):
            raise RuntimeError(f'Configuration recovery {method} {path} failed (HTTP {response.status_code})')
        return body
    finally:
        response.close()


def snapshot(admin):
    return _request(admin, 'GET', EXPORT)


def _different(left, right):
    # JSON booleans and numbers must not compare equal during verification.
    return json.dumps(left, sort_keys=True) != json.dumps(right, sort_keys=True)


def restore(admin, before, *, report, route):
    after = snapshot(admin)
    changed = sorted(
        key
        for key in before.keys() | after.keys()
        if key not in before or key not in after or _different(before[key], after[key])
    )
    if not changed:
        return
    payloads = _corpus_values()
    for key in changed:
        report(
            {
                'finding': 'PLANE-001',
                'route': route,
                'key': key,
                'before_present': key in before,
                'after_present': key in after,
                'observed': after.get(key),
                'restored': before.get(key),
                'corpus': _contaminated(after.get(key), payloads),
            }
        )
    updates = {key: before[key] for key in changed if key in before}
    if updates:
        _request(admin, 'POST', IMPORT, json={'config': updates})
    if _different(snapshot(admin), before):
        raise RuntimeError(
            'Configuration recovery did not persist the exact snapshot; '
            'export/import cannot remove newly introduced keys. Retaining the recovery journal.'
        )


def _journal(admin):
    root = Path(os.getenv('ATTACK_CONFIG_STATE_DIR', Path(__file__).resolve().parents[5] / '.cache/attack'))
    key = hashlib.sha256(admin.base_url.encode()).hexdigest()[:16]
    return root / f'config-{key}.json'


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.{uuid4().hex}.tmp')
    try:
        with open(temporary, 'x', opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
            json.dump(data, stream)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _corpus_values():
    values = {p for p in fetch_payloads() if p}
    for url in FETCH_URLS:
        parts = urlsplit(url)
        values.add(urlunsplit(parts._replace(fragment='')))
        if parts.netloc:
            # Site-URL writers can remove the path carrying the reflection probe.
            values.add(urlunsplit((parts.scheme, parts.netloc, '', '', '')))
    return values


def _contaminated(value, payloads):
    if isinstance(value, str):
        return REFLECTION_PROBE in value or value in payloads
    if isinstance(value, dict):
        return any(_contaminated(k, payloads) or _contaminated(v, payloads) for k, v in value.items())
    if isinstance(value, list):
        return any(_contaminated(v, payloads) for v in value)
    return False


def recover_configuration(admin, *, report=print):
    journal = _journal(admin)
    if journal.exists():
        restore(admin, json.loads(journal.read_text()), report=report, route='interrupted pass')
        journal.unlink()
    current = snapshot(admin)
    payloads = _corpus_values()
    poisoned = {key for key, value in current.items() if _contaminated(value, payloads)}
    if not poisoned:
        return
    baseline_path = os.getenv('ATTACK_CONFIG_BASELINE')
    if not baseline_path:
        raise RuntimeError('Previous corpus poison requires ATTACK_CONFIG_BASELINE from the CI stack environment')
    baseline = json.loads(Path(baseline_path).read_text())
    if not isinstance(baseline, dict) or poisoned - baseline.keys():
        raise RuntimeError(f'Clean configuration baseline is missing poisoned keys: {sorted(poisoned)}')
    if any(_contaminated(baseline[key], payloads) for key in poisoned):
        raise RuntimeError('Configuration baseline itself contains corpus poison')
    before = {**current, **{key: baseline[key] for key in poisoned}}
    _save(journal, before)
    restore(admin, before, report=report, route='previous run')
    journal.unlink()


@contextmanager
def preserve_configuration(admin, *, report, route, durable=False, unverified=None):
    """Snapshot configuration around a route, and say so when it could not.

    A timeout here is lost evidence, not a measurement: we cannot tell whether
    the route poisoned configuration. Ending the pass would lose every route
    after it as well, so the guard steps aside and records that this route went
    unverified. `unverified` is what stops the pass claiming otherwise.
    """
    try:
        before = snapshot(admin)
    except requests.exceptions.Timeout as error:
        _unverified(unverified, route, f'configuration snapshot timed out before the route: {error}')
        yield
        return
    journal = _journal(admin) if durable else None
    if journal is not None:
        if journal.exists():
            raise RuntimeError('Recover the previous configuration journal before starting another pass')
        _save(journal, before)
    try:
        yield
    finally:
        try:
            restore(admin, before, report=report, route=route)
        except requests.exceptions.Timeout as error:
            _unverified(unverified, route, f'configuration snapshot timed out after the route: {error}')
        else:
            # Only a completed restore may drop the journal. A restore that
            # failed -- loudly, or by timing out before it could verify -- is
            # exactly when the next setup needs it to recover from.
            if journal is not None:
                journal.unlink()


def _unverified(unverified, route, reason):
    if unverified is None:
        raise RuntimeError(f'Configuration guard lost its snapshot with nowhere to record it: {route}: {reason}')
    unverified(reason)
