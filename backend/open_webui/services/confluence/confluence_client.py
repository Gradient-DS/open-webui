"""Confluence Cloud REST API v2 client.

Async httpx wrapper supporting three auth modes:

- ``oauth`` (default): per-user Atlassian 3LO. Talks to the API gateway
  (`https://api.atlassian.com/ex/confluence/{cloudId}/wiki/api/v2`) with a
  Bearer token, refreshed mid-flight via ``token_provider``.
- ``basic``: a single service credential (username + classic API token). Talks
  to the customer site directly (`https://{site}/wiki/api/v2`) with HTTP Basic
  auth. API tokens do not refresh — a 401 is terminal.
- ``scoped``: a single service credential (email + Atlassian *scoped* API
  token). Uses the same HTTP Basic header as ``basic`` but the token only works
  against the gateway (`https://api.atlassian.com/ex/confluence/{cloudId}/...`),
  so it reuses the oauth transport. Not refreshable — a 401 is terminal; a 401
  whose body contains "scope does not match" means the token is missing a
  granular read scope.

Mirrors the retry/refresh pattern used by the OneDrive GraphClient.
"""

import asyncio
import base64
import logging
from typing import Optional, Callable, Awaitable, Dict, Any, List, Tuple
from urllib.parse import urlparse, parse_qs

import httpx

log = logging.getLogger(__name__)

_API_BASE = 'https://api.atlassian.com/ex/confluence'

# v2 endpoints accept a cursor in a `cursor` query param; responses carry a
# `_links.next` string (absolute URL with cursor) when more pages exist.
_DEFAULT_LIMIT = 100


def normalize_site_url(site_url: str) -> str:
    """Reduce a Confluence site URL to ``scheme://host``.

    Admins often paste a value that includes the ``/wiki`` context path, a
    trailing slash, or a deep link copied from the browser. Every request
    appends ``/wiki/api/v2/...``, so a stored ``.../wiki`` would double up into
    ``.../wiki/wiki/api/v2/...`` and 404. Stripping to ``scheme://host`` (and
    defaulting a missing scheme to https) makes the stored value robust to those
    mistakes. Returns '' for blank input.
    """
    raw = (site_url or '').strip()
    if not raw:
        return ''
    parsed = urlparse(raw if '://' in raw else f'https://{raw}')
    netloc = parsed.netloc or parsed.path.split('/', 1)[0]
    if not netloc:
        return ''
    return f'{parsed.scheme or "https"}://{netloc}'


def _cursor_from_next_link(next_link: Optional[str]) -> Optional[str]:
    """Extract the opaque ``cursor`` token from a v2 ``_links.next`` value.

    v2 returns ``next`` as a host-relative path with the cursor embedded as a
    query param (``/wiki/api/v2/spaces?limit=100&cursor=<token>``). Callers that
    page via the one-shot ``list_*`` methods re-pass the returned value as the
    bare ``cursor=`` param, so it must be just the opaque token — not the whole
    path, which Confluence rejects with a 400. (``_paginated_get`` follows the
    full URL instead, so it is unaffected and keeps using ``_links.next`` raw.)
    """
    if not next_link:
        return None
    values = parse_qs(urlparse(next_link).query).get('cursor')
    return values[0] if values else None


