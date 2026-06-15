# TOPdesk API Verification — Phase 0 (docs-based) + Reconciled to REST (2026-06-15)

**Date:** 2026-06-12 (Phase 0) · **Reconciled:** 2026-06-15
**Author:** @lexlubbers (with Claude Fable 5; reconciliation with Claude Opus 4.8)
**Branch:** `feat/topdesk`
**Plan phase:** Phase 0 — API verification → reconciled against the real OpenAPI specs.

---

## STATUS BANNER (updated 2026-06-15)

> **The GraphQL hypothesis was WRONG. The integration now targets the SaaS REST
> Knowledge Base API (`knowledge-base-v1`).** Phase 0 (below, §1–§12) was a
> documentation-only pass that — because TOPdesk's developer portal is a JS SPA —
> inferred a GraphQL schema. We have since obtained the **real OpenAPI specs**
> (`knowledge-base_SaaS.json`) and **confirmed the auth model**, and reconciled the
> implementation to them. The authoritative current truth is **§0 (REST — CONFIRMED)**
> immediately below; the original GraphQL sections **§3–§6 are SUPERSEDED** and kept
> only as the historical Phase-0 record.
>
> **What changed:** transport is REST (`GET /knowledgeItems`, offset paging,
> FIQL `query`, `fields` selector), not GraphQL; the item shape is **nested**
> (`translation.content.*`, `status.name`, `visibility.sspVisibility`, `urls.*`);
> the publish filter is **visibility-driven** (scope `ssp`/`public`/`all`), not a
> status enum; auth is **HTTP Basic only** (operator login + application token — the
> `TOKEN id="…"` person form was dropped).
>
> **Only one thing remains live-unverified:** that the tenant serves KB-v1 **and our
> operator login authenticates**. Run `scripts/topdesk_discover.py --login "<op>"`
> once Intermax provides the operator login; a 2xx on the `KB-v1` row closes the gate.

---

## 0. REST Knowledge Base API (`knowledge-base-v1`) — CONFIRMED (2026-06-15)

Source: the tenant's OpenAPI spec `knowledge-base_SaaS.json` (OAS 3.0), plus the
TOPdesk API Tutorial for auth. This section supersedes §3–§6.

### 0.1 Auth — HTTP Basic, operator-only (CONFIRMED)

```
Authorization: Basic base64(operatorLoginName:applicationToken)
```

Verbatim (TOPdesk API Tutorial → "Create an application token"): *"select the 'Basic
Auth' option … Fill the login name of your TOPdesk user into the 'Username' field and
the application token you just created for this user in the 'Password' field." … "This
is currently only possible for operators."* The legacy `TOKEN id="…"` person-token form
is **not** accepted by the KB REST API and was removed from our client
(`services/topdesk/auth.py` — Basic only; operator login is **required**).

### 0.2 Endpoints

- Base: `https://{tenant}.topdesk.net/services/knowledge-base-v1` (config:
  `TOPDESK_KB_API_PATH`). Auth/version probe stays under `/tas/api/version`.
- `GET /knowledgeItems` — list. Params: `start` (offset, default 0), `page_size`
  (1–1000, default 100), `fields` (comma list — content/title/status/etc. are **NOT**
  returned unless requested), `query` (FIQL: `parent.id`, `status.id/name`,
  `modificationDate`, `archived`, `news`, `visibility.*`), `language` (BCP-47).
  Response `{ "item": [KnowledgeItem], "prev"?, "next"? }`; HTTP **200** (complete) or
  **206** (partial). Accept `application/x.topdesk-kb-ki-list-v1+json`.
- `GET /knowledgeItems/{id|number}` — single item (returned directly). Accept
  `application/x.topdesk-kb-ki-v1+json`. Spec warning: if no `translation.content.*`
  field is requested the content block returns empty — so always request content
  subfields (our `_KI_FIELDS` does).
- Errors: `400` → `{ "errors": [{ "errorCode", "appliesTo", "errorMessage" }] }`;
  `404` not found.

