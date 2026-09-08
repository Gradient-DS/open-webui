"""Offline guards always run; only real application round trips need the stack."""

import ast
import io
import json
import os
from http.client import RemoteDisconnected
from pathlib import Path

import pytest
import requests
from urllib3.connectionpool import HTTPConnectionPool
from urllib3.exceptions import ProtocolError, ReadTimeoutError
from urllib3.response import HTTPResponse

from .client import (
    _NOT_ENTERED_MARKERS,
    AttackClient,
    AuthenticationError,
    ConnectionRetry,
    entered_the_handler,
    login,
    session_body,
)

needs_stack = pytest.mark.skipif(
    not os.getenv('ATTACK_BASE_URL'),
    reason='drives the running CI stack; set ATTACK_BASE_URL=http://localhost:8080',
)
SOURCE = Path(__file__).resolve().parents[3]


def response(status, body):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(body).encode()
    return result


@pytest.mark.parametrize('marker', _NOT_ENTERED_MARKERS, ids=lambda m: f'{m.path}:{m.line}')
def test_each_marker_still_lives_in_the_function_that_emits_it(marker):
    tree = ast.parse((SOURCE / marker.path).read_text())
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == marker.function
    )
    exception = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == 'HTTPException'
        and any(k.arg == 'detail' and k.value.lineno == marker.line for k in node.keywords)
    )
    status = next((k.value for k in exception.keywords if k.arg == 'status_code'), None)
    if status is None:
        status = exception.args[0]
    code = status.value if isinstance(status, ast.Constant) else int(status.attr.split('_')[1])
    assert code in marker.statuses
    detail = next(
        keyword.value
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'HTTPException'
        for keyword in node.keywords
        if keyword.arg == 'detail' and keyword.value.lineno == marker.line
    )
    if marker.constant:
        assert isinstance(detail, ast.Attribute)
        assert isinstance(detail.value, ast.Name) and detail.value.id == 'ERROR_MESSAGES'
        assert detail.attr == marker.constant
        constants = ast.parse((SOURCE / 'constants.py').read_text())
        enum = next(n for n in constants.body if isinstance(n, ast.ClassDef) and n.name == 'ERROR_MESSAGES')
        assignment = next(
            n
            for n in enum.body
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == marker.constant for t in n.targets)
        )
        assert assignment.lineno == marker.constant_line
        detail = assignment.value
    assert ast.literal_eval(detail) == marker.detail


def test_every_auth_dependency_refusal_has_a_pinned_marker():
    tree = ast.parse((SOURCE / 'utils/auth.py').read_text())
    dependencies = {'get_current_user', 'get_current_user_by_api_key', 'get_verified_user', 'get_admin_user'}
    emissions = {
        (function.name, keyword.value.lineno)
        for function in tree.body
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) and function.name in dependencies
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'HTTPException'
        for keyword in node.keywords
        if keyword.arg == 'detail'
    }
    pinned = {(m.function, m.line) for m in _NOT_ENTERED_MARKERS if m.path == 'utils/auth.py'}
    assert emissions == pinned, 'Auth dependencies gained or lost refusal paths; review the marker table'


@pytest.mark.parametrize('marker', _NOT_ENTERED_MARKERS, ids=lambda m: f'{m.path}:{m.line}')
def test_every_marker_is_excluded_from_coverage(marker):
    for status in marker.statuses:
        assert not entered_the_handler(response(status, {'detail': marker.detail}))


