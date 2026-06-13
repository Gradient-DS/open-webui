"""TOPdesk Knowledge Base GraphQL API client.

Async httpx wrapper for the TOPdesk Knowledge Base GraphQL endpoint, used by the
service-account sync. Talks to the customer tenant directly
(`{TOPDESK_URL}{TOPDESK_GRAPHQL_PATH}`) with a static auth header (Basic or
person-token — see ``services/topdesk/auth.py``). The credential never
refreshes, so a 401 is terminal.

Modelled on ``services/confluence/confluence_client.py`` (async httpx,
retry/backoff, 401-terminal). Two key differences from the Confluence client:

1. The data surface is a **POST GraphQL** body (`{query, variables}`), not REST
   GETs. A GraphQL query-level failure arrives as **HTTP 200 with a top-level
   `errors` array** (findings §4.0) — this client treats that as a FAILURE and
   raises ``TopdeskGraphQLError`` (it never returns a 200-with-errors body as
   success).
2. Pagination is the Relay `first`/`after` + `pageInfo{hasNextPage,endCursor}`
   cursor model carried in the query body, not a `_links.next` URL.

The lightweight connection probe (``probe``) uses the REST surface
(`/tas/api/operators/current`, falling back to `/tas/api/version`).

SCHEMA-UNCERTAINTY DESIGN RULE
------------------------------
The GraphQL schema is **inferred** — live verification is pending (see
thoughts/shared/research/2026-06-topdesk-api-verification.md §4/§5 and its
"REQUIRES LIVE VERIFICATION" checklist). ALL GraphQL query strings and
field-name constants are centralised in the "GraphQL schema (INFERRED)" section
below so a live-verification correction is a one-place edit. Parsing helpers
reference these constants rather than hard-coding field names inline.
"""

import asyncio
import logging
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx

log = logging.getLogger(__name__)


# =====================================================================
# GraphQL schema (INFERRED — single source of truth for field names)
# =====================================================================
# Every TOPdesk-specific query string and GraphQL field name lives in this
# section. If live introspection reveals different names (different query name,
# pagination args, filter comparator, or node fields), edit ONLY this block —
# the request/parse code below references these constants. See findings §4–§5.

# --- Top-level query / connection / node field names -----------------
FIELD_KNOWLEDGE_ITEMS = 'knowledgeItems'  # Query.knowledgeItems (the connection)
FIELD_KNOWLEDGE_ITEM = 'knowledgeItem'  # Query.knowledgeItem(id) (single item)
FIELD_EDGES = 'edges'
FIELD_NODE = 'node'
FIELD_CURSOR = 'cursor'
FIELD_PAGE_INFO = 'pageInfo'
FIELD_HAS_NEXT_PAGE = 'hasNextPage'
FIELD_END_CURSOR = 'endCursor'
FIELD_TOTAL_COUNT = 'totalCount'
FIELD_CHILDREN = 'children'

# --- modificationDate filter comparator key (guessed — findings §5.2) ---
# The comparator key (gte / since / modifiedAfter / after) is unconfirmed; isolate
# it here so a live-verification correction is one line.
FILTER_MODIFICATION_DATE = 'modificationDate'
FILTER_MODIFICATION_DATE_COMPARATOR = 'gte'
FILTER_STATUS = 'status'

# Default page size for list pagination.
_DEFAULT_PAGE_SIZE = 50

# Hard safety cap on the number of pages the pagination loop will walk, so a
# misbehaving server (e.g. a cursor that never advances) cannot spin forever.
_MAX_PAGES = 200

# KnowledgeItem node selection set used for the list query. Kept lean for
# pagination; full content is hydrated per-item via get_knowledge_item.
_NODE_SUMMARY_FIELDS = """
  id
  number
  title
  description
  keywords
  language
  status
  visibility
  availableTranslations { language title status }
  creationDate
  modificationDate
"""

# Full node selection including the HTML body + parent breadcrumb. Used by
# get_knowledge_item for content hydration. (Attachments are read but ignored in
# v1 — findings §6; the selection omits them to keep payloads small.)
_NODE_FULL_FIELDS = (
    _NODE_SUMMARY_FIELDS
    + """
  content
  parent { id number title }
"""
)

# Child-node selection for tree traversal (findings §5.3).
_CHILD_FIELDS = """
  id
  number
  title
  language
  status
  modificationDate
"""

