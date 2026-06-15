#!/usr/bin/env python3
"""TOPdesk capability discovery — try every auth form × every KB API.

For a *custom* tenant where we don't yet know (a) which auth the credential is,
or (b) which of the THREE possible Knowledge Base APIs the tenant exposes. It
builds a result matrix so you can see exactly what the tenant accepts.

The three KB APIs (in order of preference for our integration):
  1. REST SaaS  — GET /services/knowledge-base-v1/knowledgeItems   (OAS 3.0;
     rich: parent/status/visibility/modificationDate/urls/keywords, HTML
     content, FIQL filtering, start/page_size paging) — the one we want.
  2. GraphQL    — POST /tas/api/knowledgeBase/public               (OAS 2.0;
     minimal SSP: id/number/translations{title,content plain-text}/languages).
  3. Legacy     — GET /tas/api/knowledgeItems                      (old REST KB,
     removed in 2025 R2; reachable on older tenants).

Auth forms tried:
  - direct token:  Authorization: TOKEN id="<secret>"            (no login)
  - modern Basic:  Authorization: Basic base64(login:secret)     (per --login)
  - legacy 2-step: GET /tas/api/login/operator (and /login/person) with Basic
                   base64(login:secret) → hex token → TOKEN id="<token>"
                   (the pre-Nov-2017 method; per --login)

Logins are NEVER guessed (a wrong-password storm could lock a production
operator). Pass them with --login.

────────────────────────────────────────────────────────────────────────────
STRICTLY READ-ONLY: GET + GraphQL *queries* only (the legacy login GET returns a
token, it does not mutate). No POST/PUT/PATCH/DELETE to data. Sequential,
short-timeout, polite. Never prints the secret. Safe against production.
────────────────────────────────────────────────────────────────────────────

Usage:
  export TOPDESK_URL="https://yourtenant.topdesk.net"
  export TOPDESK_APP_PASSWORD="<the secret>"
  /path/to/.venv/bin/python scripts/topdesk_discover.py            # token-only
  python scripts/topdesk_discover.py --login api-account           # + Basic/2-step
  python scripts/topdesk_discover.py --login "api,integration,svc"
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
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


def _short(text: str, limit: int = 150) -> str:
    text = ' '.join((text or '').split())
    return text if len(text) <= limit else text[:limit] + '…'


def _body_summary(ctype: str, text: str) -> str:
    """Surface a useful snippet. For HTML, pull the <title> + de-tagged text so a
    WAF / IP-block / login page is distinguishable from a JSON API auth error."""
    if 'json' in ctype:
        return _short(text, 140)
    kind = (ctype.split(';')[0] or '?').strip()
    m = re.search(r'<title[^>]*>(.*?)</title>', text, re.I | re.S)
    title = f'title="{m.group(1).strip()}" ' if m else ''
    detagged = _short(re.sub(r'<[^>]+>', ' ', text), 110)
    return f'[{len(text)}B {kind}] {title}{detagged}'


def _basic(login: str, secret: str) -> str:
    return 'Basic ' + base64.b64encode(f'{login}:{secret}'.encode()).decode()


# Rich REST SaaS KB query — request a useful field set so we see real shape.
_KBV1_PATH = (
    '/services/knowledge-base-v1/knowledgeItems'
    '?page_size=1&fields=number,status,parent,modificationDate,language,content,urls'
)
_KBV1_ACCEPT = 'application/x.topdesk-kb-ki-list-v1+json'

# Minimal GraphQL query (public realm). Response wrapped as {"results": {...}}.
_GQL_QUERY = (
    'query { knowledgeItems(search: {term: ""}) '
    '{ id number translations { languageId title content } } '
    'languages { id name languageCode } }'
)

# (label, method, path, graphql_query_or_None, accept_override_or_None)
ENDPOINTS = [
    ('REST   version', 'GET', '/tas/api/version', None, None),
    ('REST   operators/current', 'GET', '/tas/api/operators/current', None, None),
    ('REST   persons/current', 'GET', '/tas/api/persons/current', None, None),
    ('KB-v1  knowledgeItems (REST SaaS) ★', 'GET', _KBV1_PATH, None, _KBV1_ACCEPT),
    ('LEGACY knowledgeItems (old REST)', 'GET', '/tas/api/knowledgeItems?page_size=1', None, None),
    ('GQL    knowledgeBase/public', 'POST', '/tas/api/knowledgeBase/public', _GQL_QUERY, None),
]


def classify(status: int, ctype: str, body: str) -> tuple[str, str]:
    is_json = 'json' in ctype
    if status in (200, 206):
        return (_GREEN, f'{status} OK')
    if status == 204:
        return (_YEL, '204 empty')
    if status == 401:
        return (_RED, '401 auth')
    if status == 403:
        return (_YEL, '403 forbid')
    if status == 404:
        return (_DIM, '404 absent')
    if status == 400 and is_json and 'error' in body.lower():
        return (_YEL, '400 api-err')
    return (_YEL, f'{status}')


def request(client, base, headers, method, path, query, accept):
    url = base + path
    h = dict(headers)
    h['Accept'] = accept or 'application/json'
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
    return (r.status_code, ctype, _body_summary(ctype, r.text))


def legacy_login(client, base, login: str, secret: str, kind: str) -> tuple[Optional[str], str]:
    """Old 2-step: Basic to /tas/api/login/{operator|person} → hex token.
    Returns (token_or_None, human_status)."""
    url = f'{base}/tas/api/login/{kind}'
    try:
        r = client.get(url, headers={'Authorization': _basic(login, secret)})
    except httpx.HTTPError as e:
        return (None, f'transport: {type(e).__name__}')
    if r.status_code == 200 and r.text.strip():
        token = r.text.strip().strip('"')
        return (token, f'200 → token {token[:8]}…')
    return (None, f'{r.status_code} {_body_summary(r.headers.get("content-type", ""), r.text)}')


def build_strategies(client, base, secret: str, logins: list[str]) -> list[tuple[str, dict]]:
    """Resolve every auth header we can build (incl. legacy 2-step token flow)."""
    strategies: list[tuple[str, dict]] = [
        ('direct TOKEN id="…"', {'Authorization': f'TOKEN id="{secret}"'}),
    ]
    for login in logins:
        strategies.append((f'modern Basic (login="{login}")', {'Authorization': _basic(login, secret)}))
        for kind in ('operator', 'person'):
            token, note = legacy_login(client, base, login, secret, kind)
            print(f'  {_DIM}· legacy login/{kind} (login="{login}"): {note}{_RST}')
            if token:
                strategies.append(
                    (f'legacy TOKEN via {kind} (login="{login}")', {'Authorization': f'TOKEN id="{token}"'})
                )
            time.sleep(0.3)
    return strategies


def main() -> int:
    ap = argparse.ArgumentParser(description='Read-only TOPdesk auth × KB-API discovery matrix.')
    ap.add_argument('--url', default=os.environ.get('TOPDESK_URL', ''))
    ap.add_argument('--token', default=os.environ.get('TOPDESK_APP_PASSWORD', ''), help='the secret value')
    ap.add_argument(
        '--login',
        default=os.environ.get('TOPDESK_LOGIN', ''),
        help='operator login name(s) for Basic / legacy auth; comma-separated. Never guessed.',
    )
    ap.add_argument('--timeout', type=float, default=20.0)
    args = ap.parse_args()

    if not args.url or not args.token:
        print('error: provide --url and --token (or TOPDESK_URL / TOPDESK_APP_PASSWORD).', file=sys.stderr)
        return 2

    base = args.url.strip().rstrip('/')
    secret = args.token.strip()
    logins = [x.strip() for x in args.login.split(',') if x.strip()]

    print(f'{_BOLD}TOPdesk discovery matrix{_RST}')
    print(f'  tenant : {base}')
    print(f'  secret : {_DIM}(set, {len(secret)} chars — not shown){_RST}')
    print(f'  logins : {logins or "(none — only direct TOKEN tried; pass --login for Basic + legacy 2-step)"}')
    print('  mode   : READ-ONLY (GET + GraphQL queries only)')

    findings: list[str] = []
    with httpx.Client(timeout=args.timeout, follow_redirects=False) as client:
        if logins:
            print(f'\n{_BOLD}{_BLU}── Resolving auth strategies (incl. legacy 2-step) {"─" * 18}{_RST}')
        strategies = build_strategies(client, base, secret, logins)

        for strat_label, headers in strategies:
            print(f'\n{_BOLD}{_BLU}── Auth: {strat_label} {"─" * max(0, 46 - len(strat_label))}{_RST}')
            for ep_label, method, path, query, accept in ENDPOINTS:
                status, ctype, body = request(client, base, headers, method, path, query, accept)
                if status is None:
                    print(f'  {_RED}✗{_RST} {ep_label:<38} transport: {_short(body, 70)}')
                    time.sleep(0.3)
                    continue
                colour, verdict = classify(status, ctype, body)
                print(f'  {colour}{verdict:<11}{_RST} {ep_label:<38} {_DIM}{_short(body, 170)}{_RST}')
                if status in (200, 206):
                    findings.append(f'{strat_label}  →  {ep_label}')
                time.sleep(0.3)

    print(f'\n{_BOLD}── Summary {"─" * 52}{_RST}')
    if findings:
        print(f'  {_GREEN}Working (2xx) combinations:{_RST}')
        for f in findings:
            print(f'    ✓ {f}')
        print(f'\n  {_DIM}Prefer a KB-v1 (REST SaaS) hit — it has parent/status/modificationDate/')
        print(f'  urls/HTML. A GraphQL-only hit means the minimal SSP schema. A LEGACY hit')
        print(f'  means a pre-2025-R2 tenant.{_RST}')
    else:
        print(f'  {_YEL}No combination returned 2xx.{_RST}')
        print('    - Only direct TOKEN tried → rerun with --login <operatorLogin>.')
        print('    - Basic + legacy both 401 → wrong login, or KB needs a person/SSP token.')
        print('    - KB-v1 404 but identity 200 → the REST SaaS KB API may need the')
        print('      feature flag enabled (ask the application manager).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