@pytest.mark.parametrize(
    ('status', 'body', 'entered'),
    [
        (200, {'id': 'resource'}, True),
        (201, {}, True),
        (400, {'detail': 'Invalid application input'}, True),
        (401, {'detail': 'An unrelated handler refusal'}, True),
        (403, {'detail': 'An unrelated handler refusal'}, True),
        (422, {'detail': 'Application validation failed'}, True),
        (422, {'detail': [{'loc': ['body'], 'msg': 'missing'}]}, False),
        (422, {'detail': []}, False),
        (429, {'detail': 'An application quota'}, True),
        (500, {'detail': 'Crash'}, True),
        (404, {'detail': 'Not Found'}, False),
        (405, {'detail': 'Method Not Allowed'}, False),
        (302, {}, False),
        (307, {}, False),
        (200, {'requires_2fa': True, 'partial_token': 'partial'}, False),
        (200, {'requires_2fa_setup': True, 'partial_token': 'partial'}, False),
        (200, ['not', 'an', 'error'], True),
    ],
)
def test_response_classification(status, body, entered):
    assert entered_the_handler(response(status, body)) is entered


@pytest.mark.parametrize(
    ('status', 'body'),
    [
        (400, {'detail': 'Wrong password'}),
        (401, {}),
        (403, {}),
        (429, {}),
        (500, {}),
        (302, {}),
        (200, {'requires_2fa': True, 'partial_token': 'partial'}),
        (200, {'requires_2fa_setup': True, 'token': 'misleading'}),
        (200, {}),
        (200, []),
        (200, {'token': ''}),
        (200, {'token': 'sk-api-key', 'token_type': 'Bearer', 'id': 'id', 'email': 'a@example.com', 'role': 'user'}),
    ],
)
def test_authentication_failure_is_loud(status, body):
    with pytest.raises(AuthenticationError):
        session_body(response(status, body), 'a@example.com')


def test_retry_policy_handles_post_disconnect_but_never_status_or_timeout():
    with AttackClient() as client:
        retry = client.session.get_adapter('http://localhost/').max_retries
        assert isinstance(retry, ConnectionRetry)
        assert retry.total == retry.connect == retry.read == 3
        assert retry.allowed_methods is None
        error = ProtocolError('Connection aborted.', RemoteDisconnected('no response'))
        assert retry.increment('POST', '/', error=error).total == 2
        with pytest.raises(ReadTimeoutError):
            retry.increment('POST', '/', error=ReadTimeoutError(None, '/', 'timeout'))
        for status in (301, 302, 307, 400, 401, 403, 422, 429, 500, 503):
            assert not retry.is_retry('POST', status, has_retry_after=True)
        assert retry.status == retry.redirect == 0


@pytest.mark.parametrize('status', [429, 503])
def test_even_retry_after_cannot_trigger_a_status_retry(status):
    retry = ConnectionRetry(total=3, status=0)
    assert not retry.is_retry('GET', status, has_retry_after=True)


def complete_session(**overrides):
    return {'token': 'jwt', 'token_type': 'Bearer', 'id': 'id', 'email': 'a@example.com', 'role': 'user', **overrides}


@pytest.mark.parametrize(
    'fields', [{'email': None}, {'token_type': None}, {'email': 'wrong@example.com'}, {'role': 'garbage'}]
)
def test_malformed_session_fields_are_authentication_errors(fields):
    with pytest.raises(AuthenticationError):
        session_body(response(200, complete_session(**fields)), 'a@example.com')


def test_login_probes_the_bearer_session(monkeypatch):
    calls = []

    def send(session, request, **kwargs):
        calls.append(request)
        return response(200, complete_session())

    monkeypatch.setattr(requests.Session, 'send', send)
    with login('a@example.com', 'password', base_url='http://app') as client:
        assert client.identity['id'] == 'id'
        assert calls[0].url == 'http://app/api/v1/auths/signin'
        assert 'Authorization' not in calls[0].headers
        assert calls[1].url == 'http://app/api/v1/auths/'
        assert calls[1].headers['Authorization'] == 'Bearer jwt'


def test_failed_session_probe_clears_identity(monkeypatch):
    with AttackClient(token='old-token') as client:
        monkeypatch.setattr(client, 'request', lambda *a, **kw: response(200, complete_session(id='someone-else')))
        with pytest.raises(AuthenticationError):
            client.authenticate(response(200, complete_session()), 'a@example.com')
        assert client.token is client.identity is None


