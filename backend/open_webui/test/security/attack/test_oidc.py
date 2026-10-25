"""Exercise the requests login flow against fake Keycloak and OWUI ASGI apps."""

import json
import time
from email.message import Message
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode
from uuid import uuid4

import pytest
import requests
from requests.adapters import BaseAdapter
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from . import crossuser, identities, oidc, plane, seeds
from .client import AttackClient, AuthenticationError, json_body, login
from .test_client import IdentityServer


class FakeStack:
    def __init__(self):
        self.identities = IdentityServer()
        self.users = {}
        self.calls = []
        self.form_action = '/realms/fictional/login-actions/authenticate?execution=one&client_id=owui'
        self.callback_error = False
        self.callback_token = True
        self.callback_session = True
        self.oauth_sessions = {}
        self.probe_email = None
        self.lookup_status = 200
        self.create_status = 201
        self.redirect_loop = False

    async def dispatch(self, request):
        host, path = request.url.hostname, request.url.path
        self.calls.append((host, request.method, path, dict(request.headers)))
        if host == 'keycloak':
            return await self.keycloak(request, path)
        if path == '/oauth/oidc/login':
            if self.redirect_loop:
                return RedirectResponse('/oauth/oidc/login')
            response = RedirectResponse('http://keycloak/realms/fictional/protocol/openid-connect/auth')
            response.set_cookie('oidc_state', 'fictional-state')
            return response
        if path == '/oauth/oidc/callback':
            return self.callback(request)
        assert path.startswith('/api/'), f'Unexpected app request: {path}'
        assert set(request.cookies) <= {'oauth_session_id'}
        token = request.headers.get('authorization', '').removeprefix('Bearer ')
        if path == '/api/v1/memories/' or path.startswith('/api/v1/export/data'):
            return self.platform(request, token)
        payload = await request.json() if request.method == 'POST' else {}
        result = self.identities.request(
            SimpleNamespace(token=token), request.method, path, json=payload, params=dict(request.query_params)
        )
        body = json_body(result)
        if self.probe_email and path == '/api/v1/auths/':
            body['email'] = self.probe_email
        return JSONResponse(body, status_code=result.status_code)

    def callback(self, request):
        assert request.cookies.get('oidc_state') == 'fictional-state'
        if self.callback_error:
            return RedirectResponse('/auth?error=fictional-denial')
        email = request.query_params['code']
        user = next((u for u in self.identities.users.values() if u['email'] == email), None)
        if user is None:
            result = self.identities.create_user(
                '/api/v1/auths/signup' if not self.identities.users else '/api/v1/auths/add',
                {'email': email, 'name': 'Attack fictional', 'password': uuid4().hex, 'role': 'pending'},
                {'role': 'admin'},
            )
            user = self.identities.users[json_body(result)['id']]
        response = RedirectResponse('/auth')
        if self.callback_token:
            response.set_cookie('token', self.identities.session(user)['token'])
        if self.callback_session:
            session_id = uuid4().hex
            self.oauth_sessions[session_id] = user['id']
            response.set_cookie('oauth_session_id', session_id, httponly=True)
        return response

    def platform(self, request, token):
        user = self.identities.holder(token)
        subject = self.oauth_sessions.get(request.cookies.get('oauth_session_id'))
        if not user or subject != user['id'] or 'x-api-key' in request.headers:
            return JSONResponse({'detail': 'identity:platform_login'}, status_code=403)
        if request.url.path.endswith('/status'):
            return JSONResponse({'status': 'ready', 'export_path': f'exports/{user["id"]}/fictional.json'})
        return JSONResponse({'id': 'fictional-memory'})

    async def keycloak(self, request, path):
        if path == '/realms/master/protocol/openid-connect/token':
            assert 'authorization' not in request.headers
            form = parse_qs((await request.body()).decode())
            assert form['username'] == ['fictional-admin']
            assert form['password'] == ['fictional-admin-password']
            return JSONResponse({'access_token': 'fictional-admin-token'})
        if path == '/admin/realms/fictional/users':
            assert request.headers['authorization'] == 'Bearer fictional-admin-token'
            if request.method == 'GET':
                matches = [u for u in self.users.values() if u['username'] == request.query_params['username']]
                return JSONResponse(matches, status_code=self.lookup_status)
            user = await request.json()
            assert user['emailVerified'] is True and user['enabled'] is True
            assert user['credentials'][0]['temporary'] is False
            self.users[user['username']] = user
            return JSONResponse({}, status_code=self.create_status)
        assert 'authorization' not in request.headers
        if path == '/realms/fictional/protocol/openid-connect/auth':
            response = HTMLResponse(
                f'<form id="kc-form-login" method="post" action="{self.form_action.replace("&", "&amp;")}">'
                '<input type="hidden" name="credentialId" value="fictional&amp;credential">'
                '<input name="username"><input name="password" type="password"></form>'
            )
            response.set_cookie('keycloak_session', 'fictional-session')
            return response
        assert path == '/realms/fictional/login-actions/authenticate'
        assert request.cookies.get('keycloak_session') == 'fictional-session'
        form = parse_qs((await request.body()).decode())
        assert form['credentialId'] == ['fictional&credential']
        email, password = form['username'][0], form['password'][0]
        user = self.users.get(email)
        if not user or user['credentials'][0]['value'] != password:
            return HTMLResponse('<p>Fictional invalid credentials</p>')
        return RedirectResponse('http://app/oauth/oidc/callback?' + urlencode({'code': email}), status_code=302)