### 0.3 KnowledgeItem shape (nested)

```jsonc
{
  "id": "uuid", "number": "KI 0211",
  "parent": { "id": "uuid", "name": "..." },
  "translation": {
    "language": "nl",
    "content": { "title": "...", "description": "<html>", "content": "<html>",
                 "commentsForOperators": "...", "keywords": "comma, space string" }
  },
  "visibility": { "sspVisibility": "VISIBLE|NOT_VISIBLE|VISIBLE_IN_PERIOD",
                  "sspVisibleFrom": "...", "sspVisibleUntil": "...",
                  "publicKnowledgeItem": true },
  "urls": { "operator": "/...", "ssp": "/...", "public": "/..." },   // relative
  "status": { "id": "...", "name": "..." },                          // searchlist, NOT an enum
  "modificationDate": "ISO-8601Z", "creationDate": "ISO-8601Z",
  "availableTranslations": ["nl", "en"], "news": false,
  "manager": {...}, "externalLink": "..."
}
```

Content is **HTML** (run through `html_to_markdown`). `keywords` is a single
comma/space string (split to a list). The flat field reads live in one place —
`services/topdesk/mapping.py`.

### 0.4 Mapping to our integration (what survived)

The worker *design* survived intact — the REST API has everything it assumed:
**parent** (tree via FIQL `parent.id==<id>`), **modificationDate** (client-side
incremental hashing — unchanged), **HTML content**, **visibility** (publish filter),
**urls** (front-matter web link), **keywords/availableTranslations/number**. The real
work was swapping the client transport (GraphQL→REST) and remapping field reads
(flat→nested). Publish/inclusion is now **`_should_sync`** driven by
`TOPDESK_SYNC_SCOPE` (`ssp` default / `public` / `all`; archived always excluded),
replacing the status-enum `_is_published`. Language: v1 syncs the **tenant default
language only**. Attachments/images: **deferred** (text only).

---

## 1. Background: legacy REST removed, GraphQL is the replacement

- The legacy REST Knowledge Base API at `/tas/api/knowledgeItems/…` was **deprecated
  in July 2024 and fully removed in TOPdesk 2025 R2**. Per the 2025 R2 release notes:
  *"The endpoints for the old version are no longer reachable starting with this
  version of TOPdesk … if you update to 2025 R2 and rely on the Knowledge Base API,
  you need to migrate your API calls to the documented endpoints."*
  (Confidence: **documented-confirmed**.)
- The replacement is a **GraphQL** Knowledge Base API. The TOPdesk Python SDK
  (`topdesk` on PyPI) explicitly states: *"The Knowledge Base API is currently not
  supported, because it uses GraphQL whereas the rest of the API uses REST."*
  (Confidence: **documented-confirmed** that the KB API is GraphQL; the SDK does not
  wrap it, so no schema is available from there.)

**Implication for us:** the integration targets must run TOPdesk **2025 R2 or later**.
On older tenants the GraphQL KB API may not exist and the legacy REST endpoint may
still be present — the connection probe (below) plus a capability check should detect
this.

---

## 2. Authentication forms + connection probe

### 2.1 Auth header forms (both documented-confirmed)

TOPdesk supports exactly the two header forms the plan assumes:

**(a) Basic auth — operator login + application password** (this is what we will use
for the service-account sync):

```
Authorization: Basic base64(operatorLoginName:applicationPassword)
Content-Type: application/json
```

- Docs ("Authorizing access to TOPdesk API"): *"Fill in the login name of the
  operator, and for password fill in the application password you created via the user
  menu."* The "URL and headers" doc shows a literal example header
  `Authorization: Basic b3BlcmF0b3Jfb3duOnBhc3N3b3JkaGVyZQ==` (which decodes to
  `operator_own:passwordhere`).
- The credential is an **application password**, NOT the operator's normal login
  password. Application passwords are created by the operator via their TOPdesk user
  menu and are the recommended/secure mechanism. (Confidence: **documented-confirmed**.)