def test_cookie_changes_never_replace_or_remove_bearer_auth(monkeypatch):
    calls = []

    def send(session, request, **kwargs):
        calls.append(request)
        assert not kwargs['allow_redirects']
        assert 'Cookie' not in request.headers
        session.cookies.set('token', 'server-cookie')
        return response(200, {})

    monkeypatch.setattr(requests.Session, 'send', send)
    with AttackClient('http://app', token='header-token') as client:
        client.session.cookies.set('token', 'stale-cookie')
        client.request('GET', '/first')
        client.request('POST', '/second', json={'data': 'payload'})
        assert not client.session.cookies
        assert all(r.headers['Authorization'] == 'Bearer header-token' for r in calls)


@pytest.mark.parametrize(
    'kwargs',
    [
        {'allow_redirects': True},
        {'cookies': {'token': 'cookie'}},
        {'headers': {'cookie': 'token=x'}},
        {'auth': ('user', 'password')},
    ],
)
def test_transport_invariants_cannot_be_overridden(kwargs):
    with AttackClient() as client, pytest.raises(ValueError):
        client.request('GET', '/', **kwargs)


def test_lost_bearer_auth_raises_but_role_gate_remains_a_noncoverage_result(monkeypatch):
    replies = iter(
        [
            response(401, {'detail': 'Invalid token'}),
            response(
                401,
                {
                    'detail': 'You do not have permission to access this resource. '
                    'Please contact your administrator for assistance.'
                },
            ),
            response(401, {'detail': 'Invalid token'}),
        ]
    )
    monkeypatch.setattr(requests.Session, 'send', lambda *a, **kw: next(replies))
    with AttackClient('http://app', token='jwt') as client:
        with pytest.raises(AuthenticationError, match='identity was lost'):
            client.request('GET', '/protected')
        assert not entered_the_handler(client.request('GET', '/admin'))
        assert not entered_the_handler(client.request('GET', '/protected', headers={'Authorization': 'Bearer hostile'}))


@pytest.mark.parametrize('probe_status', [200, 401, 403, 500])
def test_ambiguous_refusal_probes_once_and_records_handler_entry(monkeypatch, probe_status):
    calls, refusals = [], []

    def send(session, request, **kwargs):
        calls.append(request)
        if request.url.endswith('/api/v1/auths/'):
            return response(probe_status, {'id': 'id'} if probe_status == 200 else {'detail': '401 Unauthorized'})
        result = response(401, {'detail': '401 Unauthorized'})
        refusals.append(result)
        return result

    monkeypatch.setattr(requests.Session, 'send', send)
    with AttackClient('http://app', token='jwt') as client:
        for path in ['/api/v1/knowledge/create', '/api/v1/skills/create']:
            if probe_status == 200:
                assert entered_the_handler(client.request('POST', path, json={}))
            else:
                with pytest.raises(AuthenticationError, match='identity was lost'):
                    client.request('POST', path, json={})
                assert not entered_the_handler(refusals[-1])
    assert len(calls) == 3
    assert calls[1].method == 'GET'
    assert calls[1].headers['Authorization'] == 'Bearer jwt'
    assert not calls[1].body


@pytest.mark.parametrize(
    'detail',
    ['Not authenticated', 'Invalid token', 'Your session has expired or the token is invalid. Please sign in again.'],
)
def test_unambiguous_identity_loss_never_probes_even_after_cached_success(monkeypatch, detail):
    calls = []
    replies = iter(
        [response(401, {'detail': '401 Unauthorized'}), response(200, {'id': 'id'}), response(401, {'detail': detail})]
    )

    def send(*args, **kwargs):
        calls.append(args)
        return next(replies)

    monkeypatch.setattr(requests.Session, 'send', send)
    with AttackClient('http://app', token='jwt') as client:
        assert entered_the_handler(client.request('POST', '/permission'))
        with pytest.raises(AuthenticationError):
            client.request('GET', '/lost')
        assert client._probe_cache is None
    assert len(calls) == 3


