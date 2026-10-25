"""CI-only Keycloak provisioning and Open WebUI's HTTP authorization flow."""

from __future__ import annotations

import os
from html.parser import HTMLParser
from typing import TYPE_CHECKING
from urllib.parse import quote, urljoin, urlsplit

import requests

from .client import DEFAULT_TIMEOUT_SECONDS, AuthenticationError, json_body

if TYPE_CHECKING:
    from .client import AttackClient


def enabled() -> bool:
    return os.getenv('ATTACK_OIDC_LOGIN') == '1'


def _setting(name: str) -> str:
    value = os.environ.get(name, '')
    if not value:
        raise ValueError(f'{name} is required with ATTACK_OIDC_LOGIN=1')
    return value


def _keycloak() -> tuple[str, str]:
    base = _setting('ATTACK_KEYCLOAK_URL').rstrip('/')
    parsed = urlsplit(base)
    if (
        parsed.scheme not in {'http', 'https'}
        or not parsed.netloc
        or parsed.username
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError('ATTACK_KEYCLOAK_URL must be an HTTP(S) URL without credentials, query or fragment')
    return base, quote(_setting('ATTACK_KEYCLOAK_REALM'), safe='')


def provision(email: str, password: str, name: str) -> None:
    base, realm = _keycloak()
    with requests.Session() as session:
        session.trust_env = False
        response = session.post(
            f'{base}/realms/master/protocol/openid-connect/token',
            data={
                'grant_type': 'password',
                'client_id': 'admin-cli',
                'username': _setting('ATTACK_KEYCLOAK_ADMIN_USER'),
                'password': _setting('ATTACK_KEYCLOAK_ADMIN_PASSWORD'),
            },
            timeout=DEFAULT_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        body = json_body(response)
        if response.status_code != 200 or not isinstance(body, dict) or not body.get('access_token'):
            raise AuthenticationError(f'Keycloak admin authentication failed (HTTP {response.status_code})')
        session.headers['Authorization'] = f'Bearer {body["access_token"]}'
        users_url = f'{base}/admin/realms/{realm}/users'
        response = session.get(
            users_url,
            params={'username': email, 'exact': 'true'},
            timeout=DEFAULT_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        users = json_body(response)
        if response.status_code != 200 or not isinstance(users, list):
            raise AuthenticationError(f'Keycloak user lookup failed (HTTP {response.status_code})')
        if users:
            if len(users) != 1 or users[0].get('username') != email or users[0].get('email') != email:
                raise AuthenticationError('Keycloak user lookup returned a different identity')
            return
        response = session.post(
            users_url,
            json={
                'username': email,
                'email': email,
                'firstName': name,
                'lastName': 'Sweep',
                'enabled': True,
                'emailVerified': True,
                'requiredActions': [],
                'credentials': [{'type': 'password', 'value': password, 'temporary': False}],
            },
            timeout=DEFAULT_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        if response.status_code != 201:
            raise AuthenticationError(f'Keycloak user creation failed (HTTP {response.status_code})')


class LoginForm(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.action = None
        self.fields = {}
        self.inside = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs)
        if tag == 'form':
            self.inside = attrs.get('id') == 'kc-form-login' and attrs.get('method', '').lower() == 'post'
            if self.inside:
                self.action = attrs.get('action')
        elif tag == 'input' and self.inside and attrs.get('name') and attrs.get('type') == 'hidden':
            self.fields[attrs['name']] = attrs.get('value', '')

    def handle_endtag(self, tag: str) -> None:
        if tag == 'form':
            self.inside = False


def _origin(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    return parsed.scheme, parsed.netloc


def _check_url(url: str, app: str, keycloak: str, realm: str) -> None:
    parsed = urlsplit(url)
    app_path = urlsplit(app).path.rstrip('/')
    kc_path = urlsplit(keycloak).path.rstrip('/')
    app_allowed = _origin(url) == _origin(app) and parsed.path in {
        f'{app_path}/oauth/oidc/login',
        f'{app_path}/oauth/oidc/callback',
        f'{app_path}/auth',
    }
    kc_allowed = _origin(url) == _origin(keycloak) and parsed.path.startswith(f'{kc_path}/realms/{realm}/')
    if parsed.username or not (app_allowed or kc_allowed):
        raise AuthenticationError('OIDC flow left the configured app or Keycloak realm')


def authenticate(client: AttackClient, email: str, password: str, *, role: str | None = None) -> AttackClient:
    keycloak, realm = _keycloak()
    url = f'{client.base_url}/oauth/oidc/login'
    method, data, submitted = 'GET', None, False
    # Only the server-side OIDC session reference follows the bearer afterward.
    with requests.Session() as session:
        session.trust_env = False
        for _ in range(20):
            _check_url(url, client.base_url, keycloak, realm)
            response = session.request(method, url, data=data, allow_redirects=False, timeout=DEFAULT_TIMEOUT_SECONDS)
            callback = urlsplit(url).path == f'{urlsplit(client.base_url).path.rstrip("/")}/oauth/oidc/callback'
            token = response.cookies.get('token') if callback else None
            session_id = response.cookies.get('oauth_session_id') if callback else None
            if callback and (not token or not session_id or not 300 <= response.status_code < 400):
                raise AuthenticationError(
                    f'OIDC callback did not return a session (HTTP {response.status_code})', status=response.status_code
                )
            if callback and token and 300 <= response.status_code < 400:
                if token.startswith('sk-') or not token.strip():
                    raise AuthenticationError('OIDC callback did not return a bearer session')
                client.token = token
                client.oauth_session_id = session_id
                try:
                    client.verify_identity(email=email, role=role)
                except Exception:
                    client.token = None
                    client.oauth_session_id = None
                    client.identity = None
                    raise
                return client
            if response.is_redirect:
                url = urljoin(url, response.headers['Location'])
                method, data = 'GET', None
                continue
            form = LoginForm()
            if response.status_code == 200 and not submitted and _origin(url) == _origin(keycloak):
                form.feed(response.text)
            if not form.action:
                raise AuthenticationError(
                    f'OIDC authentication failed (HTTP {response.status_code}): no login form or session',
                    status=response.status_code,
                )
            url = urljoin(url, form.action)
            # Never send credentials to an app endpoint or another realm.
            _check_url(url, client.base_url, keycloak, realm)
            if _origin(url) != _origin(keycloak) or '/login-actions/' not in urlsplit(url).path:
                raise AuthenticationError('OIDC login form did not target Keycloak login-actions')
            method, data, submitted = 'POST', {**form.fields, 'username': email, 'password': password}, True
    raise AuthenticationError('OIDC authentication exceeded the redirect limit')