- This is exactly the base64(`username:secret`) construction already used by
  `ConfluenceClient` basic mode — we can reuse the same precomputed-header pattern.

**(b) Person token form** (documented, but **not** what we use for sync):

```
Authorization: TOKEN id="<personToken>"
```

- Docs: *"For the name, enter Authorization. For the value, enter `TOKEN id="[your
  token]"`."* This is tied to a **person** (Self-Service Portal user), not an operator,
  and is the wrong identity class for syncing the operator-visible knowledge base.
  (Confidence: **documented-confirmed** the form exists; we will **not** use it in v1.)

### 2.2 Recommended connection probe

A lightweight authenticated GET to confirm credentials + reachability before any
GraphQL traffic. Two candidates, both on the REST surface (the GraphQL endpoint is a
POST-only `query` surface, less suited to a cheap probe):

| Probe | Status | Returns | Notes |
|-------|--------|---------|-------|
| `GET /tas/api/version` | **inferred** (endpoint path) | TOPdesk version string/object | The TOPdesk SDK exposes `topdesk.version()` (documented-confirmed the method exists), which maps to a version endpoint. Exact path `/tas/api/version` is **inferred** from the SDK method + the `/tas/api/` base convention. Likely needs no special permission — good as a pure reachability+auth check. |
| `GET /tas/api/operators/current` | **inferred** | The authenticated operator's own record | Analogous to the **documented-confirmed** `GET /tas/api/persons/current` ("retrieves … currently logged-in persons"). `operators/current` is **inferred** by symmetry — it additionally confirms the credential is an *operator* (the identity class we need), and surfaces the operator's name for the admin UI. Requires the operator to be valid/active. |

**Recommendation:** use `GET /tas/api/operators/current` as the primary probe — it both
checks auth *and* verifies the credential is an operator (not a person), which is the
class that can read the operator knowledge base. Fall back to `/tas/api/version` if
`operators/current` is not present on the tenant. **Both exact paths require live
confirmation.**

- Base URL convention (documented-confirmed): `https://{tenant}.topdesk.net/tas/api/…`
  (the "URL and headers" doc: `https://TOPdeskURL/tas/api/endpoint/...`).

---

## 3. GraphQL endpoint path

> **⚠️ SUPERSEDED (2026-06-15) — §3–§6 below describe the INFERRED GraphQL API, which
> turned out to be the wrong API. The integration targets the REST KB API; see §0 for
> the confirmed schema, endpoints, auth, pagination, filtering, and field shape. The
> text below is retained only as the historical Phase-0 record.**

- **Inferred.** The exact POST path for the Knowledge Base GraphQL endpoint could not
  be extracted from public docs (it lives only inside the JS-rendered explorer at
  `developers.topdesk.com/explorer/?page=knowledgebase-graphql`). Based on the
  `/tas/api/` base convention and common TOPdesk module pathing, the most likely
  candidates (in rough order of likelihood) are:
  1. `POST https://{tenant}.topdesk.net/tas/api/knowledgeBase/graphql`
  2. `POST https://{tenant}.topdesk.net/services/knowledge-base/api/graphql`
  3. `POST https://{tenant}.topdesk.net/tas/api/graphql`
- **How to disambiguate them mechanically:** POST a trivial `{ __typename }` query to
  each candidate (Basic auth, `Content-Type: application/json`). A **404 or an HTML
  body** (e.g. a login/redirect page) rules a path out — it is not a GraphQL endpoint.
  A **GraphQL-shaped JSON response confirms the path**: either HTTP 200 with a
  `data` object (`{"data": {"__typename": "Query"}}`), or HTTP 400 carrying a top-level
  GraphQL `errors` array (a 400 with `errors` still means a GraphQL server is answering —
  see §4.0). Pick the first candidate that responds GraphQL-shaped.
