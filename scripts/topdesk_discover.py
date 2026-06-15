#!/usr/bin/env python3
"""TOPdesk capability discovery — try every auth form × every KB endpoint.

For a *custom* tenant where we don't yet know (a) which auth the credential is,
or (b) whether the Knowledge Base API is the new GraphQL one or the legacy REST
one. It builds a result matrix so you can see exactly what the tenant accepts.

It probes, for each auth form it can build:
  - identity/version REST endpoints (which auth class is this credential?)
  - the LEGACY REST Knowledge Base API (/tas/api/knowledgeItems) — present only
    on tenants older than 2025 R2
  - the NEW GraphQL Knowledge Base API (POST /tas/api/knowledgeBase/public, plus
    fallback paths) with a trivial query AND the real knowledgeItems query

Auth forms tried:
  - person token:  Authorization: TOKEN id="<secret>"   (needs no login)
  - HTTP Basic:    Authorization: Basic base64(login:secret)   — ONLY for each
                   --login you pass. Logins are NEVER guessed (a wrong-password
                   storm could lock a production operator account).

────────────────────────────────────────────────────────────────────────────
STRICTLY READ-ONLY: GET requests + GraphQL *queries* only. No mutations, no
POST/PUT/PATCH/DELETE to the REST API. Sequential, short-timeout, polite delays.
Never prints the secret. Safe to run against production.
────────────────────────────────────────────────────────────────────────────

Usage:
  export TOPDESK_URL="https://yourtenant.topdesk.net"
  export TOPDESK_APP_PASSWORD="<the secret>"
  /path/to/.venv/bin/python scripts/topdesk_discover.py
  # once you have the operator login name(s) — comma-separated to try several:
  python scripts/topdesk_discover.py --login api-account
  python scripts/topdesk_discover.py --login "api,integration,svc-openwebui"
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from typing import Optional

try:
    import httpx
except ImportError:
    sys.exit(
        'error: httpx is required. Run with the project venv, e.g.\n'
        '  /Users/lexlubbers/Code/soev/open-webui/.venv/bin/python scripts/topdesk_discover.py'
    )

_BOLD = '\033[1m'
_DIM = '\033[2m'
_GREEN = '\033[32m'
_RED = '\033[31m'
_YEL = '\033[33m'
_BLU = '\033[34m'
_RST = '\033[0m'


def _short(text: str, limit: int = 160) -> str:
    text = ' '.join((text or '').split())
    return text if len(text) <= limit else text[:limit] + '…'


# The real KB GraphQL query (OAS 2.0 spec shape). Response is wrapped as
# {"results": {"knowledgeItems": [...], "languages": [...]}}.
_KB_QUERY = (
    'query { knowledgeItems(search: {term: ""}) '
    '{ id number translations { languageId title content } } '
    'languages { id name languageCode } }'
)

# (label, method, path, graphql_query_or_None). GraphQL endpoints send a POST
# with a {"query": ...} body — still a read-only query, never a mutation.
ENDPOINTS = [
    ('REST  version', 'GET', '/tas/api/version', None),
    ('REST  operators/current', 'GET', '/tas/api/operators/current', None),
    ('REST  persons/current', 'GET', '/tas/api/persons/current', None),
    ('LEGACY knowledgeItems (REST)', 'GET', '/tas/api/knowledgeItems?page_size=1', None),
    ('GQL  knowledgeBase/public  __typename', 'POST', '/tas/api/knowledgeBase/public', '{ __typename }'),
    ('GQL  knowledgeBase/public  knowledgeItems', 'POST', '/tas/api/knowledgeBase/public', _KB_QUERY),
    ('GQL  knowledgeBase/graphql __typename', 'POST', '/tas/api/knowledgeBase/graphql', '{ __typename }'),
    ('GQL  knowledgeBase/read    __typename', 'POST', '/tas/api/knowledgeBase/read', '{ __typename }'),
    ('GQL  knowledgeBase         __typename', 'POST', '/tas/api/knowledgeBase', '{ __typename }'),
]


def auth_forms(secret: str, logins: list[str]) -> list[tuple[str, dict]]:
    """Build every auth header we can. TOKEN needs no login; Basic needs each
    provided login. Logins are never invented here."""
    forms: list[tuple[str, dict]] = [
        ('TOKEN id="…" (person token)', {'Authorization': f'TOKEN id="{secret}"'}),
    ]
    for login in logins:
        enc = base64.b64encode(f'{login}:{secret}'.encode()).decode()
        forms.append((f'Basic (login="{login}")', {'Authorization': f'Basic {enc}'}))
    return forms


def classify(status: int, ctype: str, body: str) -> tuple[str, str]:
    """Return (colour, one-word verdict) for a response."""
    is_json = 'json' in ctype
    if status == 200:
        return (_GREEN, 'OK')
    if status == 401:
        return (_RED, '401 auth')
    if status == 403:
        return (_YEL, '403 forbid')
    if status == 404:
        return (_DIM, '404 absent')
    if status == 400 and is_json and ('errors' in body or 'error' in body):
        # A GraphQL server answering with a query error still means it's THERE.
        return (_YEL, '400 gql-err')
    return (_YEL, f'{status}')


def probe(client: httpx.Client, base: str, headers: dict, method: str, path: str, query: Optional[str]):
    url = base + path
    h = dict(headers)
    h['Accept'] = 'application/json'
    try:
        if method == 'GET':
            r = client.get(url, headers=h)
        else:
            if query and 'mutation' in query.lower():
                raise RuntimeError('refusing to send a GraphQL mutation — read-only')
            h['Content-Type'] = 'application/json'
            r = client.post(url, headers=h, json={'query': query})
    except httpx.HTTPError as e:
        return (None, '', f'{type(e).__name__}: {e}')
    ctype = r.headers.get('content-type', '')
    body = r.text if 'json' in ctype or len(r.text) < 400 else f'<{len(r.text)} bytes {ctype}>'
    return (r.status_code, ctype, body)


def main() -> int:
    ap = argparse.ArgumentParser(description='Read-only TOPdesk auth × endpoint discovery matrix.')
    ap.add_argument('--url', default=os.environ.get('TOPDESK_URL', ''))
    ap.add_argument('--token', default=os.environ.get('TOPDESK_APP_PASSWORD', ''), help='the secret value')
    ap.add_argument('--login', default=os.environ.get('TOPDESK_LOGIN', ''),
                    help='operator login name(s) for Basic auth; comma-separated. Never guessed.')
    ap.add_argument('--timeout', type=float, default=20.0)
    args = ap.parse_args()

    if not args.url or not args.token:
        print('error: provide --url and --token (or TOPDESK_URL / TOPDESK_APP_PASSWORD).', file=sys.stderr)
        return 2

    base = args.url.strip().rstrip('/')
    secret = args.token.strip()
    logins = [x.strip() for x in args.login.split(',') if x.strip()]
    forms = auth_forms(secret, logins)

    print(f'{_BOLD}TOPdesk discovery matrix{_RST}')
    print(f'  tenant : {base}')
    print(f'  secret : {_DIM}(set, {len(secret)} chars — not shown){_RST}')
    print(f'  logins : {logins or "(none — only TOKEN form tried; pass --login to add Basic)"}')
    print('  mode   : READ-ONLY (GET + GraphQL queries only)')

    findings: list[str] = []

    with httpx.Client(timeout=args.timeout, follow_redirects=False) as client:
        for form_label, headers in forms:
            print(f'\n{_BOLD}{_BLU}── Auth: {form_label} {"─" * max(0, 48 - len(form_label))}{_RST}')
            for ep_label, method, path, query in ENDPOINTS:
                status, ctype, body = probe(client, base, headers, method, path, query)
                if status is None:
                    print(f'  {_RED}✗{_RST} {ep_label:<42} transport: {_short(body, 80)}')
                    time.sleep(0.3)
                    continue
                colour, verdict = classify(status, ctype, body)
                print(f'  {colour}{verdict:<11}{_RST} {ep_label:<42} {_DIM}{_short(body, 120)}{_RST}')
                if status == 200:
                    findings.append(f'{form_label}  →  {ep_label}')
                time.sleep(0.3)

    print(f'\n{_BOLD}── Summary {"─" * 50}{_RST}')
    if findings:
        print(f'  {_GREEN}Working (HTTP 200) combinations:{_RST}')
        for f in findings:
            print(f'    ✓ {f}')
    else:
        print(f'  {_YEL}No combination returned 200.{_RST}')
        print('    - If only TOKEN was tried: the credential is likely an operator')
        print('      application password → rerun with --login <operatorLogin> for Basic.')
        print('    - If Basic was tried and still 401: wrong login, or the KB API needs')
        print('      a person/SSP token rather than an operator credential.')
    print(f'\n  {_DIM}Read the LEGACY vs GQL rows: a 200 on LEGACY knowledgeItems → pre-2025-R2')
    print(f'  tenant (legacy REST KB still available). A 200 / 400-gql-err on a')
    print(f'  knowledgeBase/* POST → the GraphQL KB API is the path.{_RST}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
