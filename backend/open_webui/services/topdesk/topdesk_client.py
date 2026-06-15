"""TOPdesk Knowledge Base REST API client.

Async httpx wrapper for the TOPdesk SaaS Knowledge Base REST API
(``knowledge-base-v1``), used by the service-account sync. Talks to the customer
tenant directly (``{TOPDESK_URL}{TOPDESK_KB_API_PATH}``) with a static HTTP Basic
auth header (operator login + application token — see ``services/topdesk/auth.py``).
The credential never refreshes, so a 401 is terminal.

Modelled on ``services/confluence/confluence_client.py`` (async httpx,
retry/backoff, 401-terminal). The KB API is plain REST:

- ``GET /knowledgeItems`` — list, with ``start``/``page_size`` offset paging,
  a FIQL ``query`` filter, a ``fields`` selector, and an optional ``language``.
  Returns ``{"item": [...], "prev"?, "next"?}`` (HTTP 200 = full, 206 = partial).
- ``GET /knowledgeItems/{id|number}`` — a single item (returned directly).

The lightweight connection probe (``probe``) uses ``GET /tas/api/version`` (auth +
reachability), falling back to a minimal KB list (which also confirms the KB-v1
feature is enabled on the tenant).

FIELD SELECTION
---------------
The REST API does NOT return content/title/status/etc. unless they are named in
the ``fields`` param (the spec warns: omit ``translation.content.*`` and the
content block comes back empty). All KB calls therefore request ``_KI_FIELDS`` by
default; callers that need a lean payload (the tree picker) pass a narrower set.
``_KI_FIELDS`` is the single source of truth for the synced field set — a schema
correction is a one-line edit here.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger(__name__)


# =====================================================================
# REST field set + Accept headers (single source of truth)
# =====================================================================

# Field selector requested on every list/get for sync. Content title/body/etc.
# live under ``translation.content`` — requesting them populates the nested block
# (a bare list otherwise returns it empty). Centralised so a schema correction is
# one edit; ``services/topdesk/mapping.py`` reads the resulting nested shape.
_KI_FIELDS = (
    'parent,status,visibility,urls,language,title,description,content,keywords,'
    'creationDate,modificationDate,availableTranslations'
)

# Vendor media types from the OpenAPI spec.
_ACCEPT_LIST = 'application/x.topdesk-kb-ki-list-v1+json'
_ACCEPT_ITEM = 'application/x.topdesk-kb-ki-v1+json'
_ACCEPT_JSON = 'application/json'

# Default page size for a single list page. The spec allows 1–1000; sync paging
# uses the max (1000) for fewer round-trips.
_DEFAULT_PAGE_SIZE = 100
_PAGE_SIZE_MAX = 1000

# Hard safety cap on the number of pages the pagination loop will walk, so a
# misbehaving server (e.g. a ``next`` that never clears) cannot spin forever.
_MAX_PAGES = 200


# =====================================================================
# Errors
# =====================================================================


class TopdeskAuthError(Exception):
    """Raised on a terminal 401 — the service credential is invalid.

    Service-account mode has no token to refresh, so a 401 is not retried.
    """


class TopdeskApiError(Exception):
    """Raised when the REST API returns a structured error body.

    The KB API surfaces validation/business errors as
    ``{"errors": [{"errorCode", "appliesTo", "errorMessage"}]}`` (typically with a
    400). The first ``errorMessage`` is surfaced as the exception message; the raw
    list is preserved on ``.errors``. (Replaces the old GraphQL error type; the
    router maps it to a clean HTTP status.)
    """

    def __init__(self, message: str, errors: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.errors = errors or []


class TopdeskTransientError(Exception):
    """Raised when transient 429/5xx retries are exhausted.

    Carries the last observed HTTP ``status_code`` so a caller (e.g. the router)
    can map it to a clean 502/503 without string-matching the message. Distinct
    from non-retryable 4xx, which still surface as ``httpx.HTTPStatusError`` via
    ``raise_for_status()``.
    """

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


# =====================================================================
# Client
# =====================================================================


class TopdeskClient:
    """Async client for the TOPdesk Knowledge Base REST API with retry logic."""

    def __init__(
        self,
        base_url: str = '',
        username: str = '',
        app_password: str = '',
        *,
        kb_api_path: Optional[str] = None,
        page_size: int = _DEFAULT_PAGE_SIZE,
    ):
        self._base_url = (base_url or '').rstrip('/')
        self._username = (username or '').strip()
        self._app_password = app_password or ''
        self._page_size = page_size
        # Resolve the KB API base path lazily-but-once; config is the default source.
        if kb_api_path is None:
            from open_webui.config import TOPDESK_KB_API_PATH

            kb_api_path = TOPDESK_KB_API_PATH
        self._kb_api_path = '/' + (kb_api_path or '').strip('/')
        self._client: Optional[httpx.AsyncClient] = None
        # Auth header is static — precompute it once. Built via the auth helper so
        # the Basic-only rule lives in one place.
        from open_webui.services.topdesk.auth import build_auth_header

        self._auth_header = build_auth_header(self._username, self._app_password)

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def kb_api_url(self) -> str:
        return f'{self._base_url}{self._kb_api_path}'

    def _kb_url(self, path: str) -> str:
        return f'{self._base_url}{self._kb_api_path}/{path.lstrip("/")}'

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> 'TopdeskClient':
        await self._get_client()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Low-level request helper (shared by all KB GETs + the probe)
    # ------------------------------------------------------------------

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        accept: str = _ACCEPT_JSON,
        max_retries: int = 3,
    ) -> httpx.Response:
        """Make an authenticated request with retry logic.

        Handles:
        - 401: terminal — raises TopdeskAuthError (no token to refresh).
        - 429: respects Retry-After header, then retries.
        - 5xx: exponential backoff (capped), then retries.
        - httpx.ConnectError: surfaced as a friendly ConnectionError.

        Any other status (2xx/206, 400, 404, …) is returned for the caller to
        interpret.
        """
        client = await self._get_client()
        last_exception: Optional[Exception] = None
        last_status: Optional[int] = None

        for attempt in range(max_retries):
            is_final_attempt = attempt == max_retries - 1
            try:
                headers = {**self._auth_header, 'Accept': accept}
                if json_body is not None:
                    headers['Content-Type'] = 'application/json'
                response = await client.request(method, url, params=params, json=json_body, headers=headers)

                # Static credential — a 401 is terminal, nothing to refresh.
                if response.status_code == 401:
                    raise TopdeskAuthError(
                        'TOPdesk rejected the service credential (401). Check the '
                        'TOPdesk URL, operator login and application password.'
                    )

                if response.status_code == 429:
                    last_status = 429
                    if is_final_attempt:
                        # About to give up — don't burn the Retry-After window.
                        continue
                    retry_after = int(response.headers.get('Retry-After', '60'))
                    log.warning('TOPdesk rate limited, waiting %d seconds', retry_after)
                    await asyncio.sleep(retry_after)
                    continue

                if response.status_code >= 500:
                    last_status = response.status_code
                    if is_final_attempt:
                        # About to give up — skip the backoff sleep.
                        continue
                    wait_time = min(2**attempt, 60)
                    log.warning(
                        'TOPdesk server error %d, retrying in %d seconds',
                        response.status_code,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                    continue

                return response

            except TopdeskAuthError:
                # Terminal — do not retry.
                raise
            except (httpx.HTTPStatusError, httpx.TransportError) as e:
                last_exception = e
                if attempt < max_retries - 1:
                    wait_time = min(2**attempt, 60)
                    log.warning(
                        'TOPdesk request failed (attempt %d/%d): %s — retrying in %ds',
                        attempt + 1,
                        max_retries,
                        e,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                else:
                    if isinstance(e, httpx.ConnectError):
                        raise ConnectionError(
                            'Unable to reach TOPdesk. Please check the TOPdesk URL and network connectivity.'
                        ) from e
                    raise

        # Retries exhausted on a transient 429/5xx — surface a typed error carrying
        # the last HTTP status so the router can map it to a clean 502/503.
        raise TopdeskTransientError(
            f'TOPdesk request failed after {max_retries} retries (last status {last_status})',
            status_code=last_status,
        )

    def _raise_api_error(self, response: httpx.Response) -> None:
        """Parse a ``{errors:[...]}`` body and raise TopdeskApiError.

        Surfaces the first ``errorMessage``; falls back to a status-based message
        when the body is not the expected error envelope.
        """
        payload = _safe_json(response)
        errors = payload.get('errors') if isinstance(payload, dict) else None
        message: Optional[str] = None
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                message = first.get('errorMessage')
        raise TopdeskApiError(
            message or f'TOPdesk API error (HTTP {response.status_code})',
            errors if isinstance(errors, list) else [],
        )

    # ------------------------------------------------------------------
    # Knowledge items — listing + offset pagination
    # ------------------------------------------------------------------

    async def list_knowledge_items(
        self,
        *,
        start: int = 0,
        page_size: int = _DEFAULT_PAGE_SIZE,
        query: Optional[str] = None,
        language: Optional[str] = None,
        fields: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fetch one page of knowledge items.

        ``query`` is a FIQL expression (e.g. ``parent.id==<id>``);
        ``page_size`` is clamped to the API max (1000); ``fields`` overrides the
        default ``_KI_FIELDS`` selector. Returns the raw ``{item, prev?, next?}``
        page object (200 = complete, 206 = partial).
        """
        params: Dict[str, Any] = {
            'start': max(0, start),
            'page_size': max(1, min(page_size, _PAGE_SIZE_MAX)),
            'fields': fields or _KI_FIELDS,
        }
        if query:
            params['query'] = query
        if language:
            params['language'] = language

        response = await self._request_with_retry(
            'GET', self._kb_url('knowledgeItems'), params=params, accept=_ACCEPT_LIST
        )
        if response.status_code == 400:
            self._raise_api_error(response)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {'item': []}

    async def iter_all_knowledge_items(
        self,
        *,
        query: Optional[str] = None,
        language: Optional[str] = None,
        fields: Optional[str] = None,
        page_size: int = _PAGE_SIZE_MAX,
        max_pages: int = _MAX_PAGES,
    ) -> List[Dict[str, Any]]:
        """Eagerly collect every knowledge item across all pages, sequentially.

        Bounded sequential offset paging — no parallel fan-out (ITSM politeness).
        Terminates when the server reports no ``next``, a short/empty page, or the
        ``max_pages`` safety cap (logged).
        """
        out: List[Dict[str, Any]] = []
        start = 0
        for page in range(max_pages):
            data = await self.list_knowledge_items(
                start=start, page_size=page_size, query=query, language=language, fields=fields
            )
            items = data.get('item') or []
            out.extend(items)
            # Stop on the server's end-of-list signal or a short/empty page.
            if not data.get('next') or len(items) < page_size:
                return out
            start += page_size

        log.warning('TOPdesk pagination hit the %d-page safety cap; results may be truncated', max_pages)
        return out

    # ------------------------------------------------------------------
    # Knowledge items — single item + tree traversal
    # ------------------------------------------------------------------

    async def get_knowledge_item(
        self,
        identifier: str,
        *,
        language: Optional[str] = None,
        fields: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Fetch a single knowledge item by id or number (e.g. ``KI 0211``).

        Always requests ``_KI_FIELDS`` (incl. the HTML content) so the content
        block is populated. Returns the item dict, or None if it does not exist
        (404).
        """
        params: Dict[str, Any] = {'fields': fields or _KI_FIELDS}
        if language:
            params['language'] = language

        response = await self._request_with_retry(
            'GET', self._kb_url(f'knowledgeItems/{identifier}'), params=params, accept=_ACCEPT_ITEM
        )
        if response.status_code == 404:
            return None
        if response.status_code == 400:
            self._raise_api_error(response)
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else None

    async def list_item_children(
        self,
        parent_id: str,
        *,
        language: Optional[str] = None,
        fields: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return the direct child items of a knowledge item via FIQL parent.id."""
        return await self.iter_all_knowledge_items(query=f'parent.id=={parent_id}', language=language, fields=fields)

    async def list_root_items(
        self,
        *,
        language: Optional[str] = None,
        fields: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return the top-level (parent-less) knowledge items.

        FIQL for "no parent" (``parent.id==null``) is unconfirmed against the live
        API, so this fail-safes: enumerate all items and filter on the absence of a
        ``parent`` client-side. ``parent`` is forced into the field set regardless
        of the caller's ``fields`` so the filter always has the data it needs. For a
        flat KB (the Intermax v1 case) every item is a root, matching the old
        flat-listing behaviour.
        """
        fset = fields or _KI_FIELDS
        if 'parent' not in fset:
            fset = f'{fset},parent'
        items = await self.iter_all_knowledge_items(language=language, fields=fset)
        return [i for i in items if not i.get('parent')]

    # ------------------------------------------------------------------
    # Connection probe
    # ------------------------------------------------------------------

    async def probe(self) -> Dict[str, Any]:
        """Lightweight authenticated connection probe.

        Tries ``GET /tas/api/version`` (confirms auth + reachability); if that is
        unavailable, falls back to a minimal KB list (which also confirms the
        KB-v1 feature is enabled). Returns a dict the router can use to report
        ok/detail: ``{'ok': bool, 'probe': '<endpoint>', 'detail': <body or message>}``.
        Raises TopdeskAuthError on 401 (terminal).
        """
        version_url = f'{self._base_url}/tas/api/version'
        response = await self._request_with_retry('GET', version_url, accept=_ACCEPT_JSON)
        if response.is_success:
            return {'ok': True, 'probe': 'version', 'detail': _safe_json(response)}

        # Version endpoint unavailable (e.g. 404 on this tenant) — fall back to a
        # one-item KB list. A 401 already raised above; a KB API error here means
        # auth worked but the KB-v1 feature is off / unreachable.
        try:
            data = await self.list_knowledge_items(page_size=1)
        except TopdeskApiError as e:
            return {'ok': False, 'probe': 'knowledgeItems', 'detail': str(e)}
        return {
            'ok': True,
            'probe': 'knowledgeItems',
            'detail': {'returned': len(data.get('item') or [])},
        }


# =====================================================================
# Parsing helpers
# =====================================================================


def _safe_json(response: httpx.Response) -> Any:
    """Return parsed JSON, or the raw (truncated) text if the body is not JSON."""
    try:
        return response.json()
    except ValueError:
        return (response.text or '')[:500]