- The request body is the standard GraphQL envelope:
  `{"query": "...", "variables": {...}}`, `Content-Type: application/json`, with the
  Basic auth header from §2.1.
- **This is the single highest-priority item to confirm by live introspection.** The
  client code (Phase 2) should make the endpoint path a config value, not a hard-coded
  constant, so it can be corrected without a code change once the live path is known.

---

## 4. GraphQL schema (knowledge items) — INFERRED

> **All of §4–§6 is inferred** and modelled on the standard GraphQL **Relay cursor
> connection** pattern (the idiomatic default for a paginated GraphQL list, and what
> the TOPdesk explorer almost certainly exposes). Field *names* are taken from the
> TOPdesk knowledge-item domain vocabulary visible in the product UI and docs
> (number, title, content, keywords, visibility, status, language, translations,
> creation/modification dates). The fixtures in
> `backend/open_webui/test/services/topdesk/fixtures/` follow this exact shape so the
> client can be written against it; if live introspection differs, fixtures + client
> adapt together.

### 4.0 GraphQL error envelope

Unlike the success-schema fixtures (whose *field shape* is inferred from product
vocabulary), the GraphQL **error envelope** is **documented-confirmed** by the GraphQL
spec itself — its shape is not guessed. Query-level failures (validation errors,
unknown fields, resolver exceptions, partial-result failures) are **not** signalled by
an HTTP status code. They arrive as **HTTP 200** with a top-level `errors` array in the
response body:

```json
{
  "data": null,
  "errors": [
    { "message": "...", "path": ["knowledgeItems"], "extensions": { "code": "..." } }
  ]
}
```

- `errors` is a top-level array of error objects; each carries at least `message`, and
  commonly `path`, `locations`, and an `extensions` object (where TOPdesk/most servers
  put a machine-readable `code`).
- `data` is `null` (total failure) **or partially populated** (some fields resolved,
  others errored) — a successful field and an `errors` entry can coexist in one 200
  response.
- **This is a distinct failure mode from HTTP-status errors** (4xx/5xx, which the §2
  auth/transport layer handles). The Phase-2 client **must inspect the response body for
  a top-level `errors` key even on HTTP 200** and **must not treat a 200-with-`errors`
  response as success** — surface it as a query failure (and, for partial data, decide
  per-field whether to use or discard). The `items_error.json` fixture models this
  envelope; its shape is GraphQL-spec-defined (**documented-confirmed**), in contrast to
  the inferred success-schema fixtures below.

### 4.1 Assumed schema excerpt (inferred SDL)

```graphql
type Query {
  # List/search knowledge items with cursor pagination + filtering.
  knowledgeItems(
    first: Int
    after: String
    filter: KnowledgeItemFilter
  ): KnowledgeItemConnection!

  # Fetch a single item by id (UUID) — used for full-content hydration.
  knowledgeItem(id: ID!): KnowledgeItem
}

type KnowledgeItemConnection {
  edges: [KnowledgeItemEdge!]!
  pageInfo: PageInfo!
  totalCount: Int
}

type KnowledgeItemEdge {
  cursor: String!
  node: KnowledgeItem!
}

type PageInfo {
  hasNextPage: Boolean!
  endCursor: String
}

type KnowledgeItem {
  id: ID!                       # UUID (the `unid` used in web URLs, see §7)
  number: String                # human-readable KI number, e.g. "KI 0308"
  title: String!
  description: String           # short summary / abstract
  content: String               # HTML body (see note below)
  keywords: [String!]
  language: String              # e.g. "en", "nl"
  status: KnowledgeItemStatus   # PUBLISHED | DRAFT | ARCHIVED (enum, names inferred)
  visibility: KnowledgeItemVisibility  # operator vs SSP visibility (inferred)
  parent: KnowledgeItem         # parent node for tree traversal
  children: [KnowledgeItem!]    # direct child items / sub-tree
  availableTranslations: [KnowledgeItemTranslation!]
  attachments: [Attachment!]    # see §6 — excluded from v1
  creationDate: DateTime
  modificationDate: DateTime    # used for incremental sync (§5)
}

type KnowledgeItemTranslation {
  language: String!
  title: String
  status: KnowledgeItemStatus
}

enum KnowledgeItemStatus { PUBLISHED DRAFT ARCHIVED }
enum KnowledgeItemVisibility { OPERATOR SELF_SERVICE_PORTAL EVERYONE }
```