def test_probe_cache_expires_and_is_scoped_to_the_actual_bearer(monkeypatch):
    from . import client as module

    now, probes = [0], []
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])

    def send(session, request, **kwargs):
        if request.url.endswith('/api/v1/auths/'):
            probes.append(request.headers['Authorization'])
            if len(probes) == 1:
                return response(200, {'id': 'id'})
        return response(401, {'detail': '401 Unauthorized'})

    monkeypatch.setattr(requests.Session, 'send', send)
    with AttackClient('http://app', token='jwt') as client:
        assert entered_the_handler(client.request('POST', '/permission'))
        now[0] += module._PROBE_CACHE_SECONDS + 1
        with pytest.raises(AuthenticationError):
            client.request('POST', '/permission')
        client.token = 'replacement'
        with pytest.raises(AuthenticationError):
            client.request('POST', '/permission')
        result = client.request('POST', '/permission', headers={'authorization': 'Bearer hostile'})
        assert not entered_the_handler(result)
    assert probes == ['Bearer jwt', 'Bearer jwt', 'Bearer replacement', 'Bearer hostile']


def test_ambiguous_session_endpoint_failure_does_not_probe_itself(monkeypatch):
    calls = []

    def send(*args, **kwargs):
        calls.append(args)
        return response(401, {'detail': '401 Unauthorized'})

    monkeypatch.setattr(requests.Session, 'send', send)
    with AttackClient('http://app', token='jwt') as client, pytest.raises(AuthenticationError):
        client.request('GET', '/api/v1/auths/')
    assert len(calls) == 1


@pytest.mark.parametrize('status', [302, 307, 429, 500, 503])
def test_real_adapter_returns_the_first_status_without_following_or_retrying(monkeypatch, status):
    calls = []

    def make_request(*args, **kwargs):
        calls.append(args)
        return HTTPResponse(
            status=status,
            body=io.BytesIO(b'{}'),
            headers={'Location': 'http://elsewhere/', 'Retry-After': '0'},
            preload_content=False,
        )

    monkeypatch.setattr(HTTPConnectionPool, '_make_request', make_request)
    with AttackClient('http://app') as client:
        assert client.request('POST', '/write', json={}).status_code == status
    assert len(calls) == 1


