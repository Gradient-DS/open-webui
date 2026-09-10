"""Issuer and tenant validation for multi-tenant Microsoft Entra sign-in."""

import re

MULTI_TENANT_AUTHORITIES = frozenset({'organizations', 'common'})

_GUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)


def is_issuer_allowed(
    issuer: str | None,
    tid: str | None,
    login_base: str,
    allowed_tenants: list[str],
) -> bool:
    """Return True when the ID token issuer names an allowed Entra tenant.

    :param issuer: the token's ``iss`` claim.
    :param tid: the token's ``tid`` claim.
    :param login_base: the Entra login host, e.g. ``https://login.microsoftonline.com``.
    :param allowed_tenants: tenant GUIDs permitted to sign in.
    """
    if not issuer or not tid or not allowed_tenants:
        return False

    prefix = f'{login_base.rstrip("/")}/'
    suffix = '/v2.0'
    if not issuer.startswith(prefix) or not issuer.endswith(suffix):
        return False

    tenant = issuer[len(prefix) : -len(suffix)]
    if not _GUID_RE.match(tenant):
        return False

    if tenant.casefold() != tid.strip().casefold():
        return False

    return tenant.casefold() in {t.strip().casefold() for t in allowed_tenants if t and t.strip()}