### 4.2 `content` is HTML

- **Inferred (high confidence).** TOPdesk knowledge items are authored in a rich-text
  WYSIWYG editor and rendered as HTML in both the operator section and the
  Self-Service Portal. The synced body is therefore expected to be an **HTML string**,
  which matches our existing pipeline: the Confluence integration already converts
  rendered HTML bodies to markdown via `services/confluence/html_renderer.py`, and the
  plan (Phase 2.1) generalises that renderer for reuse. Treat `content` as HTML and run
  it through the shared HTML→markdown renderer.

### 4.3 Field-by-field confidence

| Field | Confidence | Note |
|-------|-----------|------|
| `id` (UUID) | inferred (high) | Real web URLs use a `unid` UUID (§7) → items have a UUID id. |
| `number` (KI number) | inferred (high) | Real public URL `…/item/KI 0308/en/` shows human KI numbers exist. |
| `title` | inferred (high) | Core field, visible everywhere. |
| `description` | inferred (med) | Short summary exists in UI; field name guessed. |
| `content` (HTML) | inferred (high) | HTML body — see §4.2. |
| `keywords` | inferred (med) | KB items have keywords/tags in UI; shape (list) guessed. |
| `language` | inferred (high) | KB is multilingual; URLs carry `/en/`, `/nl/`. |
| `status` (published) | inferred (med) | Publish/draft lifecycle is documented in product; enum names guessed. |
| `visibility` | inferred (med) | Operator vs SSP vs public is a real distinction; enum names guessed. |
| `parent` / `children` | inferred (med) | KB is hierarchical (categories/sub-items); exact traversal field names guessed. |
| `availableTranslations` | inferred (med) | Multilingual items expose translations; shape guessed. |
| `creationDate` / `modificationDate` | inferred (high) | Audit dates exist; needed for incremental sync. |

---

## 5. Pagination + filtering — INFERRED

### 5.1 Pagination (Relay cursor connection — inferred)

- Page size argument: `first: Int`
- Cursor argument: `after: String` (opaque cursor from a previous page)
- Page metadata: `pageInfo { hasNextPage endCursor }`
- Iteration: request `first: N`; while `pageInfo.hasNextPage`, repeat with
  `after: pageInfo.endCursor`.

Example list query (inferred):

```graphql
query ListKnowledgeItems($first: Int!, $after: String, $filter: KnowledgeItemFilter) {
  knowledgeItems(first: $first, after: $after, filter: $filter) {
    edges {
      cursor
      node {
        id
        number
        title
        description
        language
        status
        visibility
        creationDate
        modificationDate
      }
    }
    pageInfo { hasNextPage endCursor }
    totalCount
  }
}
```

Variables: `{ "first": 50, "after": null, "filter": { "status": "PUBLISHED" } }`

> **Contrast with Confluence:** the Confluence v2 REST client paginates via a
> pre-signed `_links.next` URL + `cursor` query param. TOPdesk GraphQL uses the Relay
> `after`/`endCursor` cursor in the query body instead. The sync-worker pagination loop
> is conceptually identical (loop until no next cursor) but the wiring differs — note
> this when modelling `topdesk_client.py` on `confluence_client.py`.

### 5.2 Filtering (inferred)

Two filters the plan needs, expressed via a `filter` input object (shape inferred):

- **Status == published** (skip drafts/archived):
  `filter: { status: PUBLISHED }`
