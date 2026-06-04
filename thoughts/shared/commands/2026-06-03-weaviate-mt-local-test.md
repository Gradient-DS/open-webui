# Weaviate Native Multi-Tenancy — Local Test Runbook

**Date:** 2026-06-03
**Branch:** `feat/weaviate-tenancy` (open-webui + genai-utils)
**Scope:** Phases 1–2 only (open-webui MT connector + genai-utils adapter). The migration
Job (Phase 4) is **not built** — "migrate old KBs" is not testable yet; dual-read covers old data.
**Goal:** Confirm (A) zero behavior change with the flag off, (B) new KBs become **tenants**
under 5 fixed collections, (C) existing legacy KBs stay retrievable via the dual-read shim.

All commands are single-line and copy-pasteable. Weaviate is exposed by
`docker-compose.soev-dev.yaml` on **HTTP `:8082`** / **gRPC `:50053`**; Postgres on `:5433`.
Open WebUI runs as a **host process** (`open-webui dev`), so the flags go on the **backend env**,
not the compose file.

---

## 0. Bring the stack up & confirm Weaviate is healthy

Start (or confirm) the dev stack:

```
docker compose -f docker-compose.soev-dev.yaml up -d
```

Containers up + health status:

```
docker compose -f docker-compose.soev-dev.yaml ps
```

Weaviate **readiness** (HTTP 200 + empty body = ready):

```
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8082/v1/.well-known/ready
```

Weaviate **liveness**:

```
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8082/v1/.well-known/live
```

Version + enabled modules (`/v1/meta`):

```
curl -s http://localhost:8082/v1/meta | python3 -c 'import sys,json; m=json.load(sys.stdin); print("version:", m["version"]); print("modules:", list(m.get("modules",{}).keys()))'
```

Node / shard / object-count overview (`/v1/nodes?output=verbose`):

```
curl -s "http://localhost:8082/v1/nodes?output=verbose" | python3 -m json.tool
```

If anything looks wrong, tail the Weaviate logs:

```
docker compose -f docker-compose.soev-dev.yaml logs --tail 50 weaviate
```

Postgres reachable (optional sanity):

```
docker compose -f docker-compose.soev-dev.yaml exec postgres pg_isready -U openwebui
```

---

## Inspection cheat-sheet (use throughout)

List all classes currently in the DB:

```
curl -s http://localhost:8082/v1/schema | python3 -c 'import sys,json; print([c["class"] for c in json.load(sys.stdin)["classes"]])'
```

Is a given collection multi-tenant? (look for `multiTenancyConfig.enabled: true`):

```
curl -s http://localhost:8082/v1/schema/Knowledge | python3 -c 'import sys,json; print(json.load(sys.stdin).get("multiTenancyConfig"))'
```

List tenants under a collection (each KB/file/etc = one tenant):

```
curl -s http://localhost:8082/v1/schema/Knowledge/tenants | python3 -c 'import sys,json; print([t["name"] for t in json.load(sys.stdin)])'
```

(Repeat with `File`, `WebSearch`, `UserMemory`, `HashBased` as needed.)

Peek objects in a specific tenant (replace `<TENANT>` with a KB UUID from the list above):

```
curl -s "http://localhost:8082/v1/objects?class=Knowledge&tenant=<TENANT>&limit=3" | python3 -m json.tool
```

Count objects in a tenant via GraphQL (MT collections REQUIRE the `tenant:` arg):

```
curl -s http://localhost:8082/v1/graphql -H 'Content-Type: application/json' -d '{"query":"{ Aggregate { Knowledge(tenant:\"<TENANT>\") { meta { count } } } }"}' | python3 -m json.tool
```

Peek objects in a LEGACY per-class collection (no tenant — replace `<CLASS>` e.g. `C<uuid>`):

```
curl -s "http://localhost:8082/v1/objects?class=<CLASS>&limit=3" | python3 -m json.tool
```

---

## The flags (only ONE toggles behavior)

| Env var | Where | Meaning |
|---|---|---|
| `ENABLE_WEAVIATE_MULTITENANCY_MODE` | OWUI backend | **master switch.** unset/`false` = legacy per-class connector; `true` = MT connector. |
| `WEAVIATE_MT_LEGACY_FALLBACK` | OWUI backend | only read when MT is ON. Keep `true` for local testing (enables dual-read over existing data). |

> The genai-utils agent stack has its own mirror flags (`weaviate_multitenancy_enabled`,
> `weaviate_mt_legacy_fallback` in the `openwebui_direct` provider config). Only needed if you're
> testing **agent** search too — the OWUI retrieval tests below don't require it.

---

## Phase A — Baseline (flag OFF) → expect ZERO change

1. Start the backend with the flag **unset/false**:

```
open-webui dev
```

2. In the UI: open an existing KB and run a query; upload a file to a KB; do a chat-file attach; run a web search; trigger a memory write. All should behave exactly as today.

3. Confirm storage is the legacy per-class layout (many `C<uuid>` / `File_*` / `Web_search_*` classes):

```
curl -s http://localhost:8082/v1/schema | python3 -c 'import sys,json; print([c["class"] for c in json.load(sys.stdin)["classes"]])'
```

✅ **Pass:** behavior identical to normal; schema shows per-class collections.

---

## Phase B — New KB under MT (flag ON) → expect TENANTS, not classes

1. Stop the backend, then restart with MT on:

```
ENABLE_WEAVIATE_MULTITENANCY_MODE=true WEAVIATE_MT_LEGACY_FALLBACK=true open-webui dev
```

2. In the UI: **create a NEW knowledge base** and upload a file to it.

3. Confirm new data landed in the 5 fixed collections, NOT a new per-KB class:

```
curl -s http://localhost:8082/v1/schema | python3 -c 'import sys,json; print([c["class"] for c in json.load(sys.stdin)["classes"]])'
```

(Expect to see `Knowledge` / `File` etc. appear; no new `C<uuid>` for this KB.)

4. Confirm the KB became a **tenant** under `Knowledge` (its name = the KB UUID):

```
curl -s http://localhost:8082/v1/schema/Knowledge/tenants | python3 -c 'import sys,json; print([t["name"] for t in json.load(sys.stdin)])'
```

5. Confirm the tenant holds chunks (replace `<TENANT>` with the UUID from step 4):

```
curl -s "http://localhost:8082/v1/objects?class=Knowledge&tenant=<TENANT>&limit=3" | python3 -m json.tool
```

6. In the UI: run retrieval/chat over the new KB — should return its chunks.

✅ **Pass:** new KB = a tenant under `Knowledge`; `/v1/schema` stays at the fixed 5–6 classes for new data; retrieval works.

---

## Phase C — Existing (legacy) KB under MT → dual-read fallback

1. Still running with the flag ON. In the UI, open an **existing** KB (created before MT) and run retrieval/chat. **Do NOT upload new files to it** (see gotcha below).

2. The MT tenant for that KB is empty, so the connector falls back to the legacy `C<uuid>` class and returns the old data. Verify the old data still lives in its legacy class:

```
curl -s http://localhost:8082/v1/schema | python3 -c 'import sys,json; print([c["class"] for c in json.load(sys.stdin)["classes"] if c["class"].startswith("C")])'
```

✅ **Pass:** retrieval over the old KB still returns results (served via dual-read from the legacy class).

---

## ⚠️ Gotcha — dual-read is "MT-first, else legacy", NEVER merged

If you add a new file to a **legacy** KB while MT is on, the new chunks go into the MT tenant →
the tenant is no longer empty → dual-read stops firing → retrieval returns **only the new chunks,
and the old legacy chunks become invisible** until they're migrated into the tenant.

This is expected behavior of this design (migration is meant to run before heavy new writes), not a bug.
**To test cleanly:** treat legacy KBs as read-only under MT; put new data in new KBs.

---

## Revert (back to legacy, data intact)

Stop the backend and restart **without** the flag:

```
open-webui dev
```

You're back on the legacy connector; all existing per-class data is untouched.

---

## Not testable yet

- **Migrating old KBs into tenants** — the collection→tenant migration Job is Phase 4 (soev-gitops)
  and has **not been built**. Dual-read (Phase C) is what keeps old data reachable without migrating.
  Ask Claude to build a local `migrate.py` if you want to exercise the real migration path.
- **Weaviate 1.37.7** — the dev compose pins `1.35.0`, which supports MT fine. 1.37.7 only matters for
  production scale (lazy shard-load, INACTIVE-tenant backups). To smoke the target version, edit
  `docker-compose.soev-dev.yaml:9` to `semitechnologies/weaviate:1.37.7`; for a clean run wipe the
  volume first: `docker compose -f docker-compose.soev-dev.yaml down -v` then `up -d`.

---

## Quick reference — what "good" looks like

| Check | Flag OFF | Flag ON (new data) |
|---|---|---|
| `/v1/schema` class list | many `C<uuid>` / `File_*` / `Web_search_*` | ≤6 fixed: `Knowledge`, `File`, `WebSearch`, `UserMemory`, `HashBased`, `Knowledge_bases` |
| KB identity | a class | a **tenant** under `Knowledge` |
| `/v1/schema/Knowledge` `multiTenancyConfig.enabled` | (collection absent) | `true` |
| Existing-KB retrieval | works (per-class) | works (dual-read from legacy class) |
