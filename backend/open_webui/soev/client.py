"""HTTP boundary for soev-api, with fresh assertions and caller-owned mutation keys.

No retries: subject assertions are single-use and mutations belong to the caller.
"""

import logging
from collections.abc import AsyncIterator, Callable

import httpx

log = logging.getLogger(__name__)


class SoevApiError(Exception):
    """A soev-api problem or a sanitized upstream failure."""

    def __init__(self, status: int, code: str, detail: str, constraint: str | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.constraint = constraint


def _response_error(response: httpx.Response) -> SoevApiError:
    status = response.status_code
    fallback = SoevApiError(status, 'upstream_error', f'soev-api returned HTTP {status}')
    if response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/problem+json':
        return fallback
    try:
        problem = response.json()
    except ValueError:
        return fallback
    if not isinstance(problem, dict):
        return fallback
    code, detail, constraint = problem.get('code'), problem.get('detail'), problem.get('constraint')
    if not isinstance(code, str) or not isinstance(detail, str):
        return fallback
    if constraint is not None and not isinstance(constraint, str):
        return fallback
    return SoevApiError(status, code, fallback.detail if status >= 500 else detail, constraint)


class SoevClient:
    """Send tenant API requests without persisting credentials or logging payloads."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        subject_minter: Callable[[str], str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip('/')
        self._api_key = api_key
        self._subject_minter = subject_minter
        self._timeout = timeout

    async def get(self, path: str, *, as_user: str | None = None, params: dict | None = None) -> dict:
        response = await self._request('GET', path, as_user=as_user, params=params)
        return self._json_object(response)

    async def pages(self, path: str, *, as_user: str | None = None, params: dict | None = None) -> AsyncIterator[dict]:
        """Yield each page's data items, minting a fresh assertion for every cursor request."""
        query = dict(params or {})
        while True:
            page = await self.get(path, as_user=as_user, params=query)
            for item in page['data']:
                yield item
            cursor = page.get('next_cursor')
            if not cursor:
                return
            query['cursor'] = cursor

    async def send(
        self, method: str, path: str, body: dict | None, *, as_user: str | None = None, idempotency_key: str
    ) -> dict | None:
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError('A mutation requires a nonempty idempotency key')
        method = method.upper()
        if method not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            raise ValueError('send requires a mutation method')
        response = await self._request(method, path, body=body, as_user=as_user, idempotency_key=idempotency_key)
        if response.status_code == 204:
            return None
        return self._json_object(response)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        as_user: str | None = None,
        params: dict | None = None,
        idempotency_key: str | None = None,
    ) -> httpx.Response:
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('An API path must start with a single slash')
        headers = {'Authorization': f'Bearer {self._api_key}'}
        if as_user is not None:
            if self._subject_minter is None:
                raise ValueError('Acting as a user requires a subject minter')
            headers['X-Soev-Subject'] = self._subject_minter(as_user)
        if idempotency_key is not None:
            headers['Idempotency-Key'] = idempotency_key
        try:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=False) as client:
                response = await client.request(
                    method, f'{self._base_url}{path}', headers=headers, params=params, json=body
                )
        except httpx.TransportError as error:
            status = 504 if isinstance(error, httpx.TimeoutException) else 502
            log.warning('soev-api transport failure', extra={'status': status, 'request_id': None})
            raise SoevApiError(status, 'upstream_error', 'soev-api request failed') from None
        log.log(
            logging.INFO if response.is_success else logging.WARNING,
            'soev-api response',
            extra={'status': response.status_code, 'request_id': response.headers.get('X-Request-ID')},
        )
        if not response.is_success:
            raise _response_error(response)
        return response

    @staticmethod
    def _json_object(response: httpx.Response) -> dict:
        try:
            result = response.json()
        except ValueError:
            result = None
        if not isinstance(result, dict):
            raise SoevApiError(502, 'upstream_error', 'soev-api returned an invalid JSON object')
        return result