QUERY_LIST_KNOWLEDGE_ITEMS = """
query ListKnowledgeItems($first: Int!, $after: String, $filter: KnowledgeItemFilter) {
  %s(first: $first, after: $after, filter: $filter) {
    %s
    %s {
      %s
      %s { %s }
    }
    %s { %s %s }
  }
}
""" % (
    FIELD_KNOWLEDGE_ITEMS,
    FIELD_TOTAL_COUNT,
    FIELD_EDGES,
    FIELD_CURSOR,
    FIELD_NODE,
    _NODE_SUMMARY_FIELDS,
    FIELD_PAGE_INFO,
    FIELD_HAS_NEXT_PAGE,
    FIELD_END_CURSOR,
)

QUERY_GET_KNOWLEDGE_ITEM = """
query GetKnowledgeItem($id: ID!) {
  %s(id: $id) {
    %s
  }
}
""" % (FIELD_KNOWLEDGE_ITEM, _NODE_FULL_FIELDS)

# Variant without the heavy `content` field (metadata-only hydration).
QUERY_GET_KNOWLEDGE_ITEM_NO_CONTENT = """
query GetKnowledgeItem($id: ID!) {
  %s(id: $id) {
    %s
    parent { id number title }
  }
}
""" % (FIELD_KNOWLEDGE_ITEM, _NODE_SUMMARY_FIELDS)

QUERY_LIST_ITEM_CHILDREN = """
query ListItemChildren($id: ID!) {
  %s(id: $id) {
    id
    number
    title
    %s { %s }
  }
}
""" % (FIELD_KNOWLEDGE_ITEM, FIELD_CHILDREN, _CHILD_FIELDS)


def _build_filter(status: Optional[str], modified_since: Optional[str]) -> Optional[Dict[str, Any]]:
    """Compose the GraphQL `filter` input object from the supported predicates.

    Returns None when no predicate is set (so the query omits filtering entirely).
    See findings §5.2 — the modificationDate comparator key is unconfirmed and
    centralised in FILTER_MODIFICATION_DATE_COMPARATOR.
    """
    flt: Dict[str, Any] = {}
    if status:
        flt[FILTER_STATUS] = status
    if modified_since:
        flt[FILTER_MODIFICATION_DATE] = {FILTER_MODIFICATION_DATE_COMPARATOR: modified_since}
    return flt or None


# =====================================================================
# Errors
# =====================================================================


class TopdeskAuthError(Exception):
    """Raised on a terminal 401 — the service credential is invalid.

    Service-account mode has no token to refresh, so a 401 is not retried.
    """


class TopdeskGraphQLError(Exception):
    """Raised when a GraphQL response carries a top-level `errors` array.

    GraphQL query-level failures arrive as HTTP 200 with `errors` (findings
    §4.0); they are FAILURES, never success. The first error message is surfaced.
    """

    def __init__(self, message: str, errors: List[Dict[str, Any]]):
        super().__init__(message)
        self.errors = errors


# =====================================================================
# Client
# =====================================================================


