"""Recover numeric configuration through HTTP while retaining poisoning evidence."""

from contextlib import contextmanager

from .client import json_body


def _request(admin, method, path, **kwargs):
    response = admin.request(method, path, **kwargs)
    try:
        body = json_body(response)
        if not 200 <= response.status_code < 300 or not isinstance(body, dict):
            raise RuntimeError(f'Configuration recovery {method} {path} failed (HTTP {response.status_code})')
        return body
    finally:
        response.close()


def _numeric(value):
    if value is None:
        return True  # The application uses None for an unlimited upload size.
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return False
    try:
        int(value)
        return True
    except ValueError:
        return False


def _restore(admin, updates):
    if not updates:
        return
    _request(admin, 'POST', '/api/v1/configs/import', json={'config': updates})
    persisted = _request(admin, 'GET', '/api/v1/configs/export')
    if any(key not in persisted or persisted[key] != value for key, value in updates.items()):
        raise RuntimeError('Configuration recovery did not persist the requested values')


def recover_numeric_configuration(admin, rules, *, report=print):
    if not rules:
        return {}
    config = _request(admin, 'GET', '/api/v1/configs/export')
    updates = {}
    for rule in rules:
        key = rule['key']
        if key not in config:
            raise RuntimeError(f'Configuration recovery cannot read {key}')
        if not _numeric(config[key]):
            updates[key] = rule['fallback']
            report({'finding': rule['finding'], 'key': key, 'observed': config[key], 'restored': rule['fallback']})
    _restore(admin, updates)
    return {rule['key']: updates.get(rule['key'], config[rule['key']]) for rule in rules}


@contextmanager
def preserve_numeric_configuration(admin, route, rules, *, report):
    affected = [rule for rule in rules if route in rule['routes']]
    before = recover_numeric_configuration(admin, affected, report=report)
    try:
        yield
    finally:
        if affected:
            after = _request(admin, 'GET', '/api/v1/configs/export')
            updates = {}
            for rule in affected:
                key = rule['key']
                if key not in after:
                    raise RuntimeError(f'Configuration recovery cannot read {key}')
                if after[key] != before[key]:
                    report(
                        {
                            'finding': rule['finding'],
                            'route': route,
                            'key': key,
                            'observed': after[key],
                            'restored': before[key],
                        }
                    )
                    updates[key] = before[key]
            _restore(admin, updates)