- **Incremental sync by modification date** (only items changed since last sync):
  `filter: { modificationDate: { gte: "2026-06-01T00:00:00Z" } }`
  (the comparator key — `gte` / `after` / `since` / `modifiedAfter` — is **guessed**;
  confirm by introspection.)

Combined:
```graphql
filter: { status: PUBLISHED, modificationDate: { gte: $since } }
```

**Risk:** if the GraphQL API does **not** support server-side `modificationDate`
filtering, incremental sync must fall back to **client-side** filtering (fetch all,
compare `modificationDate` locally). That is correct but heavier on large knowledge
bases. The Intermax driving use case (one shared read-only KB) is small enough that a
client-side fallback is acceptable for v1 if needed. This does not block the plan.

### 5.3 Parent/child tree traversal (inferred)

- Either via the `parent` / `children` fields on `KnowledgeItem` (walk the tree), or by
  a separate `knowledgeItem(id).children` query. The fixture `item_children.json` models
  a children-of-parent response. **Confirm whether traversal is field-embedded or a
  separate query** during live introspection.
- v1 (Intermax: single flat shared KB) may not even need tree traversal — it can list
  all items and treat them flat. We capture the mechanism for completeness.

---

## 6. Attachment representation — INFERRED (v1 EXCLUDES attachments)

- **Inferred.** Knowledge items can carry file attachments in the product. In the
  GraphQL schema these are expected to surface as an `attachments: [Attachment!]` field
  on `KnowledgeItem`, where an `Attachment` likely exposes `{ id, fileName, mimeType,
  size, downloadUrl }` (field names guessed). Binary bytes are not returned inline by
  GraphQL — a `downloadUrl` (a `/tas/api/...` REST download path requiring the same
  Basic auth) is the expected mechanism.
- **v1 of our integration explicitly excludes attachments.** We record the mechanism
  only so a future iteration can add attachment ingestion (resolve `downloadUrl` →
  fetch bytes with Basic auth → push through the document-processing pipeline, same as
  cloud-sync file ingestion). No fixture models attachment *bytes*; the
  `item_with_content.json` fixture includes an empty/representative `attachments` array
  so the client can safely read and ignore the field.

---

## 7. Canonical web URL (for the `web_url` link in synced documents)

Two real, observed URL forms (both **documented-confirmed** from real tenant URLs found
in public search results):

**(a) Self-Service Portal (SSP) detail link — UUID `unid`:**
```
https://{tenant}.topdesk.net/tas/public/ssp/content/detail/knowledgeitem?unid={uuid}
```
Real examples observed (tenants redacted to illustrate shape only):
- `…/tas/public/ssp/content/detail/knowledgeitem?unid=e2a64a28-c0b8-4df2-9029-241fbecfbf72`
  (UUID with dashes)
- `…/tas/public/ssp/content/detail/knowledgeitem?unid=893a0d25f1b547e7975f600f106bcfc6`
  (UUID without dashes — TOPdesk accepts both forms)

**(b) Public "open knowledge items" portal — human KI number:**
```
https://{tenant}.topdesk.net/solutions/open-knowledge-items/item/KI%20{number}/{lang}/
```
Real example: `…/solutions/open-knowledge-items/item/KI%200308/en/`

**Recommendation for our `web_url`:** use form (a), the SSP detail link with the item's
`unid` (= the GraphQL `id` UUID), as the canonical embedded link. It is the most
universally available form and maps directly to the `id` we already have. Form (b) is
only available when the org publishes a public knowledge portal and requires the KI
`number` + language. There is also an operator-section deep link (under
`/tas/secure/...`) but it requires an operator login and is unsuitable for end-user
citation links, so we do not use it.

