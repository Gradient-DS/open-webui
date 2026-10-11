"""The configuration boundary for operations that require soev-api."""

from fastapi import Depends
from fastapi.routing import APIRoute

from open_webui import config
from open_webui.soev.client import SoevApiError
from open_webui.utils.auth import get_verified_user


class SoevNotConfigured(SoevApiError):
    def __init__(self):
        super().__init__(503, 'soev_not_configured', 'Soev API is not configured.')


def configured() -> bool:
    return bool(config.SOEV_API_URL)


def ensure_configured() -> None:
    if not configured():
        raise SoevNotConfigured()


def require_soev(user=Depends(get_verified_user)) -> None:
    """Route dependency: an anonymous caller is refused before availability is reported."""
    ensure_configured()


class SoevRoute(APIRoute):
    """Resolves `require_soev` after the route's own dependencies, so their 401/403 precede its 503."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dependant.dependencies.sort(key=lambda dependency: dependency.call is require_soev)
