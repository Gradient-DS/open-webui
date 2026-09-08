"""Bearer HTTP transport and conservative handler-entry classification."""

from __future__ import annotations

import os
from dataclasses import dataclass
from http.client import RemoteDisconnected
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.exceptions import ProtocolError
from urllib3.util.retry import Retry

DEFAULT_BASE_URL = 'http://localhost:8080'


@dataclass(frozen=True)
class RefusalMarker:
    statuses: tuple[int, ...]
    detail: str
    path: str
    function: str
    line: int
    constant: str | None = None
    constant_line: int | None = None


# Lines identify the detail expression, not the surrounding raise. Constants
# are pinned both at their definition and at each dependency that emits them.
_EXPIRED = 'Your session has expired or the token is invalid. Please sign in again.'
_PROHIBITED = 'You do not have permission to access this resource. Please contact your administrator for assistance.'
_BAD_CREDENTIALS = 'The email or password provided is incorrect. Please check for typos and try logging in again.'
_NOT_ENTERED_MARKERS = (
    RefusalMarker((401,), 'Not authenticated', 'utils/auth.py', 'get_current_user', 338),
    RefusalMarker((401,), 'Invalid token', 'utils/auth.py', 'get_current_user', 364),
    RefusalMarker((401,), 'Invalid token', 'utils/auth.py', 'get_current_user', 389),
    RefusalMarker((403,), '2FA verification required', 'utils/auth.py', 'get_current_user', 373),
    RefusalMarker((401,), _EXPIRED, 'utils/auth.py', 'get_current_user', 396, 'INVALID_TOKEN', 60),
    RefusalMarker((401,), _EXPIRED, 'utils/auth.py', 'get_current_user_by_api_key', 451, 'INVALID_TOKEN', 60),
    RefusalMarker((401,), _EXPIRED, 'routers/auths.py', 'get_session_user', 331, 'INVALID_TOKEN', 60),
    RefusalMarker((401, 403), _PROHIBITED, 'utils/auth.py', 'get_verified_user', 500, 'ACCESS_PROHIBITED', 71),
    RefusalMarker((401, 403), _PROHIBITED, 'utils/auth.py', 'get_admin_user', 530, 'ACCESS_PROHIBITED', 71),
    RefusalMarker(
        (401, 403), _PROHIBITED, 'utils/auth.py', 'get_current_user_by_api_key', 478, 'ACCESS_PROHIBITED', 71
    ),
    RefusalMarker((429,), 'API rate limit exceeded', 'routers/auths.py', 'signin', 925, 'RATE_LIMIT_EXCEEDED', 88),
    RefusalMarker((400,), _BAD_CREDENTIALS, 'routers/auths.py', 'signin', 980, 'INVALID_CRED', 61),
    RefusalMarker((401,), '401 Unauthorized', 'utils/auth.py', 'get_current_user', 382, 'UNAUTHORIZED', 70),
    RefusalMarker((401,), '401 Unauthorized', 'utils/auth.py', 'get_current_user', 427, 'UNAUTHORIZED', 70),
    RefusalMarker((401,), 'User mismatch. Please sign in again.', 'utils/auth.py', 'get_current_user', 404),
    RefusalMarker(
        (403,),
        'Use of API key is not enabled in the environment.',
        'utils/auth.py',
        'get_current_user_by_api_key',
        466,
        'API_KEY_NOT_ALLOWED',
        82,
    ),
)
_REFUSALS = {(status, marker.detail) for marker in _NOT_ENTERED_MARKERS for status in marker.statuses}
_LOST_AUTH = {
    (status, marker.detail)
    for marker in _NOT_ENTERED_MARKERS
    if marker.function == 'get_current_user'
    for status in marker.statuses
}


def json_body(response: requests.Response):
    try:
        return response.json()
    except ValueError:
        return None


def entered_the_handler(response: requests.Response) -> bool:
    # Missing routes, method mismatches and redirects cannot vouch for coverage.
    # A 5xx remains visible; later passes must separately assert against crashes.
    if response.status_code < 200 or 300 <= response.status_code < 400 or response.status_code in (404, 405):
        return False
    body = json_body(response)
    if isinstance(body, dict):
        if body.get('requires_2fa') or body.get('requires_2fa_setup'):
            return False
        detail = body.get('detail')
        if isinstance(detail, list):
            return False
        if isinstance(detail, str) and (response.status_code, detail) in _REFUSALS:
            return False
    return True


class AuthenticationError(RuntimeError):
    pass


