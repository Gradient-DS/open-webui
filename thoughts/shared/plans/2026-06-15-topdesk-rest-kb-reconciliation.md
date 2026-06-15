# TOPdesk: reconcile the integration to the real REST Knowledge Base API

## Status / why this plan

The TOPdesk client + worker were built against an **inferred GraphQL schema** (Phase 0 was
docs-only; the dev portal is a JS SPA). We now have the **real OpenAPI specs** (pasted from the
explorer) and have **confirmed the auth model**. This plan reconciles the implementation to reality.

**Auth — CONFIRMED.** TOPdesk API auth is HTTP **Basic**, `Username = operator login name`,
`Password = application token` (a.k.a. application password). Operator-only. Source (verbatim,
TOPdesk API Tutorial → "Create an application token"):
> "select the 'Basic Auth' option … Fill the login name of your TOPdesk user into the 'Username'
> field and the application token you just created for this user in the 'Password' field." …
> "This is currently only possible for operators."

Our `services/topdesk/auth.py` **already** does `Basic base64(login:app_password)` when a username
is set — so auth is essentially correct. The only blocker is operational: **we don't yet have the
operator login name** (every guess 401'd). No code change is gated on it except *verification*.

**API — three candidates, target the REST one.** Not yet live-confirmed (probe 401'd on all due to
the missing login). From the specs:

| API | Path | Shape | Verdict |
|---|---|---|---|
| **REST SaaS** | `GET /services/knowledge-base-v1/knowledgeItems` (OAS 3.0) | `{item:[…], prev, next}`; rich KnowledgeItem (parent, status, visibility, urls, HTML content, keywords, modificationDate, availableTranslations); FIQL `query`; offset `start`/`page_size` | **TARGET** — only one rich enough for our design |
| GraphQL public | `POST /tas/api/knowledgeBase/public` (OAS 2.0) | `{results:{knowledgeItems,languages}}`; id/number/translations{title,content plain-text}; `search:{term}`; 403 "not an SSP user" | reject — SSP-person-only, minimal, no parent/status/modDate |
| Legacy REST | `GET /tas/api/knowledgeItems` | old REST KB | reject — removed in 2025 R2 |

**Gate (do before merging code):** once Intermax provides the operator login, run
`scripts/topdesk_discover.py --login "<operatorLogin>"` and confirm the `KB-v1 (REST SaaS)` row
returns 2xx. If KB-v1 404s but identity 200s → the **Knowledge Base API feature flag** is off on
the tenant (TOS < 14.07.019) — ask the application manager before proceeding. If only GraphQL
answers, this plan is wrong and we revisit (smaller/different worker).

## Good news: the worker *design* mostly survives

The REST API has everything our worker assumed: **parent** (tree), **modificationDate** (incremental
change detection — our current hashing is valid), **HTML content** (our `html_to_markdown` is
valid), **status/visibility** (publish filter), **urls.{operator,ssp,public}** (front-matter web
link), **keywords/availableTranslations/number**. So most of `sync_worker.py` (tree walk, item_map
change detection, dedup, render, front-matter, suspension) stays — the real work is **swapping the
client transport GraphQL→REST** and **remapping field reads** from the inferred flat shape to the
real nested shape.

---

## Current state (inventory — file:line)

- **`config.py:3303-3307`** — `TOPDESK_GRAPHQL_PATH = os.getenv('TOPDESK_GRAPHQL_PATH', '/tas/api/knowledgeBase/graphql')` (plain env). Plus `TOPDESK_URL`, `TOPDESK_USERNAME`, `TOPDESK_APP_PASSWORD`, `TOPDESK_MAX_ITEMS_PER_SYNC`, `TOPDESK_MAX_ITEM_SIZE_MB`.
- **`services/topdesk/auth.py`** — `build_auth_header(username, app_password)` (Basic when username, else `TOKEN id=`), `service_auth_configured()`, `build_client()`. **Auth correct for REST; keep.**
- **`services/topdesk/topdesk_client.py`** — GraphQL client: `graphql()`, `list_knowledge_items` (Relay `first/after`), `iter_knowledge_items`, `list_all_knowledge_items(status)`, `get_knowledge_item(item_id, include_content)`, `list_item_children(item_id)`, `list_root_items(status)`, `probe()`; builds `{TOPDESK_URL}{TOPDESK_GRAPHQL_PATH}`; exceptions `TopdeskAuthError` / `TopdeskGraphQLError` / `TopdeskTransientError`; retry skeleton (429 Retry-After, 5xx backoff, 401 terminal, connect→friendly). **Rewrite to REST.**
- **`services/topdesk/sync_worker.py`** — `_is_published` (`status == 'PUBLISHED'` string, `:57,71`), `_get_cloud_hash`/`item_map` by `modificationDate` (`:415,460-472`), `_walk_descendants` via `list_item_children` (`:389`), `_resolve_shared_kb_sources`, `_collect_folder_files`/`_collect_single_file`, `_download_file_content` (get → html → `html_to_markdown` → front-matter, `:545-552`), `_get_provider_file_meta` (`:415-433`), `_build_item_url` (constructs `…/ssp/content/detail/knowledgeitem?unid={id}`). Reads **flat** fields: `item.get('content'|'modificationDate'|'number'|'language'|'keywords'|'status')`. **Remap to nested REST shape + fix publish filter.**
- **`routers/topdesk_sync.py:195`** — `GET /browse/items` (root via `list_root_items`, children via `list_item_children`) → `{id,name,number,has_children,status}`; `/auth/test` (probe); `/shared/*`. **Browse maps to REST FIQL parent.id; contract unchanged.**
- **Frontend** — `TopdeskPickerModal.svelte` (browseTopdeskItems root/children, lazy tree), `TopdeskSection.svelte`, `apis/topdesk/index.ts`. **Unchanged** if the browse contract is preserved.
- **Fixtures** `test/services/topdesk/fixtures/*.json` — GraphQL-shaped. **Rewrite to REST.**

---

## Real REST API facts (from `knowledge-base_SaaS.json`)

- Base: `https://{host}/services/knowledge-base-v1`. (Auth/version probe stays under `/tas/api`.)
- `GET /knowledgeItems` params: `start` (offset, default 0), `page_size` (1–1000, default 100),
  `fields` (comma list — **content/title/status/etc. are NOT returned unless requested**),
  `query` (FIQL: `parent.id`, `status.id/name`, `modificationDate`, `archived`, `news`,
  `visibility.*`, …), `language` (BCP 47). Response `{ "item": [KnowledgeItem], "prev"?, "next"? }`,
  200 (all) or 206 (partial). Accept `application/x.topdesk-kb-ki-list-v1+json`.
- `GET /knowledgeItems/{id|number}` (number e.g. `KI 0211`) — single. Accept
  `application/x.topdesk-kb-ki-v1+json`. **Warning from spec:** if no `translation.content.*` field
  is requested, the content block returns empty — so always request the content subfields.
- **KnowledgeItem** (selected fields): `id`, `number`, `parent{id,name}`,
  `translation{language, content{title, description(HTML), content(HTML), commentsForOperators,
  keywords(string)}}`, `visibility{sspVisibility(VISIBLE|NOT_VISIBLE|VISIBLE_IN_PERIOD),
  sspVisibleFrom/Until, publicKnowledgeItem}`, `urls{operator, ssp, public}` (relative, start `/`;
  `public`/`ssp` only when visible/public-feature-on), `status{id,name}` (a *searchlist*, not an
  enum), `modificationDate`/`creationDate` (ISO-8601 UTC), `availableTranslations:[bcp47]`, `news`,
  `manager`, `externalLink`.
- Content is **HTML** (`<b>`, `<a>`, `<img>`, `<br>` …). `keywords` is a single comma/space string.
- Errors: `400` `{errors:[{errorCode, appliesTo, errorMessage}]}`; `404` not found.
- `GET /knowledgeItemStatuses` — lists the status searchlist (id/name) if we need to resolve names.

---

## Phase 1 — Config + endpoint + auth tightening

- **`config.py`**:
  - Replace `TOPDESK_GRAPHQL_PATH` with `TOPDESK_KB_API_PATH` (default
    `/services/knowledge-base-v1`, plain env, comment → confirmed REST). Keep `TOPDESK_URL` etc.
  - **Add `TOPDESK_SYNC_SCOPE`** (PersistentConfig — **admin-editable at setup**, decision 1):
    `ssp` (default; SSP-visible items) | `public` (publicKnowledgeItem only) | `all` (every
    operator-readable item). Drives `_should_sync` in Phase 3.
  - No language config — **v1 syncs the tenant default language only** (decision 2).
- **Auth (`auth.py`) — drop the person-token fallback (decision 4).** The KB REST API is
  operator-Basic-only, so: `build_auth_header(username, app_password)` always emits
  `Basic base64(username:app_password)` (no `TOKEN id=` branch); `service_auth_configured()` now
  requires **URL + username (login) + app_password** (login no longer optional). The admin form's
  "Login name" field becomes effectively required (already labelled as such).
- **Helm**: `topdeskGraphqlPath` → `topdeskKbApiPath`; add `topdeskSyncScope`; mirror the existing
  additive pattern.
- **`/configs/topdesk` + status feature**: add `TOPDESK_SYNC_SCOPE` to the config GET/POST
  (enum-validated). No `/api/config` change.

## Phase 2 — Client rewrite (`topdesk_client.py`): GraphQL → REST

- Two base paths off `TOPDESK_URL`: KB calls → `{url}{TOPDESK_KB_API_PATH}`; auth probe →
  `{url}/tas/api/version`.
- Keep the **retry skeleton** verbatim (429 Retry-After, 5xx bounded backoff, **401 →
  `TopdeskAuthError` terminal**, connect/timeout → `TopdeskTransientError`/friendly). Drop
  `graphql()` + the GraphQL query constants. Replace `TopdeskGraphQLError` with a REST
  `TopdeskApiError` that parses `{errors:[{errorCode,appliesTo,errorMessage}]}` and surfaces the
  first `errorMessage` (keep the class exported for the router's HTTP mapping). Keep
  `TopdeskTransientError(status_code)`.
- **Field set constant** `_KI_FIELDS = 'parent,status,visibility,urls,language,title,description,content,keywords,creationDate,modificationDate,availableTranslations'` (centralized; one-place edit).
- Methods (Accept headers per spec):
  - `list_knowledge_items(*, start=0, page_size=100, query=None, language=None) -> dict` → GET
    `/knowledgeItems?start=&page_size=&fields=_KI_FIELDS&query=&language=`; returns
    `{item, prev, next}`.
  - `iter_all_knowledge_items(*, query=None, language=None) -> list[dict]` → page via `start`/
    `page_size=1000` until `item` empty / no `next`; hard page-count safety cap; sequential (polite).
  - `get_knowledge_item(identifier, *, language=None) -> dict|None` → GET `/knowledgeItems/{id|number}`
    with `fields=_KI_FIELDS`; 404 → None.
  - `list_item_children(parent_id, *, language=None) -> list[dict]` → `iter_all_knowledge_items(
    query=f'parent.id=={parent_id}')`.
  - `list_root_items(*, language=None) -> list[dict]` → items with no parent. **VERIFY FIQL for
    "no parent"** (`parent.id==null`? unsupported?) — fallback: `iter_all_knowledge_items()` then
    filter `not item.get('parent')` client-side. Flag in code + findings.
  - `probe() -> dict` → GET `/tas/api/version` (auth + reachability); fall back to a `page_size=1`
    KB list. Return `{ok, detail, version?}` for the router's test-connection.
- **No GraphQL `results`/`data` envelope** — REST returns the `{item,…}` object directly.

## Phase 3 — Worker field-mapping + publish filter (`sync_worker.py`)

- **Field mapping helpers.** Add small extractors mapping a REST KnowledgeItem to the worker's
  internal `file_info`:
  - title `← translation.content.title`; content(HTML) `← translation.content.content`;
    description `← translation.content.description`; keywords `← split(translation.content.keywords)`
    (comma/space → list); language `← translation.language`; number `← number`;
    status `← status.name`; modificationDate `← modificationDate` (top-level — **change hash
    unchanged**); web_url `← urls.public or urls.ssp or urls.operator` (prefix with `TOPDESK_URL`
    since urls are relative) — **drop `_build_item_url` construction**; parent `← parent.id`.
- **Publish/inclusion filter** (replaces `_is_published` status-enum). `status` is a customer
  searchlist (no guaranteed "PUBLISHED"); the structured signal is **`visibility`** +
  **`archived`**. Implement `_should_sync(item)` **driven by `TOPDESK_SYNC_SCOPE`** (decision 1):
  - always exclude `archived`.
  - `ssp` (default): `sspVisibility == VISIBLE`, or `VISIBLE_IN_PERIOD` within
    `sspVisibleFrom/Until`.
  - `public`: `visibility.publicKnowledgeItem == true`.
  - `all`: every operator-readable item (no visibility gate).
  - Record `status.name`/`sspVisibility` as metadata regardless.
  - Keep the existing "descend through any node, emit only includable" tree behaviour.
- **Change detection (decision 3)** — keep `modificationDate` item_map logic as-is (now reads the
  real field). Client-side hashing is the v1 path; server-side FIQL `modificationDate=gt=<last>` is
  a later optimization (not v1).
- **Tree** — `_walk_descendants` unchanged (now `list_item_children` = FIQL parent.id). `folder` =
  subtree, `file` = single item — unchanged.
- **Render** — `html_to_markdown` off-thread unchanged (content is HTML). Front-matter unchanged
  (fields now sourced from the mapping above). Byte-cap unchanged.
- **Language (decision 2)** — v1 syncs the **tenant default language** only: call list/get without a
  `language` param (TOPdesk returns the default-language translation). Store `availableTranslations`
  as metadata. Per-language fan-out is an explicit follow-up.
- **Attachments/images (decision 5) — deferred.** REST exposes `…/attachments` + `…/images`
  download endpoints; v1 ingests text content only. Not fetched.

## Phase 4 — Router + frontend

- **`/browse/items`** (`topdesk_sync.py`): map REST list/children to the existing
  `{id, name, number, has_children, status}` contract. `name ← translation.content.title or number`
  (request `fields=title`); `has_children` — REST gives no child count; keep **best-effort
  `True`** (picker already collapses on empty expand) OR a cheap `query=parent.id==<id>&page_size=1`
  count. Document the choice.
- **`/auth/test`** — `probe()` (version). Friendly 401/403/404 mapping (now from REST errors).
- **`/shared/*`** — unchanged (delegate to shared_kb helpers + `execute_sync`).
- **Frontend** — `TopdeskPickerModal` / `apis/topdesk` browse contract unchanged. **`TopdeskSection`
  gains a "Knowledge items to sync" dropdown** (decision 1) bound to `TOPDESK_SYNC_SCOPE`: *Visible
  in Self-Service Portal* (`ssp`, default) / *Public only* (`public`) / *All readable* (`all`), with
  a one-line helper. `apis/configs` get/setTopdeskConfig carry the new field; en-US + nl-NL i18n for
  the label/options. (Apply the boot-once / error-state hardening we did for the Confluence picker if
  the TOPdesk picker shares the retry-loop pattern — verify.)

## Phase 5 — Fixtures + tests

- **Rewrite fixtures** `test/services/topdesk/fixtures/*.json` to the REST shape: a list page
  (`{item:[…], next}`), a final page (`{item:[…]}` no next), a single item (full
  `translation.content` + visibility + urls + status + modificationDate), children-of-parent, empty
  page (`{item:[]}`), and a `400` error (`{errors:[{errorCode,appliesTo,errorMessage}]}`).
- **Client tests** — REST request shape (path, `fields`, `query`, `start/page_size`, Accept), offset
  paging via `next`, 401-terminal, 400-error parse, 429/5xx retry, transient.
- **Worker tests** — field mapping (nested → file_info), publish/visibility filter (SSP-visible vs
  not-visible vs archived), modificationDate change detection (update/skip/delete), tree walk via
  children, render output, front-matter incl. `urls.public`.
- **Router tests** — browse maps correctly; auth/test error mapping. Keep existing admin-gating.

## Phase 6 — Docs

- Update `thoughts/shared/research/2026-06-topdesk-api-verification.md`: replace the inferred GraphQL
  schema (§3–§6) with the **confirmed REST schema + auth**; mark the live-verification checklist
  items done that the specs now answer; leave open only "tenant serves KB-v1 + our login authenticates"
  (the discovery gate).

---

## Success criteria

- `scripts/topdesk_discover.py --login "<op>"` → `KB-v1` row 2xx (gate, with real creds).
- `pytest backend/open_webui/test/services/topdesk/` green against REST fixtures.
- `python -c "import open_webui.main"` OK; `npm run check|build` clean for changed FE files.
- Manual (Phase 4 of original plan, now against the REST API): test-connection passes; provision a
  small subtree; sync; items render with HTML→Markdown + front-matter (`urls.public` link);
  re-sync skips unchanged (modificationDate); a not-visible/archived item is excluded.

## Decisions (resolved 2026-06-15)

1. **Which items to sync?** **Configurable at setup** via `TOPDESK_SYNC_SCOPE` (admin dropdown):
   `ssp` (default) / `public` / `all`. Drives `_should_sync`.
2. **Language:** **tenant default only for v1** (no language param, no config). Per-language = follow-up.
3. **Incremental:** **client-side `modificationDate` hashing** for v1 (already built). Server-side
   FIQL filter = later optimization.
4. **Auth `TOKEN id=` fallback:** **dropped** — Basic-only, operator login required.
5. **Attachments/images:** **deferred** — text content only in v1.
