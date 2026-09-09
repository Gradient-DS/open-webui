"""Create and repair CI identities through HTTP, without importing the app.

Call ensure_identities between passes, serially. A separate admin JWT is saved
in the worktree's ignored .cache directory (mode 0600), never handed to a pass.
It survives password changes and revocation of a driven client's distinct jti.
If that recovery JWT expires after the admin password has been destroyed, HTTP
alone cannot recover the first admin: provide a valid recovery token or reset
the test stack. That condition raises; it never returns an anonymous client.

Signin counts attempts by email (routers/auths.py:922; utils/rate_limit.py:42).
Repair temporarily changes an identity's email through the admin update API,
signs in once using that fresh bucket, then restores its stable email. IDs and
resource ownership stay intact. No Redis keys or application limits are changed.
"""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .client import AttackClient, AuthenticationError, json_body, login

PASSWORD = 'Attack-Plane-Passw0rd!'
ADMIN_EMAIL = 'attack-admin@example.com'
USER_EMAIL = 'attack-user@example.com'
INTRUDER_EMAIL = 'attack-intruder@example.com'
_EMAIL_TAKEN = (
    'Uh-oh! This email is already registered. Sign in with your existing account or choose another email to start anew.'
)
_SIGNUP_DISABLED = (
    'You do not have permission to access this resource. Please contact your administrator for assistance.'
)


@dataclass
class AttackIdentities:
    admin: AttackClient
    user: AttackClient
    intruder: AttackClient

    @property
    def clients(self):
        return self.admin, self.user, self.intruder

    def close(self):
        for client in self.clients:
            client.close()


def _object(response, operation):
    body = json_body(response)
    if not 200 <= response.status_code < 300 or not isinstance(body, dict):
        raise RuntimeError(f'Identity {operation} failed (HTTP {response.status_code})')
    return body


