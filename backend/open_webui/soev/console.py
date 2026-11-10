"""Console navigation follows the platform's live caller decision."""

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, ValidationError

from open_webui.soev import identity
from open_webui.soev.client import SoevApiError


class ConsoleAccess(BaseModel):
    model_config = ConfigDict(extra='forbid')
    console_url: str | None


async def access(user) -> ConsoleAccess:
    payload = await identity.build_client().get('/v1/console-access', as_user=identity.external_ref(user), cache=False)
    try:
        result = ConsoleAccess.model_validate(payload)
        if result.console_url is not None:
            url = urlsplit(result.console_url)
            if (
                url.scheme not in {'https', 'http'}
                or not url.netloc
                or url.username is not None
                or url.password is not None
                or url.path != '/console/'
                or url.query
                or url.fragment
            ):
                raise ValueError('Invalid console URL')
        return result
    except (ValidationError, ValueError):
        raise SoevApiError(502, 'upstream_error', 'Invalid console eligibility response') from None
