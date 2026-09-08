import json
from unittest.mock import Mock

import pytest
import requests

from . import configuration, plane, seeds

KEY = 'rag.file.max_size'
PAYLOAD = '![x](file:///etc/passwd#gdsprobe7f3a)'
ROUTE = 'POST /api/v1/retrieval/config/update'


class ConfigServer:
    def __init__(self, value):
        self.config = {KEY: value, 'unrelated': 'preserved'}
        self.calls = []
        self.fail_after_write = False
        self.ignore_restore = False

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        body = self.config.copy()
        if path == '/api/v1/configs/import':
            if not self.ignore_restore:
                self.config.update(kwargs['json']['config'])
        elif path == ROUTE.split(' ', 1)[1]:
            if 'FILE_MAX_SIZE' in kwargs.get('json', {}):
                self.config[KEY] = kwargs['json']['FILE_MAX_SIZE']
            if self.fail_after_write:
                raise requests.ConnectionError('Disconnected after committing config')
        elif path == '/upload':
            assert self.config[KEY] is None or int(self.config[KEY]) >= 1
            body = {'id': 'real-file'}
        else:
            assert (method, path) == ('GET', '/api/v1/configs/export')
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(body).encode()
        response._content_consumed = True
        return response


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(plane, '_PASSES', {})
    monkeypatch.setenv('ROUTE_HITS_PATH', str(tmp_path / 'hits.json'))
    monkeypatch.setattr(requests.Session, 'request', lambda *a, **kw: pytest.fail('Offline test sent HTTP'))


@pytest.mark.parametrize('value', [None, 25, '25'])
def test_valid_existing_limit_is_preserved_without_writes(value):
    server = ConfigServer(value)
    assert configuration.recover_numeric_configuration(server, seeds.SURFACE['config_recovery']) == {KEY: value}
    assert len(server.calls) == 1


@pytest.mark.parametrize('value', [PAYLOAD, '12.5', {}, True])
def test_previous_poison_is_repaired_before_file_fixture(value):
    server = ConfigServer(value)
    surface = {
        'config_recovery': seeds.SURFACE['config_recovery'],
        'parameter': [{'key': 'file', 'name': 'id', 'prefix': '/files/{id}', 'via': 'POST /upload', 'extract': 'id'}],
    }
    result = seeds.resolve_parameters(server, surface=surface, spec={'paths': {'/files/{id}': {}}})
    assert result == {('/files/{id}', 'id'): 'real-file'}
    assert server.config == {KEY: 10, 'unrelated': 'preserved'}
    assert server.calls[-1][:2] == ('POST', '/upload')


@pytest.mark.parametrize('value', [None, 25, '25'])
def test_corpus_write_is_driven_recorded_restored_and_rerunnable(value):
    server = ConfigServer(value)
    spec = {**seeds.SPEC, 'paths': {ROUTE.split(' ', 1)[1]: seeds.SPEC['paths'][ROUTE.split(' ', 1)[1]]}}
    for _ in range(2):
        tally = plane.seed_every_writable_field(server, {}, spec=spec, payloads=[PAYLOAD], full=False)
        assert tally.expected == tally.statuses.keys() == tally.entered.keys() == {ROUTE}
        assert not tally.skipped
        assert tally.config_findings
        assert all(f['observed'] == PAYLOAD and f['restored'] == value for f in tally.config_findings)
        writes = [kwargs['json'] for method, path, kwargs in server.calls if f'{method} {path}' == ROUTE]
        fields = plane.writable_string_fields(spec)[ROUTE]
        assert any(body.get('FILE_MAX_SIZE') == PAYLOAD for body in writes)
        assert len(writes) >= len(fields) + 1
        assert server.config == {KEY: value, 'unrelated': 'preserved'}
        artifact = json.loads(plane.hits_path().read_text())
        assert artifact['hits'] == [ROUTE]  # Recovery requests cannot inflate coverage.
        assert artifact['passes']['seeding']['config_findings'] == tally.config_findings


def test_restore_runs_after_a_write_whose_response_is_lost():
    server = ConfigServer(25)
    server.fail_after_write = True
    with pytest.raises(requests.ConnectionError, match='Disconnected after committing config'):
        plane._drive_one(server, ROUTE, ROUTE.split(' ', 1)[1], 'seeding', json={'FILE_MAX_SIZE': PAYLOAD})
    assert server.config[KEY] == 25
    assert plane._PASSES['seeding'].config_findings[0]['observed'] == PAYLOAD
    assert not plane._PASSES['seeding'].statuses


def test_failed_restore_is_loud_and_poison_evidence_is_retained():
    server = ConfigServer(25)
    server.ignore_restore = True
    with pytest.raises(RuntimeError, match='did not persist'):
        plane._drive_one(server, ROUTE, ROUTE.split(' ', 1)[1], 'seeding', json={'FILE_MAX_SIZE': PAYLOAD})
    assert plane._PASSES['seeding'].config_findings[0]['observed'] == PAYLOAD


def test_recovery_rules_do_not_remove_routes_or_fields():
    assert len(plane.operations()) == 631
    fields = plane.writable_string_fields(seeds.SPEC)
    assert (len(fields), sum(map(len, fields.values()))) == (240, 2591)
    for rule in seeds.SURFACE['config_recovery']:
        for route in rule['routes']:
            assert route in plane.operations() and route in seeds.ordinary_targets()
    assert 'FILE_MAX_SIZE' in fields[ROUTE]


def test_malformed_export_fails_before_any_attack():
    server = Mock()
    server.request.return_value = ConfigServer(25).request('GET', '/api/v1/configs/export')
    server.request.return_value._content = b'[]'
    with pytest.raises(RuntimeError, match='Configuration recovery GET'):
        configuration.recover_numeric_configuration(server, seeds.SURFACE['config_recovery'])
    server.request.assert_called_once_with('GET', '/api/v1/configs/export')
