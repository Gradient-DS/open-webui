"""The configuration boundary for operations that require soev-api."""

from open_webui import config
from open_webui.soev.client import SoevApiError


class SoevNotConfigured(SoevApiError):
    def __init__(self):
        super().__init__(503, 'soev_not_configured', 'Soev API is not configured.')


def configured() -> bool:
    return bool(config.SOEV_API_URL)


def require_soev() -> None:
    if not configured():
        raise SoevNotConfigured()