def test_real_adapter_retries_a_post_disconnected_before_response(monkeypatch):
    calls = []

    def make_request(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise ProtocolError('Connection aborted.', RemoteDisconnected('no response'))
        return HTTPResponse(status=201, body=io.BytesIO(b'{}'), preload_content=False)

    monkeypatch.setattr(HTTPConnectionPool, '_make_request', make_request)
    with AttackClient('http://app') as client:
        assert client.request('POST', '/write', json={'hostile': 'input'}).status_code == 201
    assert len(calls) == 2


@pytest.mark.parametrize('failure', ['timeout', 'truncated-response'])
def test_real_adapter_does_not_retry_timeouts_or_a_response_already_started(monkeypatch, failure):
    calls = []

    def make_request(*args, **kwargs):
        calls.append(args)
        if failure == 'timeout':
            raise ReadTimeoutError(None, '/', 'timeout')
        return HTTPResponse(status=200, body=io.BytesIO(b'{}'), headers={'Content-Length': '20'}, preload_content=False)

    monkeypatch.setattr(HTTPConnectionPool, '_make_request', make_request)
    with AttackClient('http://app') as client, pytest.raises(requests.RequestException):
        client.request('POST', '/write', json={})
    assert len(calls) == 1


def test_streams_are_not_consumed_by_the_transport(monkeypatch):
    result = response(200, {})
    monkeypatch.setattr(result, 'json', lambda: pytest.fail('transport consumed the response stream'))
    monkeypatch.setattr(requests.Session, 'send', lambda *a, **kw: result)
    with AttackClient('http://app', token='jwt') as client:
        assert client.request('POST', '/stream', stream=True) is result


class IdentityServer:
    """Stateful HTTP boundary double: passwords, email buckets, roles and JWTs."""

    def __init__(self):
        self.users = {}
        self.tokens = {}
        self.attempts = {}
        self.calls = []
        self.issued = 0

    def session(self, user):
        self.issued += 1
        token = f'jwt-{self.issued}'
        self.tokens[token] = user['id']
        return {**user, 'token': token, 'token_type': 'Bearer'}

    def signin(self, form):
        email = form['email'].lower()
        self.attempts[email] = self.attempts.get(email, 0) + 1
        if self.attempts[email] > 15:
            return response(429, {'detail': 'API rate limit exceeded'})
        user = next((u for u in self.users.values() if u['email'] == email and u['password'] == form['password']), None)
        return response(200, self.session(user)) if user else response(400, {'detail': 'bad credentials'})

    def create_user(self, path, form, user):
        from .identities import _EMAIL_TAKEN, _SIGNUP_DISABLED

        if path.endswith('/signup') and self.users:
            return response(403, {'detail': _SIGNUP_DISABLED})
        if path.endswith('/add') and (not user or user['role'] != 'admin'):
            return response(401, {'detail': _SIGNUP_DISABLED})
        if any(u['email'] == form['email'] for u in self.users.values()):
            return response(400, {'detail': _EMAIL_TAKEN})
        user_id = f'user-{self.issued}'
        user = {**form, 'id': user_id, 'role': 'admin' if not self.users else form['role']}
        self.users[user_id] = user
        return response(200, self.session(user))

    def request(self, client, method, path, **kwargs):
        self.calls.append((method, path))
        form = kwargs.get('json', {})
        user = self.users.get(self.tokens.get(client.token))
        if path == '/api/v1/auths/signup' or path == '/api/v1/auths/add':
            return self.create_user(path, form, user)
        if path == '/api/v1/auths/signin':
            return self.signin(form)
        if not user:
            return response(401, {'detail': 'Invalid token'})
        if path == '/api/v1/auths/':
            return response(200, {**user, 'token': client.token, 'token_type': 'Bearer'})
        assert user['role'] == 'admin'
        if path == '/api/v1/users/':
            matches = [u for u in self.users.values() if kwargs.get('params', {}).get('query', '') in u['email']]
            return response(200, {'users': matches, 'total': len(matches)})
        if path.startswith('/api/v1/users/'):
            target = self.users.get(path.split('/')[4])
            if not target:
                return response(400, {'detail': "We could not find what you're looking for :/"})
            if path.endswith('/update'):
                target.update(form)
            return response(200, target)
        pytest.fail(f'Unexpected HTTP call: {method} {path}')


@pytest.fixture
def identity_server(monkeypatch):
    server = IdentityServer()
    monkeypatch.delenv('ATTACK_RECOVERY_TOKEN', raising=False)
    monkeypatch.setattr(AttackClient, 'request', lambda client, *a, **kw: server.request(client, *a, **kw))
    return server


def test_identity_repair_survives_password_damage_throttles_and_driven_token_revocation(identity_server, tmp_path):
    from .identities import ADMIN_EMAIL, INTRUDER_EMAIL, PASSWORD, USER_EMAIL, ensure_identities

    state_path = tmp_path / 'identities.json'
    first = ensure_identities('http://app', state_path=state_path)
    ids = [c.identity['id'] for c in first.clients]
    assert identity_server.calls[0] == ('POST', '/api/v1/auths/signup')
    assert len(identity_server.users) == 3
    private_token = json.loads(state_path.read_text())['recovery_token']
    assert private_token not in {c.token for c in first.clients}
    assert state_path.stat().st_mode & 0o777 == 0o600
    for client in first.clients:
        user = identity_server.users[client.identity['id']]
        user['password'] = 'destroyed'
        identity_server.attempts[user['email']] = 100
        identity_server.tokens.pop(client.token)
    identity_server.users[ids[1]]['role'] = 'pending'
    # Recover a rename interrupted between alias signin and restoring email.
    identity_server.users[ids[2]]['email'] = 'interrupted-alias@example.com'
    first.close()
    second = ensure_identities('http://app', state_path=state_path)
    try:
        assert [c.identity['id'] for c in second.clients] == ids
        assert [c.identity['email'] for c in second.clients] == [ADMIN_EMAIL, USER_EMAIL, INTRUDER_EMAIL]
        assert [c.identity['role'] for c in second.clients] == ['admin', 'user', 'user']
        assert len(identity_server.users) == 3
        assert all(u['password'] == PASSWORD for u in identity_server.users.values())
        assert identity_server.attempts[ADMIN_EMAIL] == identity_server.attempts[USER_EMAIL] == 100
    finally:
        second.close()


def test_identities_can_be_adopted_without_local_state(identity_server, tmp_path):
    from .identities import ensure_identities

    first = ensure_identities('http://app', state_path=tmp_path / 'first.json')
    second = ensure_identities('http://app', state_path=tmp_path / 'second.json')
    try:
        assert [c.identity['id'] for c in first.clients] == [c.identity['id'] for c in second.clients]
        assert len(identity_server.users) == 3
    finally:
        first.close()
        second.close()


def test_a_deleted_ordinary_identity_is_recreated(identity_server, tmp_path):
    from .identities import ensure_identities

    state_path = tmp_path / 'identities.json'
    first = ensure_identities('http://app', state_path=state_path)
    deleted_id = first.user.identity['id']
    del identity_server.users[deleted_id]
    first.close()
    second = ensure_identities('http://app', state_path=state_path)
    try:
        assert second.user.identity['id'] != deleted_id
        assert len(identity_server.users) == 3
    finally:
        second.close()


def test_a_failed_repair_restores_the_stable_email(identity_server, tmp_path, monkeypatch):
    from . import identities as module

    state_path = tmp_path / 'identities.json'
    original = module.ensure_identities('http://app', state_path=state_path)
    original.close()

    def failed_login(*args, **kwargs):
        raise AuthenticationError('2FA challenge')

    monkeypatch.setattr(module, 'login', failed_login)
    with pytest.raises(AuthenticationError, match='2FA'):
        module.ensure_identities('http://app', state_path=state_path)
    assert {u['email'] for u in identity_server.users.values()} == {
        module.ADMIN_EMAIL,
        module.USER_EMAIL,
        module.INTRUDER_EMAIL,
    }


def test_an_unrecoverable_admin_fails_loudly(identity_server, tmp_path):
    from .identities import ensure_identities

    state_path = tmp_path / 'identities.json'
    original = ensure_identities('http://app', state_path=state_path)
    identity_server.users[original.admin.identity['id']]['password'] = 'destroyed'
    identity_server.tokens.clear()
    original.close()
    with pytest.raises(AuthenticationError):
        ensure_identities('http://app', state_path=state_path)


@needs_stack
def test_an_unauthenticated_client_is_refused():
    with AttackClient() as client:
        result = client.request('GET', '/api/v1/auths/')
        assert result.status_code == 401, result.text
        assert not entered_the_handler(result)


@pytest.fixture
def identities():
    from .identities import ensure_identities

    result = ensure_identities()
    yield result
    result.close()


@needs_stack
def test_signup_mints_admin_and_distinct_ordinary_identities(identities):
    assert identities.admin.identity['role'] == 'admin'
    assert identities.user.identity['role'] == identities.intruder.identity['role'] == 'user'
    assert len({c.identity['id'] for c in identities.clients}) == 3
    result = identities.admin.request('GET', '/api/v1/users/')
    assert result.status_code == 200, result.text


@needs_stack
def test_an_authenticated_client_can_write(identities):
    result = identities.user.request('POST', '/api/v1/chats/new', json={'chat': {'title': 'client-selftest'}})
    assert result.status_code == 200, result.text
    assert result.json()['user_id'] == identities.user.identity['id']
    assert entered_the_handler(result)
    deleted = identities.user.request('DELETE', f'/api/v1/chats/{result.json()["id"]}')
    assert deleted.status_code == 200, deleted.text


@needs_stack
def test_wrong_password_is_a_loud_400(identities):
    with pytest.raises(AuthenticationError, match='HTTP 400'):
        login(identities.user.identity['email'], 'wrong-password')


@needs_stack
@pytest.mark.parametrize('challenge', ['requires_2fa', 'requires_2fa_setup'])
def test_real_2fa_signin_success_status_is_rejected(identities, challenge):
    import pyotp

    from .identities import PASSWORD

    admin, user = identities.admin, identities.intruder
    config_path = '/api/v1/configs/2fa'
    original = admin.request('GET', config_path)
    assert original.status_code == 200, original.text
    enrolled = False
    try:
        configured = admin.request(
            'POST',
            config_path,
            json={
                'ENABLE_2FA': True,
                'REQUIRE_2FA': challenge == 'requires_2fa_setup',
                'TWO_FA_GRACE_PERIOD_DAYS': 0,
            },
        )
        assert configured.status_code == 200, configured.text
        if challenge == 'requires_2fa':
            setup = user.request('POST', '/api/v1/auths/2fa/totp/setup')
            assert setup.status_code == 200, setup.text
            secret = setup.json()['secret']
            enabled = user.request(
                'POST',
                '/api/v1/auths/2fa/totp/enable',
                json={
                    'password': PASSWORD,
                    'secret': secret,
                    'code': pyotp.TOTP(secret).now(),
                },
            )
            assert enabled.status_code == 200, enabled.text
            enrolled = True
        with AttackClient() as anonymous:
            result = anonymous.request(
                'POST',
                '/api/v1/auths/signin',
                json={
                    'email': user.identity['email'],
                    'password': PASSWORD,
                },
            )
            assert result.status_code == 200, result.text
            assert result.json()[challenge] is True
            assert not entered_the_handler(result)
            with pytest.raises(AuthenticationError, match='2FA'):
                anonymous.authenticate(result, user.identity['email'])
            assert anonymous.token is None
        with pytest.raises(AuthenticationError, match='2FA'):
            login(user.identity['email'], PASSWORD)
    finally:
        try:
            if enrolled:
                disabled = user.request('POST', '/api/v1/auths/2fa/totp/disable', json={'password': PASSWORD})
                assert disabled.status_code == 200, disabled.text
        finally:
            restored = admin.request('POST', config_path, json=original.json())
            assert restored.status_code == 200, restored.text


@needs_stack
def test_live_repair_preserves_ids_after_throttle_password_change_and_signout(identities):
    from .identities import PASSWORD, ensure_identities

    ids = [c.identity['id'] for c in identities.clients]
    user = identities.user
    changed = user.request(
        'POST',
        '/api/v1/auths/update/password',
        json={
            'password': PASSWORD,
            'new_password': 'Hostile-Replacement-Passw0rd!',
        },
    )
    assert changed.status_code == 200 and changed.json() is True
    with AttackClient() as anonymous:
        for _ in range(20):
            result = anonymous.request(
                'POST',
                '/api/v1/auths/signin',
                json={
                    'email': user.identity['email'],
                    'password': 'wrong-password',
                },
            )
            if result.status_code == 429:
                break
        else:
            pytest.fail('Never reached the real signin throttle')
        assert not entered_the_handler(result)
    signed_out = identities.admin.request('POST', '/api/v1/auths/signout')
    assert signed_out.status_code == 200, signed_out.text
    repaired = ensure_identities()
    try:
        assert [c.identity['id'] for c in repaired.clients] == ids
        result = repaired.user.request('POST', '/api/v1/chats/new', json={'chat': {'title': 'repair-selftest'}})
        assert result.status_code == 200, result.text
        deleted = repaired.user.request('DELETE', f'/api/v1/chats/{result.json()["id"]}')
        assert deleted.status_code == 200, deleted.text
    finally:
        repaired.close()