class ConfluenceClient:
    """Async client for Confluence Cloud REST API v2 with retry logic."""

    def __init__(
        self,
        access_token: str = '',
        cloud_id: str = '',
        token_provider: Optional[Callable[[], Awaitable[Optional[str]]]] = None,
        *,
        auth_mode: str = 'oauth',
        site_url: str = '',
        basic_username: str = '',
        basic_api_token: str = '',
    ):
        self._access_token = access_token
        self._cloud_id = cloud_id
        self._token_provider = token_provider
        self._auth_mode = auth_mode
        self._site_url = normalize_site_url(site_url)
        self._client: Optional[httpx.AsyncClient] = None
        # Basic-auth header is static — precompute it once. Both service modes
        # ('basic' and 'scoped') authenticate with the same email:token Basic
        # header; only the transport (site vs gateway) differs between them.
        self._basic_auth_header: Optional[str] = None
        if auth_mode in ('basic', 'scoped'):
            encoded = base64.b64encode(f'{basic_username}:{basic_api_token}'.encode('utf-8')).decode('ascii')
            self._basic_auth_header = f'Basic {encoded}'

    @property
    def cloud_id(self) -> str:
        return self._cloud_id

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Low-level request helpers
    # ------------------------------------------------------------------

    def _v2_url(self, path: str) -> str:
        """Build a full v2 URL.

        `path` should start without leading '/' (e.g. 'spaces', 'pages/{id}').
        In ``basic`` mode the customer site is addressed directly; in ``oauth``
        and ``scoped`` modes the request goes through the Atlassian API gateway
        keyed by cloudId.
        """
        leaf = path.lstrip('/')
        if self._auth_mode == 'basic':
            return f'{self._site_url}/wiki/api/v2/{leaf}'
        return f'{_API_BASE}/{self._cloud_id}/wiki/api/v2/{leaf}'  # oauth + scoped

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
    ) -> httpx.Response:
        """Make authenticated request with retry logic.

        Handles:
        - 401: Token refresh via token_provider callback (once)
        - 429: Respects Retry-After header
        - 5xx: Exponential backoff
        """
        client = await self._get_client()
        last_exception = None
        token_refreshed = False

        for attempt in range(max_retries):
            try:
                # Service modes ('basic', 'scoped') use the static Basic header;
                # oauth uses a refreshable Bearer token.
                if self._auth_mode in ('basic', 'scoped'):
                    auth_header = self._basic_auth_header
                else:
                    auth_header = f'Bearer {self._access_token}'
                response = await client.request(
                    method,
                    url,
                    params=params,
                    headers={
                        'Authorization': auth_header,
                        'Accept': 'application/json',
                    },
                )

                # A scoped token that is missing a granular read scope surfaces
                # as a 401 (not 403) with "scope does not match" in the body.
                # Log it distinctly so the cause is actionable (add the scope at
                # token creation) rather than looking like a bad credential.
                if (
                    response.status_code == 401
                    and self._auth_mode == 'scoped'
                    and 'scope does not match' in (response.text or '').lower()
                ):
                    log.debug(
                        'Confluence scoped token returned 401 "scope does not match" — '
                        'the token is missing a required read scope (e.g. read:page:confluence)'
                    )

                # Only oauth has a refreshable token. The service modes use a
                # static credential — a 401 is terminal, there is nothing to
                # refresh.
                if (
                    response.status_code == 401
                    and not token_refreshed
                    and self._token_provider
                    and self._auth_mode == 'oauth'
                ):
                    log.info('Received 401 from Confluence, attempting token refresh')
                    try:
                        new_token = await self._token_provider()
                        if new_token:
                            self._access_token = new_token
                            token_refreshed = True
                            continue
                    except Exception as e:
                        log.warning('Confluence token refresh failed: %s', e)
                    return response

                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', '60'))
                    log.warning('Confluence rate limited, waiting %d seconds', retry_after)
                    await asyncio.sleep(retry_after)
                    continue

                if response.status_code >= 500:
                    wait_time = 2**attempt
                    log.warning(
                        'Confluence server error %d, retrying in %d seconds',
                        response.status_code,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                    continue

                return response

            except (httpx.HTTPStatusError, httpx.TransportError) as e:
                last_exception = e
                if attempt < max_retries - 1:
                    wait_time = 2**attempt
                    log.warning(
                        'Confluence request failed (attempt %d/%d): %s — retrying in %ds',
                        attempt + 1,
                        max_retries,
                        e,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                else:
                    if isinstance(e, httpx.ConnectError):
                        raise ConnectionError(
                            'Unable to reach Confluence API. Please check your network connection.'
                        ) from e
                    raise

        raise RuntimeError(f'Failed after {max_retries} retries: {last_exception}')

    async def _get_json(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make authenticated GET request and return JSON."""
        response = await self._request_with_retry('GET', url, params)
        response.raise_for_status()
        return response.json()

    async def _paginated_get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        max_pages: int = 100,
    ) -> List[Dict[str, Any]]:
        """Iterate a v2 paginated endpoint and return all results."""
        params = dict(params or {})
        params.setdefault('limit', _DEFAULT_LIMIT)

        all_items: List[Dict[str, Any]] = []
        url = self._v2_url(path)
        page = 0

        while url and page < max_pages:
            data = await self._get_json(url, params=params)
            all_items.extend(data.get('results', []))

            next_link = (data.get('_links') or {}).get('next')
            if not next_link:
                break
            # v2 returns `next` as a host-relative path carrying the cursor.
            # basic mode → resolve against the customer site; oauth and scoped
            # modes → resolve against the Atlassian gateway host.
            if next_link.startswith('/'):
                if self._auth_mode == 'basic':
                    url = f'{self._site_url}{next_link}'
                else:
                    # oauth + scoped go through the cloudId-keyed gateway. The v2
                    # `next` path is site-relative (/wiki/api/v2/...), so it must be
                    # re-prefixed with /ex/confluence/{cloudId}; resolving it against
                    # the bare api.atlassian.com host drops the gateway route → 404.
                    url = f'{_API_BASE}/{self._cloud_id}{next_link}'
            else:
                url = next_link
            params = None  # next link already carries the cursor
            page += 1

        return all_items

    # ------------------------------------------------------------------
    # Public API — discovery
    # ------------------------------------------------------------------

    async def list_spaces(
        self,
        cursor: Optional[str] = None,
        limit: int = _DEFAULT_LIMIT,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """List spaces the user can access (one page).

        Returns (results, next_cursor). next_cursor is the opaque cursor token
        for the next page (re-pass it as the ``cursor`` argument) or None when
        there are no more results.
        """
        params: Dict[str, Any] = {'limit': limit}
        if cursor:
            params['cursor'] = cursor

        data = await self._get_json(self._v2_url('spaces'), params=params)
        results = data.get('results', [])
        next_link = (data.get('_links') or {}).get('next')
        return results, _cursor_from_next_link(next_link)

    async def list_all_spaces(self) -> List[Dict[str, Any]]:
        """Iterate all pages of the spaces endpoint."""
        return await self._paginated_get('spaces')

    async def list_pages_in_space(
        self,
        space_id: str,
        cursor: Optional[str] = None,
        limit: int = _DEFAULT_LIMIT,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """List pages in a space (one page)."""
        params: Dict[str, Any] = {'limit': limit}
        if cursor:
            params['cursor'] = cursor

        data = await self._get_json(self._v2_url(f'spaces/{space_id}/pages'), params=params)
        return data.get('results', []), _cursor_from_next_link((data.get('_links') or {}).get('next'))

    async def list_all_pages_in_space(self, space_id: str) -> List[Dict[str, Any]]:
        """Iterate every page in a space."""
        return await self._paginated_get(f'spaces/{space_id}/pages')

    async def list_page_children(
        self,
        page_id: str,
        cursor: Optional[str] = None,
        limit: int = _DEFAULT_LIMIT,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """List direct child pages of a given page (one page)."""
        params: Dict[str, Any] = {'limit': limit}
        if cursor:
            params['cursor'] = cursor

        data = await self._get_json(self._v2_url(f'pages/{page_id}/children'), params=params)
        return data.get('results', []), _cursor_from_next_link((data.get('_links') or {}).get('next'))

    async def list_all_page_descendants(self, page_id: str) -> List[Dict[str, Any]]:
        """Return all descendant pages of the given page (BFS)."""
        all_items: List[Dict[str, Any]] = []
        queue: List[str] = [page_id]
        visited: set[str] = set()

        while queue:
            current_id = queue.pop(0)
            if current_id in visited:
                continue
            visited.add(current_id)

            children = await self._paginated_get(f'pages/{current_id}/children')
            for child in children:
                all_items.append(child)
                queue.append(child['id'])

        return all_items

    async def list_all_page_labels(self, page_id: str) -> List[Dict[str, Any]]:
        """Return all labels attached to a page."""
        return await self._paginated_get(f'pages/{page_id}/labels')

    async def list_all_page_ancestors(self, page_id: str) -> List[Dict[str, Any]]:
        """Return ancestor pages of a page.

        v2 returns ancestors from root → direct parent (path order). Used to
        compose a human-readable breadcrumb for citations / RAG context.
        """
        return await self._paginated_get(f'pages/{page_id}/ancestors')

    # ------------------------------------------------------------------
    # Public API — page content
    # ------------------------------------------------------------------

    async def get_page(
        self,
        page_id: str,
        include_body: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve a single page's metadata (and optionally rendered body).

        Returns None if the page is not found (404).
        Raises httpx.HTTPStatusError for other non-2xx responses.
        """
        params: Dict[str, Any] = {}
        if include_body:
            # Rendered HTML — convenient for markdown conversion downstream.
            params['body-format'] = 'view'
        # include-version defaults to true on v2, but be explicit.
        params['include-version'] = 'true'

        response = await self._request_with_retry(
            'GET',
            self._v2_url(f'pages/{page_id}'),
            params=params,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def get_space(self, space_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a single space's metadata. Returns None on 404."""
        response = await self._request_with_retry('GET', self._v2_url(f'spaces/{space_id}'))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
