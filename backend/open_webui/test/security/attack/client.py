"""Bearer HTTP transport and conservative handler-entry classification."""

from __future__ import annotations

import base64
import hmac
import os
import struct
import time
from dataclasses import dataclass
from http.client import RemoteDisconnected
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.exceptions import ProtocolError
from urllib3.util.retry import Retry

DEFAULT_BASE_URL = 'http://localhost:8080'
# A hosted CI runner is slower than a workstation, and the plane drives every
# operation in the spec. Too short a timeout measures the runner rather than the
# application: it turns "slow here" into "never answered", and before timeouts
# were measurements it ended the whole pass.
DEFAULT_TIMEOUT_SECONDS = float(os.getenv('ATTACK_TIMEOUT_SECONDS', '60'))
_SESSION_PATH = '/api/v1/auths/'
_AMBIGUOUS_AUTH = (401, '401 Unauthorized')
_PROBE_CACHE_SECONDS = 5


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
    RefusalMarker((401,), 'Not authenticated', 'utils/auth.py', 'get_current_user', 370),
    RefusalMarker((401,), 'Invalid token', 'utils/auth.py', 'get_current_user', 398),
    RefusalMarker((401,), 'Invalid token', 'utils/auth.py', 'get_current_user', 423),
    RefusalMarker((403,), '2FA verification required', 'utils/auth.py', 'get_current_user', 407),
    RefusalMarker((401,), _EXPIRED, 'utils/auth.py', 'get_current_user', 430, 'INVALID_TOKEN', 59),
    RefusalMarker((401,), _EXPIRED, 'utils/auth.py', 'get_current_user_by_api_key', 486, 'INVALID_TOKEN', 59),
    RefusalMarker((401,), _EXPIRED, 'routers/auths.py', 'get_session_user', 354, 'INVALID_TOKEN', 59),
    RefusalMarker((401, 403), _PROHIBITED, 'utils/auth.py', 'get_verified_user', 544, 'ACCESS_PROHIBITED', 70),
    RefusalMarker((401, 403), _PROHIBITED, 'utils/auth.py', 'get_admin_user', 621, 'ACCESS_PROHIBITED', 70),
    RefusalMarker(
        (401, 403), _PROHIBITED, 'utils/auth.py', 'get_current_user_by_api_key', 519, 'ACCESS_PROHIBITED', 70
    ),
    RefusalMarker((429,), 'API rate limit exceeded', 'routers/auths.py', 'signin', 1002, 'RATE_LIMIT_EXCEEDED', 87),
    RefusalMarker((400,), _BAD_CREDENTIALS, 'routers/auths.py', 'signin', 1057, 'INVALID_CRED', 60),
    RefusalMarker((401,), '401 Unauthorized', 'utils/auth.py', 'get_current_user', 416, 'UNAUTHORIZED', 69),
    RefusalMarker((401,), '401 Unauthorized', 'utils/auth.py', 'get_current_user', 462, 'UNAUTHORIZED', 69),
    RefusalMarker((401,), 'User mismatch. Please sign in again.', 'utils/auth.py', 'get_current_user', 438),
    RefusalMarker(
        (403,),
        'Use of API key is not enabled in the environment.',
        'utils/auth.py',
        'get_current_user_by_api_key',
        497,
        'API_KEY_NOT_ALLOWED',
        81,
    ),
    # v0.11.3 split the API-key refusal in two: the feature flag at 497, and a
    # per-user permission check at 506. Both emit the same detail, so both must
    # be pinned or the emission count no longer matches the table.
    RefusalMarker(
        (403,),
        'Use of API key is not enabled in the environment.',
        'utils/auth.py',
        'get_current_user_by_api_key',
        506,
        'API_KEY_NOT_ALLOWED',
        81,
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
            if (response.status_code, detail) == _AMBIGUOUS_AUTH:
                return getattr(response, '_attack_bearer_valid', False)
            return False
    return True


class AuthenticationError(RuntimeError):
    def __init__(self, *args, status: int | None = None):
        super().__init__(*args)
        # The refusal that produced this, where one exists. Callers repairing an
        # identity must distinguish a rejected credential from a throttle or a
        # 2FA challenge: only the first is repairable, and repairing the others
        # would destroy a working password to work around a temporary refusal.
        self.status = status


def session_body(response: requests.Response, email: str) -> dict:
    body = json_body(response)
    context = f'Attack identity {email}: authentication failed (HTTP {response.status_code})'
    if not 200 <= response.status_code < 300 or not isinstance(body, dict):
        raise AuthenticationError(context, status=response.status_code)
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
        self._probe_cache = None
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
        result = self._send(method, path, **kwargs)
        headers = requests.structures.CaseInsensitiveDict(kwargs.get('headers', {}))
        authorization = headers.get('Authorization', f'Bearer {self.token}' if self.token else '')
        own_bearer = bool(self.token and authorization == f'Bearer {self.token}')
        body = json_body(result) if result.status_code in (401, 403) else None
        detail = body.get('detail') if isinstance(body, dict) else None
        marker = (result.status_code, detail) if isinstance(detail, str) else None
        if marker == _AMBIGUOUS_AUTH and authorization.lower().startswith('bearer '):
            # The session endpoint itself has no router-level UNAUTHORIZED gate.
            valid = path != _SESSION_PATH and self._probe_bearer(authorization)
            result._attack_bearer_valid = valid
            if valid:
                return result
        if own_bearer and marker in _LOST_AUTH:
            if marker != _AMBIGUOUS_AUTH or path == _SESSION_PATH:
                self._probe_cache = None
            raise AuthenticationError(f'Attack bearer identity was lost at {method.upper()} {path}: {detail}')
        return result

    def _probe_bearer(self, authorization: str) -> bool:
        cached = self._probe_cache
        if cached is not None and cached[0] == authorization and time.monotonic() < cached[1]:
            return cached[2]
        # Use the transport directly: an ambiguous probe failure must not recurse.
        probe = self._send('GET', _SESSION_PATH, headers={'Authorization': authorization})
        valid = probe.status_code == 200
        self._probe_cache = (authorization, time.monotonic() + _PROBE_CACHE_SECONDS, valid)
        return valid

    def _send(self, method: str, path: str, **kwargs) -> requests.Response:
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
        kwargs.setdefault('timeout', DEFAULT_TIMEOUT_SECONDS)
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
        return result

    def authenticate(self, response: requests.Response, email: str, *, role: str | None = None):
        self.token = None
        self.identity = None
        self._probe_cache = None
        body = session_body(response, email)
        self.token = body['token']
        try:
            self.verify_identity(email=email, role=role, user_id=body['id'])
        except Exception:
            self.token = None
            self.identity = None
            raise
        return self

    def reauthenticate(self, email: str, password: str):
        """Replace this client's session in place, pinned to the identity it holds.

        A password change revokes every token the user holds, including the one
        that made the request (routers/users.py:976), so a helper repairing its
        own driving identity destroys the session it repairs from. Signing in
        again is only safe if the replacement is the same user in the same role:
        a client that quietly came back as somebody else would drive the rest of
        the pass under an identity nobody chose. The caller passes the email
        because the repair may have just changed it.
        """
        if not self.identity:
            raise AuthenticationError('Only an authenticated client can reauthenticate')
        expected_id, expected_role = self.identity['id'], self.identity['role']
        self.token = None
        self.identity = None
        self._probe_cache = None
        result = self.request('POST', '/api/v1/auths/signin', json={'email': email, 'password': password})
        self.authenticate(result, email, role=expected_role)
        if self.identity['id'] != expected_id:
            self.token = None
            self.identity = None
            raise AuthenticationError('Reauthentication returned a different user')
        return self

    @property
    def user_id(self):
        return self.identity['id'] if self.identity else None

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


def totp_code(secret: str, *, at: float | None = None) -> str:
    """The RFC 6238 code pyotp's defaults verify (SHA-1, 6 digits, 30 s), without importing pyotp."""
    key = base64.b32decode(secret.upper() + '=' * (-len(secret) % 8))
    counter = int(time.time() if at is None else at) // 30
    digest = hmac.new(key, struct.pack('>Q', counter), 'sha1').digest()
    offset = digest[-1] & 0x0F
    return f'{(struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}'