class TopdeskClient:
    """Async client for the TOPdesk Knowledge Base GraphQL API with retry logic."""

    def __init__(
        self,
        base_url: str = '',
        username: str = '',
        app_password: str = '',
        *,
        graphql_path: Optional[str] = None,
        page_size: int = _DEFAULT_PAGE_SIZE,
    ):
        self._base_url = (base_url or '').rstrip('/')
        self._username = (username or '').strip()
        self._app_password = app_password or ''
        self._page_size = page_size
        # Resolve the GraphQL path lazily-but-once; config is the default source.
        if graphql_path is None:
            from open_webui.config import TOPDESK_GRAPHQL_PATH

            graphql_path = TOPDESK_GRAPHQL_PATH
        self._graphql_path = '/' + (graphql_path or '').lstrip('/')
        self._client: Optional[httpx.AsyncClient] = None
        # Auth header is static — precompute it once. Built via the auth helper so
        # the Basic-vs-TOKEN rule lives in one place.
        from open_webui.services.topdesk.auth import build_auth_header

        self._auth_header = build_auth_header(self._username, self._app_password)

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def graphql_url(self) -> str:
        return f'{self._base_url}{self._graphql_path}'

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Low-level request helper (shared by GraphQL POST + REST probe GETs)
    # ------------------------------------------------------------------

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
    ) -> httpx.Response:
        """Make an authenticated request with retry logic.

        Handles:
        - 401: terminal — raises TopdeskAuthError (no token to refresh).
        - 429: respects Retry-After header, then retries.
        - 5xx: exponential backoff (capped), then retries.
        - httpx.ConnectError: surfaced as a friendly ConnectionError.
        """
        client = await self._get_client()
        last_exception: Optional[Exception] = None

        for attempt in range(max_retries):
            try:
                headers = {**self._auth_header, 'Accept': 'application/json'}
                if json_body is not None:
                    headers['Content-Type'] = 'application/json'
                response = await client.request(method, url, json=json_body, headers=headers)

                # Static credential — a 401 is terminal, nothing to refresh.
                if response.status_code == 401:
                    raise TopdeskAuthError(
                        'TOPdesk rejected the service credential (401). Check the '
                        'TOPdesk URL, operator login name and application password.'
                    )

                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', '60'))
                    log.warning('TOPdesk rate limited, waiting %d seconds', retry_after)
                    await asyncio.sleep(retry_after)
                    continue

                if response.status_code >= 500:
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

        raise RuntimeError(f'TOPdesk request failed after {max_retries} retries: {last_exception}')

    # ------------------------------------------------------------------
    # GraphQL
    # ------------------------------------------------------------------

    async def graphql(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """POST a GraphQL query and return the ``data`` value.

        CRITICAL (findings §4.0): an HTTP 200 whose body carries a top-level
        ``errors`` array is a FAILURE — this raises TopdeskGraphQLError (with the
        first error's message). A 200-with-errors response is never treated as
        success. Returns the ``data`` object on success.
        """
        body: Dict[str, Any] = {'query': query, 'variables': variables or {}}
        response = await self._request_with_retry('POST', self.graphql_url, json_body=body)
        # Non-200/4xx (other than the 401 already handled above) — surface clearly.
        response.raise_for_status()
        payload = response.json()

        errors = payload.get('errors')
        if errors:
            first = errors[0] if isinstance(errors, list) and errors else {}
            message = (first.get('message') if isinstance(first, dict) else None) or 'TOPdesk GraphQL query failed'
            raise TopdeskGraphQLError(message, errors if isinstance(errors, list) else [])

        return payload.get('data') or {}

    # ------------------------------------------------------------------
    # Knowledge items — listing + pagination
    # ------------------------------------------------------------------

    async def list_knowledge_items(
        self,
        modified_since: Optional[str] = None,
        page_cursor: Optional[str] = None,
        page_size: Optional[int] = None,
        status: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str], bool]:
        """Fetch one page of knowledge items (Relay `first`/`after`).

        Returns ``(nodes, end_cursor, has_next_page)``. ``modified_since`` (ISO-8601
        string) applies the incremental `modificationDate` filter; ``status`` applies
        the published/status filter (findings §5). The cursor for the next page is
        ``end_cursor`` when ``has_next_page`` is True.
        """
        variables: Dict[str, Any] = {
            'first': page_size or self._page_size,
            'after': page_cursor,
        }
        flt = _build_filter(status, modified_since)
        if flt is not None:
            variables['filter'] = flt

        data = await self.graphql(QUERY_LIST_KNOWLEDGE_ITEMS, variables)
        connection = data.get(FIELD_KNOWLEDGE_ITEMS) or {}
        nodes = _nodes_from_connection(connection)
        page_info = connection.get(FIELD_PAGE_INFO) or {}
        end_cursor = page_info.get(FIELD_END_CURSOR)
        has_next = bool(page_info.get(FIELD_HAS_NEXT_PAGE))
        return nodes, end_cursor, has_next

    async def iter_knowledge_items(
        self,
        modified_since: Optional[str] = None,
        page_size: Optional[int] = None,
        status: Optional[str] = None,
        max_pages: int = _MAX_PAGES,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Yield every knowledge item across all pages, sequentially.

        Bounded sequential pagination — no parallel fan-out (ITSM politeness).
        Terminates when ``hasNextPage`` is False, when a page yields no
        advancing cursor, or when the ``max_pages`` safety cap is hit (logged).
        """
        cursor: Optional[str] = None
        for page in range(max_pages):
            nodes, end_cursor, has_next = await self.list_knowledge_items(
                modified_since=modified_since,
                page_cursor=cursor,
                page_size=page_size,
                status=status,
            )
            for node in nodes:
                yield node

            if not has_next:
                return
            if not end_cursor or end_cursor == cursor:
                # Cursor did not advance — stop rather than loop forever.
                log.warning('TOPdesk pagination cursor did not advance; stopping at page %d', page)
                return
            cursor = end_cursor

        log.warning('TOPdesk pagination hit the %d-page safety cap; results may be truncated', max_pages)

    async def list_all_knowledge_items(
        self,
        modified_since: Optional[str] = None,
        page_size: Optional[int] = None,
        status: Optional[str] = None,
        max_pages: int = _MAX_PAGES,
    ) -> List[Dict[str, Any]]:
        """Eagerly collect every knowledge item across all pages into a list."""
        return [
            node
            async for node in self.iter_knowledge_items(
                modified_since=modified_since,
                page_size=page_size,
                status=status,
                max_pages=max_pages,
            )
        ]

    # ------------------------------------------------------------------
    # Knowledge items — single item + tree traversal
    # ------------------------------------------------------------------

    async def get_knowledge_item(self, item_id: str, include_content: bool = True) -> Optional[Dict[str, Any]]:
        """Fetch a single knowledge item by id.

        ``include_content=True`` hydrates the HTML ``content`` body; False fetches
        metadata only. Returns the node dict, or None if the item does not exist
        (GraphQL resolves an absent item to ``null``).
        """
        query = QUERY_GET_KNOWLEDGE_ITEM if include_content else QUERY_GET_KNOWLEDGE_ITEM_NO_CONTENT
        data = await self.graphql(query, {'id': item_id})
        return data.get(FIELD_KNOWLEDGE_ITEM)

    async def list_item_children(self, item_id: str) -> List[Dict[str, Any]]:
        """Return the direct child items of a knowledge item (findings §5.3)."""
        data = await self.graphql(QUERY_LIST_ITEM_CHILDREN, {'id': item_id})
        item = data.get(FIELD_KNOWLEDGE_ITEM) or {}
        return list(item.get(FIELD_CHILDREN) or [])

    async def list_root_items(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return all knowledge items (flat).

        v1 (Intermax: a single flat shared KB) treats the KB as flat, so this is an
        alias for the full paginated listing. Subtree traversal, when needed, walks
        from these via ``list_item_children``.
        """
        return await self.list_all_knowledge_items(status=status)

    # ------------------------------------------------------------------
    # Connection probe (REST surface — findings §2.2)
    # ------------------------------------------------------------------

    async def probe(self) -> Dict[str, Any]:
        """Lightweight authenticated connection probe.

        Tries ``GET /tas/api/operators/current`` (confirms auth + operator identity
        class); on 404 falls back to ``GET /tas/api/version``. Returns a dict the
        router can use to report ok/detail:
        ``{'ok': bool, 'probe': '<endpoint>', 'detail': <body or message>}``.
        Raises TopdeskAuthError on 401 (terminal).
        """
        operators_url = f'{self._base_url}/tas/api/operators/current'
        response = await self._request_with_retry('GET', operators_url)
        if response.status_code == 404:
            # Endpoint not present on this tenant — fall back to version.
            version_url = f'{self._base_url}/tas/api/version'
            version_resp = await self._request_with_retry('GET', version_url)
            if version_resp.is_success:
                return {
                    'ok': True,
                    'probe': 'version',
                    'detail': _safe_json(version_resp),
                }
            return {
                'ok': False,
                'probe': 'version',
                'detail': f'TOPdesk version probe returned HTTP {version_resp.status_code}',
            }
        if response.is_success:
            return {
                'ok': True,
                'probe': 'operators/current',
                'detail': _safe_json(response),
            }
        return {
            'ok': False,
            'probe': 'operators/current',
            'detail': f'TOPdesk operator probe returned HTTP {response.status_code}',
        }


# =====================================================================
# Parsing helpers (reference the centralised field constants above)
# =====================================================================


def _nodes_from_connection(connection: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract the node dicts from a Relay connection's ``edges``."""
    edges = connection.get(FIELD_EDGES) or []
    nodes: List[Dict[str, Any]] = []
    for edge in edges:
        node = (edge or {}).get(FIELD_NODE)
        if node is not None:
            nodes.append(node)
    return nodes


def _safe_json(response: httpx.Response) -> Any:
    """Return parsed JSON, or the raw (truncated) text if the body is not JSON."""
    try:
        return response.json()
    except ValueError:
        return (response.text or '')[:500]
