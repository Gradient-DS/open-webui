import ast
import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from . import client as transport
from . import configuration, plane, seeds

KEY = 'rag.file.max_size'
ENGINE = 'rag.embedding_engine'
PAYLOAD = '![x](file:///etc/passwd#gdsprobe7f3a)'
ROUTE = 'POST /api/v1/retrieval/config/update'


class ConfigServer:
    base_url = 'http://offline-config'

    def __init__(self, value=25):
        self.config = {KEY: value, ENGINE: 'openai', 'future.setting': {'urls': ['http://stub:8000']}}
        self.calls = []
        self.fail_after_write = False
        self.ignore_restore = False
        self.attack_status = 200

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        body = copy.deepcopy(self.config)
        status = 200
        if path == configuration.IMPORT:
            if not self.ignore_restore:
                self.config.update(copy.deepcopy(kwargs['json']['config']))
        elif path in {ROUTE.split(' ', 1)[1], '/future-write'}:
            if 'FILE_MAX_SIZE' in kwargs.get('json', {}):
                self.config[KEY] = kwargs['json']['FILE_MAX_SIZE']
                self.config[ENGINE] = PAYLOAD
                self.config['future.setting'] = {'urls': [PAYLOAD]}
            status = self.attack_status
            if self.fail_after_write:
                raise requests.ConnectionError('Disconnected after committing config')
        elif path == '/upload':
            assert self.config[KEY] is None or int(self.config[KEY]) >= 1
            assert self.config[ENGINE] == 'openai'
            body = {'id': 'real-file'}
        else:
            assert (method, path) == ('GET', configuration.EXPORT)
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(body).encode()
        response._content_consumed = True
        return response


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(plane, '_PASSES', {})
    monkeypatch.setenv('ROUTE_HITS_PATH', str(tmp_path / 'hits.json'))
    monkeypatch.setenv('ATTACK_CONFIG_STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.delenv('ATTACK_CONFIG_BASELINE', raising=False)
    monkeypatch.setattr(configuration, 'fetch_payloads', lambda: [PAYLOAD])
    monkeypatch.setattr(requests.Session, 'request', lambda *a, **kw: pytest.fail('Offline test sent HTTP'))


@pytest.mark.parametrize('value', [None, 25, '25'])
def test_valid_existing_configuration_is_preserved_without_writes(value):
    server = ConfigServer(value)
    configuration.recover_configuration(server)
    assert len(server.calls) == 1


def test_previous_poison_in_any_key_is_repaired_before_file_and_integration_fixtures(monkeypatch, tmp_path):
    server = ConfigServer()
    baseline = copy.deepcopy(server.config)
    path = tmp_path / 'baseline.json'
    path.write_text(json.dumps(baseline))
    monkeypatch.setenv('ATTACK_CONFIG_BASELINE', str(path))
    server.config = {key: PAYLOAD for key in server.config}
    server.config['future.setting'] = {'urls': ['http://egress-canary.invalid']}
    server.config['valid.setting'] = 42
    surface = {
        'preserve_configuration': True,
        'parameter': [{'key': 'file', 'name': 'id', 'prefix': '/files/{id}', 'via': 'POST /upload', 'extract': 'id'}],
    }
    result = seeds.resolve_parameters(server, surface=surface, spec={'paths': {'/files/{id}': {}}})
    assert result == {('/files/{id}', 'id'): 'real-file'}
    assert server.config == {**baseline, 'valid.setting': 42}
    assert server.calls[-1][:2] == ('POST', '/upload')


@pytest.mark.parametrize('value', [None, 25, '25'])
@pytest.mark.parametrize('status', [200, 500])
def test_all_changed_keys_are_driven_recorded_restored_and_rerunnable(value, status):
    server = ConfigServer(value)
    server.attack_status = status
    before = copy.deepcopy(server.config)
    spec = {**seeds.SPEC, 'paths': {ROUTE.split(' ', 1)[1]: seeds.SPEC['paths'][ROUTE.split(' ', 1)[1]]}}
    for _ in range(2):
        tally = plane.seed_every_writable_field(server, {}, spec=spec, payloads=[PAYLOAD], full=False)
        assert tally.expected == tally.statuses.keys() == tally.entered.keys() == {ROUTE}
        assert not tally.skipped
        assert {f['key'] for f in tally.config_findings} == set(before)
        writes = [kwargs['json'] for method, path, kwargs in server.calls if f'{method} {path}' == ROUTE]
        assert any(body.get('FILE_MAX_SIZE') == PAYLOAD for body in writes)
        assert len(writes) >= len(plane.writable_string_fields(spec)[ROUTE]) + 1
        assert server.config == before
        artifact = json.loads(plane.hits_path().read_text())
        assert artifact['hits'] == [ROUTE]
        assert artifact['passes']['seeding']['config_findings'] == tally.config_findings
        assert artifact['passes']['seeding']['config_keys'] == sorted(before)
        assert artifact['passes']['seeding']['corpus_config_keys'] == sorted(before)
        assert artifact['passes']['seeding']['config_restore_verified'] is True
        assert not configuration._journal(server).exists()


def test_restore_runs_after_a_write_whose_response_is_lost():
    server = ConfigServer()
    before = copy.deepcopy(server.config)
    server.fail_after_write = True
    with pytest.raises(requests.ConnectionError, match='Disconnected after committing config'):
        plane._drive_one(server, ROUTE, ROUTE.split(' ', 1)[1], 'seeding', json={'FILE_MAX_SIZE': PAYLOAD})
    assert server.config == before
    assert {f['key'] for f in plane._PASSES['seeding'].config_findings} == set(before)
    assert not plane._PASSES['seeding'].statuses


def test_failed_restore_is_loud_evidence_and_journal_survive_then_next_setup_recovers():
    server = ConfigServer()
    before = copy.deepcopy(server.config)
    server.ignore_restore = True
    spec = {**seeds.SPEC, 'paths': {ROUTE.split(' ', 1)[1]: seeds.SPEC['paths'][ROUTE.split(' ', 1)[1]]}}
    with pytest.raises(RuntimeError, match='did not persist'):
        plane.seed_every_writable_field(server, {}, spec=spec, payloads=[PAYLOAD])
    assert plane._PASSES['seeding'].config_findings[0]['observed']
    assert configuration._journal(server).exists()
    assert configuration._journal(server).stat().st_mode & 0o777 == 0o600
    assert json.loads(plane.hits_path().read_text())['passes']['seeding']['config_keys']
    assert json.loads(plane.hits_path().read_text())['passes']['seeding']['config_restore_verified'] is False
    server.ignore_restore = False
    configuration.recover_configuration(server)
    assert server.config == before
    assert not configuration._journal(server).exists()


def test_pass_boundary_recovers_changes_from_read_routes():
    server = ConfigServer()
    before = copy.deepcopy(server.config)
    unverified = []
    with configuration.preserve_configuration(
        server, report=lambda _: None, route='drive pass', unverified=unverified.append, durable=True
    ):
        server.config[ENGINE] = 'another-engine'
    assert server.config == before


def test_unknown_route_and_unknown_key_need_no_recovery_rule():
    server = ConfigServer()
    before = copy.deepcopy(server.config)
    records = []
    unverified = []
    with configuration.preserve_configuration(
        server, report=records.append, route='POST /future', unverified=unverified.append
    ):
        server.config['future.setting']['urls'].append(PAYLOAD)
        del server.config[KEY]
    assert server.config == before
    assert {f['key'] for f in records} == {KEY, 'future.setting'}
    plane._drive_one(server, 'POST /future-write', '/future-write', 'seeding', json={'FILE_MAX_SIZE': PAYLOAD})
    assert server.config == before
    assert {f['key'] for f in plane._PASSES['seeding'].config_findings} == set(before)


def test_new_keys_cannot_be_silently_left_behind_by_merge_only_import():
    server = ConfigServer()
    records = []
    unverified = []
    with pytest.raises(RuntimeError, match='cannot remove newly introduced keys'):
        with configuration.preserve_configuration(
            server, report=records.append, route='POST /future', unverified=unverified.append, durable=True
        ):
            server.config['new.key'] = PAYLOAD
    assert records[0]['before_present'] is False
    assert configuration._journal(server).exists()


def test_verification_distinguishes_boolean_from_integer():
    server = ConfigServer(1)
    records = []
    unverified = []
    with configuration.preserve_configuration(server, report=records.append, route=ROUTE, unverified=unverified.append):
        server.config[KEY] = True
    assert type(server.config[KEY]) is int
    assert records[0]['observed'] is True


@pytest.mark.parametrize('baseline', [None, {}, {ENGINE: PAYLOAD}])
def test_old_poison_without_a_clean_baseline_fails_before_seeding(monkeypatch, tmp_path, baseline):
    server = ConfigServer()
    server.config[ENGINE] = PAYLOAD
    if baseline is not None:
        path = tmp_path / 'bad-baseline.json'
        path.write_text(json.dumps(baseline))
        monkeypatch.setenv('ATTACK_CONFIG_BASELINE', str(path))
    with pytest.raises(RuntimeError, match='baseline|BASELINE'):
        configuration.recover_configuration(server)
    assert all(method == 'GET' for method, _, _ in server.calls)


def test_recovery_does_not_remove_routes_or_fields():
    assert len(plane.operations()) == 662
    fields = plane.writable_string_fields(seeds.SPEC)
    assert (len(fields), sum(map(len, fields.values()))) == (252, 2757)
    assert seeds.SURFACE['preserve_configuration'] is True
    assert 'FILE_MAX_SIZE' in fields[ROUTE]
    assert 'POST /api/v1/retrieval/embedding/update' in fields
    assert 'POST /api/v1/configs/import' in plane.operations()


def test_malformed_export_fails_before_any_attack():
    server = Mock()
    server.request.return_value = ConfigServer().request('GET', configuration.EXPORT)
    server.request.return_value._content = b'[]'
    with pytest.raises(RuntimeError, match='Configuration recovery GET'):
        configuration.snapshot(server)
    # The guard is instrumentation and gets a far longer timeout than a driven
    # route: a timeout here loses the evidence rather than producing any.
    server.request.assert_called_once_with('GET', configuration.EXPORT, timeout=configuration.CONFIG_TIMEOUT_SECONDS)
    assert configuration.CONFIG_TIMEOUT_SECONDS > transport.DEFAULT_TIMEOUT_SECONDS


def test_ingest_body_failure_is_recorded_without_hiding_handler_entry():
    route = 'POST /api/v1/integrations/ingest'
    assert plane.record(route, 200, {'created': 0, 'errors': 1}, pass_name='seeding')
    tally = plane._PASSES['seeding']
    assert tally.accepted == {route: 1}  # This counter explicitly measures HTTP 2xx only.
    assert tally.body_failures == [
        {
            'finding': 'PLANE-002',
            'route': route,
            'status': 200,
            'body': {'created': 0, 'errors': 1},
        }
    ]
    plane.flush_hits()
    assert json.loads(plane.hits_path().read_text())['passes']['seeding']['body_failures'] == tally.body_failures


def test_every_configuration_guard_says_where_a_lost_snapshot_goes():
    """A guard that loses its snapshot must have somewhere to record it.

    The keyword is required, so a new call site fails loudly rather than
    silently -- but only when that path runs, which for the live passes means
    CI. This is the same check, offline.
    """
    package = Path(__file__).resolve().parent
    missing = []
    for module in sorted(package.glob('*.py')):
        if module.name.startswith('test_'):
            continue
        for node in ast.walk(ast.parse(module.read_text())):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, 'id', None)
            if name != 'preserve_configuration':
                continue
            if not any(keyword.arg == 'unverified' for keyword in node.keywords):
                missing.append(f'{module.name}:{node.lineno}')
    assert not missing, f'configuration guards with nowhere to record a lost snapshot: {missing}'


def test_a_pass_reloads_derived_state_even_when_every_value_matches():
    # PLANE-013: value equality is not "the stack is as it was". A pass can
    # leave the application's derived state -- the loaded embedding model --
    # unusable while every exported key still compares equal, and the guard
    # then verified a stack on which the next pass's seeding could not run.
    # The import is what makes the application rebuild from configuration, so
    # a pass ends with one whether or not anything drifted.
    server = ConfigServer()
    unverified = []
    with configuration.preserve_configuration(
        server, report=lambda _: None, route='drive pass', unverified=unverified.append, durable=True
    ):
        pass
    imports = [call for call in server.calls if call[1] == configuration.IMPORT]
    assert len(imports) == 1
    assert imports[0][2]['json']['config'] == server.config
    assert unverified == []


def test_a_single_route_that_changed_nothing_still_costs_no_write():
    # The other half: the guard runs twice around every write the plane drives,
    # so an unconditional import per route would double the writes of a pass
    # that measures hundreds of them. Only the pass boundary pays for it.
    server = ConfigServer()
    with configuration.preserve_configuration(server, report=lambda _: None, route=ROUTE, unverified=lambda _: None):
        pass
    assert [call for call in server.calls if call[1] == configuration.IMPORT] == []