class ASGIAdapter(BaseAdapter):
    def __init__(self, client):
        self.client = client

    def send(self, request, **kwargs):
        incoming = self.client.request(
            request.method, request.url, headers=dict(request.headers), content=request.body, follow_redirects=False
        )
        result = requests.Response()
        result.status_code = incoming.status_code
        result.url = str(incoming.url)
        result.headers = requests.structures.CaseInsensitiveDict(incoming.headers)
        result._content = incoming.content
        result._content_consumed = True
        result.request = request
        headers = Message()
        for key, value in incoming.headers.multi_items():
            headers[key] = value
        result.raw = SimpleNamespace(_original_response=SimpleNamespace(msg=headers))
        requests.cookies.extract_cookies_to_jar(result.cookies, request, result.raw)
        return result

    def close(self):
        pass


@pytest.fixture
def stack(monkeypatch):
    for name, value in {
        'ATTACK_OIDC_LOGIN': '1',
        'ATTACK_KEYCLOAK_URL': 'http://keycloak',
        'ATTACK_KEYCLOAK_REALM': 'fictional',
        'ATTACK_KEYCLOAK_ADMIN_USER': 'fictional-admin',
        'ATTACK_KEYCLOAK_ADMIN_PASSWORD': 'fictional-admin-password',
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv('ATTACK_RECOVERY_TOKEN', raising=False)
    stack = FakeStack()
    monkeypatch.setattr(time, 'sleep', stack.identities.advance)
    app = Starlette(routes=[Route('/{path:path}', stack.dispatch, methods=['GET', 'POST'])])
    with TestClient(app) as client:
        # TestClient has a browser-style jar of its own; only requests owns cookies here.
        original_request = client.request

        def asgi_request(*args, **kwargs):
            client.cookies.clear()
            return original_request(*args, **kwargs)

        monkeypatch.setattr(client, 'request', asgi_request)
        adapter = ASGIAdapter(client)
        monkeypatch.setattr(requests.Session, 'get_adapter', lambda *args, **kwargs: adapter)
        yield stack


def test_provision_and_form_login_capture_bearer_and_isolate_cookies(stack):
    oidc.provision(identities.ADMIN_EMAIL, identities.PASSWORD, 'Attack admin')
    oidc.provision(identities.ADMIN_EMAIL, identities.PASSWORD, 'Attack admin')
    with login(identities.ADMIN_EMAIL, identities.PASSWORD, base_url='http://app', role='admin') as client:
        assert client.token.startswith('jwt-')
        assert not client.session.cookies
        assert client.identity['email'] == identities.ADMIN_EMAIL
        assert client.request('GET', '/api/v1/memories/').status_code == 200
        assert client.oauth_session_id in stack.oauth_sessions
        client.reauthenticate(identities.ADMIN_EMAIL, identities.PASSWORD)
        assert client.identity['role'] == 'admin'
    assert sum(method == 'POST' and path == '/admin/realms/fictional/users' for _, method, path, _ in stack.calls) == 1
    assert not any(path == '/api/v1/auths/signin' for _, _, path, _ in stack.calls)


def test_oidc_identities_repair_roles_email_password_damage_and_revocation(stack, tmp_path):
    path = tmp_path / 'identities.json'
    with_clients = identities.ensure_identities('http://app', state_path=path)
    ids = [c.user_id for c in with_clients.clients]
    with_clients.close()
    user = stack.identities.users[ids[1]]
    user.update(email='fictional-damaged@example.com', role='pending', password='fictional-damaged')
    stack.identities.tokens.clear()
    repaired = identities.ensure_identities('http://app', state_path=path)
    try:
        assert [c.user_id for c in repaired.clients] == ids
        assert [c.identity['role'] for c in repaired.clients] == ['admin', 'user', 'user']
        assert repaired.user.identity['email'] == identities.USER_EMAIL
        assert len(stack.users) == 3
        assert not any('attack-signin-' in u['email'] for u in stack.identities.users.values())
        assert not any(
            p in {'/api/v1/auths/signin', '/api/v1/auths/signup', '/api/v1/auths/add'} for _, _, p, _ in stack.calls
        )
    finally:
        repaired.close()


def test_disposable_and_seed_identities_use_oidc(stack, tmp_path):
    actors = identities.ensure_identities('http://app', state_path=tmp_path / 'identities.json')
    try:
        with plane._throwaway(actors.admin) as disposable:
            assert disposable.identity['role'] == 'admin'
        with crossuser._actor(actors.admin, actors.user, True) as disposable:
            assert disposable.identity['role'] == 'user'
        ctx = seeds.SeedContext(actors.user, actors.admin, seeds.SURFACE)
        body = ctx.request(
            'POST',
            '/api/v1/auths/add',
            actor=actors.admin,
            json={
                'email': 'fictional-export@example.com',
                'password': 'Fictional-Password!',
                'name': 'Attack export',
                'role': 'user',
            },
        )
        ctx.payloads['user'] = body
        ctx.values['user'] = body['id']
        assert seeds.seed_export(ctx, {'key': 'export'}).startswith(f'exports/{body["id"]}/')
        with login(body['email'], 'Fictional-Password!', base_url='http://app', role='user') as exporter:
            assert exporter.user_id == body['id']
        assert not any(p == '/api/v1/auths/add' for _, _, p, _ in stack.calls)
    finally:
        actors.close()


def test_password_endpoint_negative_probe_remains_enabled_in_oidc_mode(stack, tmp_path):
    from .test_live_client import test_wrong_password_is_a_loud_400

    actors = identities.ensure_identities('http://app', state_path=tmp_path / 'identities.json')
    try:
        test_wrong_password_is_a_loud_400(actors)
        assert oidc.enabled()
        assert stack.identities.attempts == {identities.USER_EMAIL: 1}
        actors.user.verify_identity(email=identities.USER_EMAIL, role='user')
    finally:
        actors.close()


@pytest.mark.parametrize(
    'failure', ['password', 'callback_error', 'callback_token', 'callback_session', 'probe_email', 'redirect_loop']
)
def test_failed_oidc_never_falls_back_to_password_login(stack, failure):
    oidc.provision(identities.ADMIN_EMAIL, identities.PASSWORD, 'Attack admin')
    password = identities.PASSWORD
    if failure == 'password':
        password = 'fictional-wrong'
    elif failure == 'probe_email':
        stack.probe_email = 'fictional-other@example.com'
    else:
        setattr(stack, failure, failure not in {'callback_token', 'callback_session'})
    with pytest.raises(AuthenticationError):
        login(identities.ADMIN_EMAIL, password, base_url='http://app', role='admin')
    assert not any(p == '/api/v1/auths/signin' for _, _, p, _ in stack.calls)


@pytest.mark.parametrize(
    'action',
    [
        'http://elsewhere/login-actions/authenticate',
        '/realms/other/login-actions/authenticate',
        'http://app/oauth/oidc/callback',
    ],
)
def test_credentials_do_not_leave_the_configured_realm(stack, action):
    stack.form_action = action
    with pytest.raises(AuthenticationError):
        login('fictional-user@example.com', 'Fictional-Password!', base_url='http://app')
    assert not any(method == 'POST' for _, method, _, _ in stack.calls)


@pytest.mark.parametrize('operation', ['lookup', 'create'])
def test_provisioning_failure_is_loud(stack, operation):
    setattr(stack, f'{operation}_status', 503)
    with pytest.raises(AuthenticationError, match='HTTP 503'):
        oidc.provision('fictional-user@example.com', 'Fictional-Password!', 'Attack user')


def test_reauthentication_rejects_a_different_user(stack):
    oidc.provision(identities.ADMIN_EMAIL, identities.PASSWORD, 'Attack admin')
    oidc.provision(identities.USER_EMAIL, identities.PASSWORD, 'Attack user')
    with login(identities.ADMIN_EMAIL, identities.PASSWORD, base_url='http://app') as client:
        # Same role isolates the id pin from the separate role check.
        stack.identities.create_user(
            '/api/v1/auths/add',
            {'email': identities.USER_EMAIL, 'password': 'fictional', 'name': 'Attack user', 'role': 'admin'},
            {'role': 'admin'},
        )
        with pytest.raises(AuthenticationError, match='different user'):
            client.reauthenticate(identities.USER_EMAIL, identities.PASSWORD)
        assert client.token is None and client.identity is None


def test_missing_oidc_settings_fail_without_requests(stack, monkeypatch):
    monkeypatch.delenv('ATTACK_KEYCLOAK_REALM')
    with pytest.raises(ValueError, match='ATTACK_KEYCLOAK_REALM'):
        login('fictional-user@example.com', 'Fictional-Password!', base_url='http://app')
    assert not stack.calls


@pytest.mark.parametrize(
    'headers',
    [
        {'Authorization': 'Bearer fictional-other'},
        {'Authorization': 'Bearer sk-fictional-key'},
        {'Authorization': ''},
        {'x-api-key': 'sk-fictional-key'},
    ],
)
def test_oidc_reference_is_only_sent_with_the_actors_own_bearer(stack, headers):
    oidc.provision(identities.ADMIN_EMAIL, identities.PASSWORD, 'Attack admin')
    with login(identities.ADMIN_EMAIL, identities.PASSWORD, base_url='http://app') as client:
        assert client.request('GET', '/api/v1/memories/').status_code == 200
        result = client.request('GET', '/api/v1/memories/', headers=headers)
        assert result.status_code == 403
        assert 'cookie' not in stack.calls[-1][3]
        with AttackClient('http://app', token=client.token) as bearer_only:
            assert bearer_only.request('GET', '/api/v1/memories/').status_code == 403
        assert client.request('GET', '/api/v1/memories/').status_code == 200
        assert not client.session.cookies


def test_oidc_recovery_preserves_the_server_session_reference(stack, tmp_path):
    path = tmp_path / 'identities.json'
    first = identities.ensure_identities('http://app', state_path=path)
    first.close()
    state = json.loads(path.read_text())
    session_id = state['recovery_oauth_session_id']
    assert session_id in stack.oauth_sessions
    offset = len(stack.calls)
    second = identities.ensure_identities('http://app', state_path=path)
    try:
        host, method, route, headers = stack.calls[offset]
        assert (host, method, route) == ('app', 'GET', '/api/v1/auths/')
        assert headers['cookie'] == f'oauth_session_id={session_id}'
        assert second.user.request('GET', '/api/v1/memories/').status_code == 200
    finally:
        second.close()