def _save(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.{uuid4().hex}.tmp')
    try:
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as handle:
            json.dump(state, handle)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _bootstrap(base_url):
    client = AttackClient(base_url)
    try:
        result = client.request(
            'POST',
            '/api/v1/auths/signup',
            json={
                'email': ADMIN_EMAIL,
                'password': PASSWORD,
                'name': 'Attack admin',
            },
        )
        body = json_body(result)
        if isinstance(body, dict) and (result.status_code, body.get('detail')) in (
            (400, _EMAIL_TAKEN),
            (403, _SIGNUP_DISABLED),
        ):
            client.close()
            return login(ADMIN_EMAIL, PASSWORD, base_url=base_url, role='admin')
        return client.authenticate(result, ADMIN_EMAIL, role='admin')
    except Exception:
        client.close()
        raise


def _update(admin, user_id, **fields):
    body = _object(admin.request('POST', f'/api/v1/users/{user_id}/update', json=fields), 'update')
    if body.get('id') != user_id or any(body.get(k) != v for k, v in fields.items() if k != 'password'):
        raise RuntimeError('Identity update did not persist the requested identity/role')
    return body


@contextmanager
def signin_alias(admin, user_id, email, **fields):
    alias = f'attack-signin-{uuid4().hex}@example.com'
    try:
        _update(admin, user_id, email=alias, **fields)
        yield alias
    finally:
        _update(admin, user_id, email=email)


def _fresh_login(admin, user_id, email, role):
    client = None
    try:
        with signin_alias(admin, user_id, email, password=PASSWORD, role=role, name=f'Attack {role}') as alias:
            client = login(alias, PASSWORD, base_url=admin.base_url, role=role)
            if client.identity['id'] != user_id:
                raise AuthenticationError('Repair signed in as a different user')
        client.verify_identity(email=email, role=role, user_id=user_id)
        return client
    except Exception:
        if client:
            client.close()
        raise


def _find_by_email(admin, email):
    # Exact email match, across all filtered pages; never adopt a fuzzy match.
    page = 1
    matches = []
    while True:
        body = _object(admin.request('GET', '/api/v1/users/', params={'query': email, 'page': page}), 'lookup')
        users = body.get('users')
        if not isinstance(users, list) or not isinstance(body.get('total'), int):
            raise RuntimeError('Malformed identity lookup')
        matches.extend(user['id'] for user in users if user.get('email') == email)
        if page * 30 >= body['total']:
            break
        if not users:
            raise RuntimeError('Identity lookup stopped before its reported total')
        page += 1
    if len(matches) > 1:
        raise RuntimeError(f'Ambiguous attack identity: {email}')
    if matches:
        return matches[0]


def _find_or_create(admin, state, label, email):
    user_id = state['ids'].get(label)
    if user_id:
        result = admin.request('GET', f'/api/v1/users/{user_id}')
        body = json_body(result)
        missing = (
            result.status_code == 400
            and isinstance(body, dict)
            and body.get('detail') == "We could not find what you're looking for :/"
        )
        if not missing:
            body = _object(result, 'lookup by id')
            if body.get('id') != user_id:
                raise RuntimeError('Identity lookup returned a different user')
            return user_id
    user_id = _find_by_email(admin, email)
    if user_id:
        return user_id
    body = _object(
        admin.request(
            'POST',
            '/api/v1/auths/add',
            json={
                'email': email,
                'password': PASSWORD,
                'name': f'Attack {label}',
                'role': 'user',
            },
        ),
        'creation',
    )
    if not body.get('id') or body.get('email') != email or body.get('role') != 'user':
        raise RuntimeError('Identity creation returned a different identity/role')
    return body['id']


def _recover_admin(base_url, state):
    token = os.getenv('ATTACK_RECOVERY_TOKEN') or state.get('recovery_token')
    if token:
        recovery = AttackClient(base_url, token=token)
        try:
            recovery.verify_identity(role='admin', user_id=state['ids'].get('admin'))
            return recovery
        except AuthenticationError:
            recovery.close()
        except Exception:
            recovery.close()
            raise
    try:
        return _bootstrap(base_url)
    except AuthenticationError as error:
        raise AuthenticationError(
            f'{error}. No usable admin recovery session; provide ATTACK_RECOVERY_TOKEN '
            'or reset the CI database if its admin credentials are destroyed.'
        ) from error


def ensure_identities(base_url=None, *, state_path: Path | None = None) -> AttackIdentities:
    with AttackClient(base_url) as probe:
        base_url = probe.base_url
    if state_path is None:
        key = hashlib.sha256(base_url.encode()).hexdigest()[:16]
        state_path = Path(__file__).resolve().parents[5] / '.cache' / 'attack' / f'{key}.json'
    state_path = Path(state_path)
    state = json.loads(state_path.read_text()) if state_path.exists() else {'base_url': base_url, 'ids': {}}
    if state.get('base_url') != base_url:
        raise RuntimeError('Identity recovery state belongs to another stack URL')
    recovery = None
    clients = []
    try:
        recovery = _recover_admin(base_url, state)
        admin_id = recovery.identity['id']
        if state['ids'].get('admin') != admin_id:
            state['ids'] = {}
        state['ids']['admin'] = admin_id
        state['recovery_token'] = recovery.token
        _save(state_path, state)
        renewed = _fresh_login(recovery, admin_id, ADMIN_EMAIL, 'admin')
        recovery.close()
        recovery = renewed
        state['recovery_token'] = recovery.token
        _save(state_path, state)
        # Every pass receives a different jti from the private recovery JWT.
        clients.append(_fresh_login(recovery, admin_id, ADMIN_EMAIL, 'admin'))
        for label, email in (('user', USER_EMAIL), ('intruder', INTRUDER_EMAIL)):
            user_id = _find_or_create(recovery, state, label, email)
            state['ids'][label] = user_id
            _save(state_path, state)  # Save the id before temporarily renaming it.
            clients.append(_fresh_login(recovery, user_id, email, 'user'))
        if len({client.identity['id'] for client in clients}) != 3:
            raise RuntimeError('Attack identities must be three distinct users')
        return AttackIdentities(*clients)
    except Exception:
        for client in clients:
            client.close()
        raise
    finally:
        if recovery:
            recovery.close()