def session_body(response: requests.Response, email: str) -> dict:
    body = json_body(response)
    context = f'Attack identity {email}: authentication failed (HTTP {response.status_code})'
    if not 200 <= response.status_code < 300 or not isinstance(body, dict):
        raise AuthenticationError(context)
    if body.get('requires_2fa') or body.get('requires_2fa_setup'):
        raise AuthenticationError(f'{context}: 2FA challenge or setup is not a session')
    token = body.get('token')
    if (
        not isinstance(token, str)
        or not token.strip()
        or token.startswith('sk-')
        or not isinstance(body.get('token_type'), str)
        or body['token_type'].lower() != 'bearer'
        or not isinstance(body.get('id'), str)
        or not body['id']
        or not isinstance(body.get('email'), str)
        or body['email'].lower() != email.lower()
        or body.get('role') not in {'admin', 'user', 'pending'}
    ):
        raise AuthenticationError(f'{context}: missing or mismatched bearer session fields')
    return body


class ConnectionRetry(Retry):
    def is_retry(self, method, status_code, has_retry_after=False):
        # urllib3 otherwise retries 429/503 with Retry-After even with an empty
        # status_forcelist. Statuses are measurements, including throttles.
        return False

    def increment(self, method=None, url=None, response=None, error=None, *args, **kwargs):
        if error is not None and self._is_read_error(error):
            if not (
                isinstance(error, ProtocolError) and any(isinstance(arg, RemoteDisconnected) for arg in error.args)
            ):
                raise error
        return super().increment(method, url, response, error, *args, **kwargs)


class AttackClient:
    def __init__(self, base_url: str | None = None, *, token: str | None = None):
        self.base_url = (base_url or os.getenv('ATTACK_BASE_URL', DEFAULT_BASE_URL)).rstrip('/')
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme not in {'http', 'https'}
            or not parsed.netloc
            or parsed.username
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError('ATTACK_BASE_URL must be an HTTP(S) URL without credentials, query or fragment')
        self.session = requests.Session()
        self.session.trust_env = False  # No ambient proxy or .netrc credentials.
        self.token = token
        self.identity = None
        retry = ConnectionRetry(
            total=3,
            connect=3,
            read=3,
            other=0,
            status=0,
            redirect=0,
            allowed_methods=None,
            backoff_factor=0.2,
            raise_on_status=False,
            respect_retry_after_header=False,
        )
        for scheme in ('http://', 'https://'):
            self.session.mount(scheme, HTTPAdapter(max_retries=retry))

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('Attack paths must be relative to the configured app, starting with /')
        headers = requests.structures.CaseInsensitiveDict(kwargs.pop('headers', {}))
        if 'Cookie' in headers or 'cookies' in kwargs or 'auth' in kwargs:
            raise ValueError('AttackClient uses bearer headers only')
        if self.token and 'Authorization' not in headers:
            headers['Authorization'] = f'Bearer {self.token}'
        if kwargs.pop('allow_redirects', False):
            raise ValueError('AttackClient never follows redirects')
        self.session.cookies.clear()
        kwargs.setdefault('timeout', 30)
        try:
            result = self.session.request(
                method.upper(),
                f'{self.base_url}{path}',
                headers=headers,
                allow_redirects=False,
                **kwargs,
            )
        finally:
            self.session.cookies.clear()
        body = json_body(result) if result.status_code in (401, 403) else None
        detail = body.get('detail') if isinstance(body, dict) else None
        if (
            self.token
            and headers.get('Authorization') == f'Bearer {self.token}'
            and isinstance(detail, str)
            and (result.status_code, detail) in _LOST_AUTH
        ):
            raise AuthenticationError(f'Attack bearer identity was lost at {method.upper()} {path}: {detail}')
        return result

    def authenticate(self, response: requests.Response, email: str, *, role: str | None = None):
        self.token = None
        self.identity = None
        body = session_body(response, email)
        self.token = body['token']
        try:
            self.verify_identity(email=email, role=role, user_id=body['id'])
        except Exception:
            self.token = None
            self.identity = None
            raise
        return self

    def verify_identity(self, *, email=None, role=None, user_id=None):
        result = self.request('GET', '/api/v1/auths/')
        body = json_body(result)
        if (
            result.status_code != 200
            or not isinstance(body, dict)
            or not body.get('id')
            or (email is not None and body.get('email') != email)
            or (role is not None and body.get('role') != role)
            or (user_id is not None and body.get('id') != user_id)
        ):
            raise AuthenticationError('Bearer session probe failed or returned a different identity/role')
        self.identity = body
        return body

    def close(self):
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def login(email: str, password: str, *, base_url: str | None = None, role: str | None = None) -> AttackClient:
    client = AttackClient(base_url)
    try:
        result = client.request('POST', '/api/v1/auths/signin', json={'email': email, 'password': password})
        return client.authenticate(result, email, role=role)
    except Exception:
        client.close()
        raise
