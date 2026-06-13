#!/usr/bin/env python3
"""TOPdesk read-only connectivity + Knowledge-Base GraphQL probe.

Validates a TOPdesk **URL + API token** (and, optionally, an operator login)
against a *live* tenant so we can confirm the Phase-0 assumptions before
trusting the integration: which auth form works, the real GraphQL endpoint
path, the actual schema (field names / pagination), and whether knowledge
items are reachable with the credential you hold.

Background: thoughts/shared/research/2026-06-topdesk-api-verification.md

────────────────────────────────────────────────────────────────────────────
STRICTLY READ-ONLY. The script only ever issues:
  • HTTP GET requests (the REST probes), and
  • GraphQL *queries* (introspection + read queries).
It refuses to send any GraphQL `mutation` and makes no POST/PUT/PATCH/DELETE to
the REST API. Requests are sequential, low-volume, and short-timeout — safe to
run against production. It never prints your secret.
────────────────────────────────────────────────────────────────────────────

Auth form (matches the integration's own logic):
  • If an operator login IS given  → HTTP Basic  base64(login:token)
  • If no login is given           → person-token  Authorization: TOKEN id="<token>"
Per our research the operator/Basic form is the one that can read the operator
knowledge base; the token-only (person) form may not. This script lets you find
out for YOUR credential — run it once without a login, and (if you have one)
once with --login.

Usage:
  export TOPDESK_URL="https://yourtenant.topdesk.net"
  export TOPDESK_API_TOKEN="<the secret>"
  # optional — switches to operator/Basic auth:
  export TOPDESK_LOGIN="api_operator"
  /path/to/.venv/bin/python scripts/topdesk_probe.py

  # or pass on the CLI (env vars take precedence is NOT assumed — CLI wins):
  python scripts/topdesk_probe.py --url https://x.topdesk.net --token SECRET [--login op]
  python scripts/topdesk_probe.py --graphql-path /tas/api/knowledgeBase/graphql

Exit code 0 if it ran (regardless of individual probe results); 2 on bad args.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from typing import Any, Optional

try:
    import httpx
except ImportError:
    sys.exit(
        "error: httpx is required. Run with the project venv, e.g.\n"
        "  /Users/lexlubbers/Code/soev/open-webui/.venv/bin/python scripts/topdesk_probe.py"
    )

# ── output helpers ──────────────────────────────────────────────────────────
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_YEL = "\033[33m"
_BLU = "\033[34m"
_RST = "\033[0m"


def _section(title: str) -> None:
    print(f"\n{_BOLD}{_BLU}── {title} {'─' * max(0, 60 - len(title))}{_RST}")


def _ok(msg: str) -> None:
    print(f"  {_GREEN}✓{_RST} {msg}")


def _bad(msg: str) -> None:
    print(f"  {_RED}✗{_RST} {msg}")


def _info(msg: str) -> None:
    print(f"  {_DIM}·{_RST} {msg}")


def _warn(msg: str) -> None:
    print(f"  {_YEL}!{_RST} {msg}")


def _short(text: str, limit: int = 600) -> str:
    text = text.strip().replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + " …[truncated]"


# ── candidate paths (from research §2/§3) ────────────────────────────────────
REST_PROBES = [
    "/tas/api/version",
    "/tas/api/operators/current",
    "/tas/api/persons/current",
]
GRAPHQL_CANDIDATES = [
    "/tas/api/knowledgeBase/graphql",
    "/services/knowledge-base/api/graphql",
    "/tas/api/graphql",
]

# Standard GraphQL introspection query (read-only).
INTROSPECTION_QUERY = """
query IntrospectionQuery {
  __schema {
    queryType { name }
    types {
      kind name description
      fields(includeDeprecated: true) {
        name
        args { name type { ...TypeRef } }
        type { ...TypeRef }
      }
      inputFields { name type { ...TypeRef } }
    }
  }
}
fragment TypeRef on __Type {
  kind name
  ofType { kind name ofType { kind name ofType { kind name ofType { kind name } } } }
}
"""

# Our inferred KB query (research §5) — may fail; the errors are informative.
INFERRED_LIST_QUERY = """
query ProbeList {
  knowledgeItems(first: 3) {
    totalCount
    pageInfo { hasNextPage endCursor }
    edges { cursor node { id number title status language modificationDate } }
  }
}
"""


def build_auth_header(login: Optional[str], token: str) -> tuple[dict, str]:
    """Return (headers, human-readable form name). Basic when a login is set,
    person-token (TOKEN id="…") otherwise — mirrors services/topdesk/auth.py."""
    if login:
        raw = f"{login}:{token}".encode()
        return (
            {"Authorization": "Basic " + base64.b64encode(raw).decode()},
            f'HTTP Basic (operator login "{login}")',
        )
    return ({"Authorization": f'TOKEN id="{token}"'}, "person token (TOKEN id=…)")


def _unwrap_type(t: Optional[dict]) -> str:
    """Flatten a GraphQL TypeRef into a readable name like [KnowledgeItem!]!."""
    if not t:
        return "?"
    kind, name, of = t.get("kind"), t.get("name"), t.get("ofType")
    if kind == "NON_NULL":
        return _unwrap_type(of) + "!"
    if kind == "LIST":
        return "[" + _unwrap_type(of) + "]"
    return name or "?"


def rest_probes(client: httpx.Client, base: str) -> None:
    _section("1. REST connectivity + auth probe")
    any_ok = False
    for path in REST_PROBES:
        url = base + path
        try:
            r = client.get(url)
        except httpx.HTTPError as e:
            _bad(f"GET {path} → transport error: {type(e).__name__}: {e}")
            continue
        if r.status_code == 200:
            any_ok = True
            ctype = r.headers.get("content-type", "")
            body = _short(r.text, 300) if "json" in ctype or len(r.text) < 300 else f"<{len(r.text)} bytes {ctype}>"
            _ok(f"GET {path} → 200  {_DIM}{body}{_RST}")
        elif r.status_code in (401, 403):
            chal = r.headers.get("www-authenticate")
            extra = f'  {_DIM}WWW-Authenticate: {chal}{_RST}' if chal else ""
            _bad(f"GET {path} → {r.status_code} (auth rejected / not permitted){extra}")
        elif r.status_code == 404:
            _info(f"GET {path} → 404 (endpoint not present on this tenant)")
        else:
            _warn(f"GET {path} → {r.status_code}  {_DIM}{_short(r.text, 200)}{_RST}")
        time.sleep(0.3)
    if not any_ok:
        _warn(
            "No REST probe returned 200. If you used token-only auth, the credential "
            "may be an operator application password that needs --login (Basic auth)."
        )


def graphql_post(client: httpx.Client, endpoint: str, query: str, variables: Optional[dict] = None) -> httpx.Response:
    if "mutation" in query.lower():
        raise RuntimeError("refusing to send a GraphQL mutation — this script is read-only")
    return client.post(endpoint, json={"query": query, "variables": variables or {}})


def discover_graphql(client: httpx.Client, base: str, forced_path: Optional[str]) -> Optional[str]:
    _section("2. GraphQL endpoint discovery")
    candidates = [forced_path] if forced_path else GRAPHQL_CANDIDATES
    for path in candidates:
        url = base + path
        try:
            r = graphql_post(client, url, "{ __typename }")
        except httpx.HTTPError as e:
            _bad(f"POST {path} → transport error: {type(e).__name__}")
            continue
        ctype = r.headers.get("content-type", "")
        is_json = "json" in ctype
        try:
            payload = r.json() if is_json else None
        except Exception:
            payload = None
        graphql_shaped = isinstance(payload, dict) and ("data" in payload or "errors" in payload)
        if graphql_shaped:
            _ok(f"POST {path} → {r.status_code} GraphQL-shaped  {_DIM}{_short(json.dumps(payload), 200)}{_RST}")
            return url
        if r.status_code == 404:
            _info(f"POST {path} → 404 (not here)")
        else:
            _bad(f"POST {path} → {r.status_code} {ctype} (not GraphQL: {_short(r.text, 120)})")
        time.sleep(0.3)
    _bad("No candidate responded GraphQL-shaped. Pass the real path with --graphql-path.")
    return None


def introspect(client: httpx.Client, endpoint: str, out_path: str) -> Optional[dict]:
    _section("3. GraphQL introspection (the real schema)")
    try:
        r = graphql_post(client, endpoint, INTROSPECTION_QUERY)
    except httpx.HTTPError as e:
        _bad(f"introspection transport error: {type(e).__name__}: {e}")
        return None
    try:
        payload = r.json()
    except Exception:
        _bad(f"introspection returned non-JSON (HTTP {r.status_code}): {_short(r.text, 200)}")
        return None
    if payload.get("errors"):
        _bad(f"introspection rejected: {_short(json.dumps(payload['errors']), 400)}")
        _info("(some tenants disable introspection; you can still try the read queries below)")
        return None
    schema = payload.get("data", {}).get("__schema")
    if not schema:
        _bad(f"no __schema in response: {_short(json.dumps(payload), 300)}")
        return None

    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)
    _ok(f"introspection succeeded — full schema written to {out_path}")

    types = schema.get("types", [])
    query_type_name = (schema.get("queryType") or {}).get("name", "Query")
    query_type = next((t for t in types if t.get("name") == query_type_name), None)

    if query_type and query_type.get("fields"):
        print(f"\n  {_BOLD}Root Query fields:{_RST}")
        for fld in query_type["fields"]:
            args = ", ".join(f"{a['name']}: {_unwrap_type(a['type'])}" for a in (fld.get("args") or []))
            print(f"    {_GREEN}{fld['name']}{_RST}({_DIM}{args}{_RST}) → {_unwrap_type(fld['type'])}")

    kb_types = [
        t for t in types
        if t.get("name") and "knowledge" in t["name"].lower() and not t["name"].startswith("__")
    ]
    if kb_types:
        print(f"\n  {_BOLD}Knowledge-related types:{_RST}")
        for t in kb_types:
            print(f"    {_YEL}{t['name']}{_RST} ({t.get('kind')})")
            for fld in (t.get("fields") or [])[:40]:
                print(f"      · {fld['name']}: {_unwrap_type(fld['type'])}")
            for inp in (t.get("inputFields") or [])[:40]:
                print(f"      · (in) {inp['name']}: {_unwrap_type(inp['type'])}")
    else:
        _warn("no type with 'knowledge' in its name — the KB query may be named differently; "
              "scan the Root Query fields above.")
    return payload


def try_inferred_query(client: httpx.Client, endpoint: str) -> None:
    _section("4. Attempt the inferred knowledgeItems query (research §5)")
    _info("This is our GUESS; failures here are useful — the GraphQL errors reveal the real names.")
    try:
        r = graphql_post(client, endpoint, INFERRED_LIST_QUERY)
        payload = r.json()
    except Exception as e:
        _bad(f"request/parse error: {type(e).__name__}: {e}")
        return
    if payload.get("errors"):
        _bad("GraphQL errors (expected if the inferred schema is wrong):")
        for err in payload["errors"][:6]:
            print(f"      {_RED}- {_short(err.get('message', json.dumps(err)), 300)}{_RST}")
        return
    data = payload.get("data", {}).get("knowledgeItems")
    if data is None:
        _warn(f"no knowledgeItems in data: {_short(json.dumps(payload), 300)}")
        return
    total = data.get("totalCount")
    edges = data.get("edges", [])
    _ok(f"knowledgeItems worked! totalCount={total}, returned {len(edges)} item(s)")
    for e in edges[:3]:
        node = e.get("node", {})
        print(f"      · {_short(json.dumps(node), 300)}")
    pi = data.get("pageInfo", {})
    _info(f"pageInfo: {json.dumps(pi)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only TOPdesk URL+token probe.")
    ap.add_argument("--url", default=os.environ.get("TOPDESK_URL", ""))
    ap.add_argument("--token", default=os.environ.get("TOPDESK_API_TOKEN", ""))
    ap.add_argument("--login", default=os.environ.get("TOPDESK_LOGIN", ""),
                    help="operator login → switches to HTTP Basic; omit for person-token auth")
    ap.add_argument("--graphql-path", default=os.environ.get("TOPDESK_GRAPHQL_PATH", ""),
                    help="skip discovery and use this exact GraphQL path")
    ap.add_argument("--timeout", type=float, default=20.0)
    args = ap.parse_args()

    if not args.url or not args.token:
        print("error: provide --url and --token (or TOPDESK_URL / TOPDESK_API_TOKEN).", file=sys.stderr)
        return 2

    base = args.url.strip().rstrip("/")
    headers, form = build_auth_header(args.login.strip() or None, args.token.strip())
    headers["Content-Type"] = "application/json"
    headers["Accept"] = "application/json"

    print(f"{_BOLD}TOPdesk read-only probe{_RST}")
    print(f"  tenant : {base}")
    print(f"  auth   : {form}")
    print(f"  secret : {_DIM}(set, {len(args.token.strip())} chars — not shown){_RST}")
    print(f"  mode   : READ-ONLY (GET + GraphQL queries only; no mutations)")

    with httpx.Client(headers=headers, timeout=args.timeout, follow_redirects=False) as client:
        rest_probes(client, base)
        endpoint = discover_graphql(client, base, args.graphql_path.strip() or None)
        if endpoint:
            introspect(client, endpoint, "topdesk_introspection.json")
            try_inferred_query(client, endpoint)
        else:
            _warn("Skipping introspection/queries — no GraphQL endpoint found. "
                  "Find the path in the tenant's API explorer and pass --graphql-path.")

    _section("Done")
    print("  Review the output above against "
          "thoughts/shared/research/2026-06-topdesk-api-verification.md §10")
    print("  If introspection succeeded, topdesk_introspection.json holds the real schema —")
    print("  reconcile it with services/topdesk/topdesk_client.py (the query constants) + fixtures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