- **Confidence:** the SSP URL *template* is **documented-confirmed**; the assumption
  that the GraphQL `id` equals the `unid` is **inferred (high)** and should be spot-
  checked live (fetch one item's `id`, build the SSP URL, confirm it resolves).

---

## 8. Confidence summary

| # | Uncertainty | Finding | Confidence |
|---|-------------|---------|-----------|
| 1 | GraphQL endpoint path | `/tas/api/...graphql` form; exact leaf unknown | **inferred** |
| 2 | GraphQL schema (types/fields) | Relay connection + KnowledgeItem fields as in §4 | **inferred** (field names from product vocabulary) |
| 3 | Pagination | Relay `first`/`after` + `pageInfo{hasNextPage,endCursor}` | **inferred** |
| 4 | Filtering (`modificationDate`, `status`) | `filter` input object; comparator key guessed | **inferred** (with client-side fallback plan) |
| 5 | Auth forms | Basic(operator:appPassword) + `TOKEN id="..."` | **documented-confirmed** |
| 5 | Connection probe | `operators/current` (primary), `version` (fallback) | **inferred** (paths) / probe *concept* documented (`persons/current` confirmed) |
| 6 | Attachments | `attachments` field w/ `downloadUrl`; excluded in v1 | **inferred** |
| 7 | Web URL | SSP `…/knowledgeitem?unid={uuid}` | **documented-confirmed** (template) |
| 0 | Legacy REST removed / GraphQL is replacement / tenant must be ≥2025 R2 | yes | **documented-confirmed** |

---

## 9. Go / No-Go

**Signal: GO with conditions (DONE_WITH_CONCERNS), NOT BLOCKED.**

The plan's hard dependencies are pagination, `modificationDate` filtering, and
parent/child traversal. **No public source indicates any of these is absent** — and a
GraphQL list API that the vendor markets for integration would be unusual to ship
without cursor pagination. The product is demonstrably hierarchical (categories →
items → translations) and exposes audit dates, so the underlying data supports
traversal and incremental sync. I therefore do **not** raise BLOCKED.

However, I could not *positively confirm* the schema from docs (SPA-only), so the
go-ahead is conditional on the live-verification checklist below passing. If live
introspection reveals, e.g., **no `modificationDate` server-side filter**, the
mitigation (client-side filtering) keeps the plan viable for the small Intermax KB.
The only finding that would genuinely block is if the GraphQL API turned out to have
**no pagination at all** on a large KB — judged very unlikely.

---

## 10. REQUIRES LIVE VERIFICATION (checklist) — reconciled 2026-06-15

The OpenAPI specs answer almost everything that was inferred. **Items the specs now
resolve are checked off.** The single remaining gate is operational — the tenant
serving KB-v1 and our operator login authenticating — and is run with
`scripts/topdesk_discover.py --login "<operatorLogin>"` once Intermax provides the login.

- [ ] **GATE — auth + KB-v1 reachability:** `scripts/topdesk_discover.py --login "<op>"`
      → the `KB-v1 (REST SaaS)` row returns 2xx. (Blocked only on getting the operator
      login from Intermax; every guess so far 401'd.) If KB-v1 404s but identity 200s →
      the Knowledge Base API feature flag is off on the tenant (TOS < 14.07.019) — ask
      the application manager. If only GraphQL answers, this reconciliation is wrong.
- [x] **API surface:** REST `knowledge-base-v1`, not GraphQL — confirmed from the
      OpenAPI spec (`knowledge-base_SaaS.json`). Endpoint base `/services/knowledge-base-v1`.
- [x] **Auth form:** HTTP Basic, operator login + application token (operator-only) —
      confirmed from the API Tutorial. Person-token form dropped.
- [x] **Field names + shape:** nested `translation.content.{title,description,content,
      keywords}`, `status.name`, `visibility.sspVisibility`, `urls.*`,
      `modificationDate`, `availableTranslations` — confirmed from the spec (see §0.3).
- [x] **`content` is HTML:** confirmed (spec documents HTML body).
- [x] **Pagination:** offset `start`/`page_size` with `{item, prev, next}` (200/206) —
      confirmed (replaces the inferred Relay cursor model).
- [x] **Filtering:** FIQL `query` (`parent.id`, `status.*`, `modificationDate`,
      `archived`, `visibility.*`) — confirmed. v1 uses client-side `modificationDate`
      hashing; server-side FIQL `modificationDate=gt=` is a later optimization.
- [x] **Tree traversal:** children via FIQL `query=parent.id==<id>` (a list query, not
      embedded children) — confirmed.
- [x] **Publish/visibility filter:** driven by `visibility.sspVisibility` +
      `publicKnowledgeItem` + `archived` (not a status enum), scoped by
      `TOPDESK_SYNC_SCOPE` — confirmed; `status` is a customer searchlist with no
      guaranteed "PUBLISHED" value.
- [x] **Attachments:** REST exposes `…/attachments` + `…/images` download endpoints —
      confirmed; deferred (text only in v1).
- [ ] **Web URL spot-check:** with real creds, confirm `urls.public`/`urls.ssp` resolve
      to the item (we now use the server-provided relative URLs, not a constructed SSP
      link, so this is low-risk).

---

## 11. Sources

- TOPdesk Knowledge Base (GraphQL) explorer (JS SPA — not statically extractable):
  https://developers.topdesk.com/explorer/?page=knowledgebase-graphql
- TOPdesk Knowledge Base API explorer:
  https://developers.topdesk.com/explorer/?page=knowledge-base
- What's new in TOPdesk 2025 R2 (legacy `/tas/api/knowledgeItems` removed; GraphQL
  replacement): https://docs.topdesk.com/VA2025R2/en/what-s-new-in-topdesk.html
- Authorizing access to TOPdesk API (Basic auth = operator login + application
  password; `TOKEN id="..."` person token):
  https://docs.topdesk.com/VA2023R2/en/authorizing-access-to-topdesk-api.html
- The URL and headers (`https://TOPdeskURL/tas/api/...`; literal `Authorization: Basic`
  example; `/tas/api/persons/current` example):
  https://docs.topdesk.com/VA2023R2/en/the-url-and-headers.html
- API access to TOPdesk (`/tas/api/` base):
  https://docs.topdesk.com/VA2023R2/en/api-access-to-topdesk.html
- TOPdesk Python SDK (`topdesk` on PyPI) — confirms KB API is GraphQL and unsupported
  by the SDK; `app_creds=(username, token)`; `topdesk.version()`:
  https://pypi.org/project/topdesk/
- TOPdeskPy SDK (connect(url, user, password); KB GraphQL not supported):
  https://github.com/TwinkelToe/TOPdeskPy
- techspaceco/topdesk-go (base path `/tas/api`; transparent pagination iterator):
  https://github.com/techspaceco/topdesk-go
- Providing a knowledge item via a link (SSP vs operator section links exist):
  https://docs.topdesk.com/en/providing-a-knowledge-item-via-a-link.html
- Real-world SSP knowledge-item URL form (`/tas/public/ssp/content/detail/knowledgeitem?unid=…`)
  — observed in public search results across multiple `*.topdesk.net` tenants.
- Real-world public knowledge-portal URL form (`/solutions/open-knowledge-items/item/KI%20{n}/{lang}/`)
  — observed in public search results.

---

## 12. Notes for Phase 2 (client implementation)

- Model `services/topdesk/topdesk_client.py` on `services/confluence/confluence_client.py`
  (async httpx, retry/backoff, 401-terminal-in-basic-mode), but: (a) the request is a
  **POST GraphQL** body, not a REST GET; (b) pagination uses the **Relay `after`/`endCursor`**
  cursor in the query, not `_links.next`.
- Make the **GraphQL endpoint path a config value** (not a constant) so the live-verified
  path can be set without a code change.
- Reuse the generalised HTML→markdown renderer (Phase 2.1) for `content`.
- The fixtures in `backend/open_webui/test/services/topdesk/fixtures/` encode the inferred
  schema shape; tests should drive a mocked httpx transport against them, exactly like
  the Confluence basic-auth tests. When live introspection lands, update fixtures +
  client together.
