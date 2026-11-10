"""Provision three ordinary OWUI actors through their own OIDC logins.

The historical `admin` sweep slot has platform Owner authority only; its OWUI
role is `user`, like every other slot. No management API repairs local accounts.
"""

from dataclasses import dataclass

from . import oidc
from .client import AttackClient, AuthenticationError, login

PASSWORD = 'Attack-Plane-Passw0rd!'
ADMIN_EMAIL = 'attack-owner@example.invalid'
USER_EMAIL = 'attack-user@example.invalid'
INTRUDER_EMAIL = 'attack-intruder@example.invalid'


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


def create_oidc_identity(actor: AttackClient, *, email: str, password: str, name: str, role: str) -> AttackClient:
    if not oidc.enabled() or role != 'user':
        raise ValueError('Security actors require OIDC and the ordinary user role')
    oidc.provision(email, password, name)
    return login(email, password, base_url=actor.base_url, role='user')


def ensure_identities(base_url=None) -> AttackIdentities:
    if not oidc.enabled():
        raise ValueError('The platform security sweep requires ATTACK_OIDC_LOGIN=1')
    clients = []
    try:
        for email, name in (
            (ADMIN_EMAIL, 'Fictional owner'),
            (USER_EMAIL, 'Fictional member'),
            (INTRUDER_EMAIL, 'Fictional outsider'),
        ):
            oidc.provision(email, PASSWORD, name)
            clients.append(login(email, PASSWORD, base_url=base_url, role='user'))
        if len({client.user_id for client in clients}) != 3:
            raise AuthenticationError('Attack identities must be three distinct users')
        return AttackIdentities(*clients)
    except Exception:
        for client in clients:
            client.close()
        raise
