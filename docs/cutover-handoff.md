# OWUI cutover handoff (pt-2cut-owui-b)

The legacy v1-to-v2 migration CLI and chart Job are removed. The conductor must
carry the following into soev-solutions' resumable cutover tooling, not a product
API. Authority is the temporary migrator login from step 4 decision 3: present in
the cutover commit, removed in the reopen commit. Follow the tenant-owner handoff
and OWUI database relocation of step 4 §5.2; that login is not a member of OWUI's
runtime login. Read/export OWUI rows with the existing tenant-owner credentials
before relocation; write platform rows by direct SQL as the migrator. No runtime
role gains cross-database access.

Run these OWUI steps in order from `backend/`, with the app stopped:

1. As the schema migration login (`<db>_migrator`, allowed to SET ROLE
   `<db>_admin`), set `PGOPTIONS='-c role=<db>_admin'` and run
   `python -m open_webui.soev.migrate`. Supply the migration user's
   `DATABASE_USER`/`DATABASE_PASSWORD` and the tenant's database host/port/name,
   plus the app's settings, especially `VECTOR_DB=pgvector` and its dimensions,
   encryption and index options. Connection parts take precedence over
   `DATABASE_URL`; do not leave runtime parts set with a migration URL.
   `PGVECTOR_DB_URL`, if supplied, must also use the migration login. Pre-provision
   extensions when their creation is disabled. The command runs Alembic and the
   pgvector schema step, exits after completion, and prints JSON counts only.
2. Switch to the runtime login (`<db>_owui`), clear migration `PGOPTIONS`, set
   `ENABLE_DB_MIGRATIONS=false`, and run
   `python -m open_webui.soev.cutover --person-map person-map.json --group-map group-map.json --excluded excluded.json`.
   This person/group rewrite requires only runtime DML and table locks.
3. Start the app as the runtime login with `ENABLE_DB_MIGRATIONS=false` and the
   same vector settings. Startup verifies the vector schema without DDL. The
   chart uses `postgres.migration.user`, `passwordSecretRef.{name,key}`, and
   optional `role` for its init step; the app never receives those credentials.

Dry-run always rolls back; add `--apply` to commit after locks and post-checks. JSON output contains counts and outcomes only; explicitly excluded test/demo accounts remain unbound and cannot sign in or hold a platform session.

Evidence: soev-docs `architecture/platform/TARGET.md` §§4, 7, 16; master plan
`thoughts/plans/2026-10-23-target-implementation.md` §3; soev-solutions plans
`2026-10-23-pt-step2-persons.md` §6 item 8, `2026-10-24-pt-step4-plan.md` decisions
3 and 16, step 7 decision 7.4, and step 8's 2026-10-25 memory decision. These
references live in the sibling repositories; this is an implementation handoff.

| Removed OWUI step | Cutover disposition and exact source/target rows |
| --- | --- |
| Identity links | Delete outright. Never copy `identity_link_provenance`, signing keys, replay rows or product/person membership links. Resolve verified Keycloak identities into `control.principal_registry`, `principal_name`, `person`, `source_identity` and `door_use`. Before the group step and before reopening login, call `open_webui.soev.migrate_persons.cutover_persons(session, person_map, excluded_ids)` with the journal's OWUI-user-id to canonical `platform:user:<uuid>` map and explicit excluded OWUI-user-id list in the OWUI cutover transaction. It writes `user.person_id`, preserves the account and existing OAuth bindings, returns changed-user counts and makes no commit; a retry with the same map writes nothing. Unknown ids, users neither mapped nor excluded, overlapping mapped/excluded ids, duplicate persons, invalid refs and conflicting existing bindings (including bound exclusions) refuse before any person write; roll back the whole cutover transaction on refusal. The first unknown Keycloak login asks soev-api `/v1/me` and attaches only to the account already bound to that returned person. No uuid5 person ids, email matching or self-vouched proof. Pending/unproven accounts are quarantined by the platform. |
| Groups | Convert OWUI `group`/`group_member` 1:1 into organisation-owned platform groups, resolving eligible members through the verified person map (flattened membership, nesting depth at most 5). Persist 9g's `group_map` from OWUI group id to `platform:group:<id>`. Call `open_webui.soev.migrate_groups.cutover_groups(session, group_map)` in the OWUI cutover transaction to rewrite group ids, memberships and group `access_grant.principal_id` values while preserving per-group product settings; unmapped ids or conflicting targets refuse. Verify group counts, member equality, listed quarantines and resolving grants before starting the read-only `GET /v1/groups` mirror. Seed the platform Archive readers group and configure its ref as `SOEV_ARCHIVE_READERS_GROUP_REF`; OWUI admin roles never synthesize readership. No product group push or runtime directory-write capability remains. |
| Collections and grants | For nondeleted, nonexternal OWUI `knowledge` rows, preserve the KB id as `data.collection.key`, source kind, name, description and immutable embedding/chunking settings; map owner, readers and writers through the journal. Read `access_grant` for `resource_type=knowledge`: user `*` becomes the OWUI door audience, explicit users become persons, groups become mapped platform groups; writers retain read access. Local KBs retain the explicitly configured service reader. Personal cloud KBs retain no product shares/service reader. Reuse matching existing platform rows, refuse conflicting ownership/type and missing owners. |
| Folders | Read OWUI `knowledge_directory` and directory components of `knowledge_file.relative_path`; preserve local KB paths in `data.folder`, deduplicated by collection/path. Cloud trees come from their source; do not create empty legacy cloud folders. |
| Cloud setup and coverage | Preserve existing `data.connection`, `control.connection_binding`, `custody.connection_custody`/`connection_secret`, `data.schedule`, `control.grant_config`/`"grant"`, and `data.collection_schedule`, rewriting all owner/source refs and scope bindings through the journal. For legacy OneDrive `knowledge.meta.onedrive_sync.sources` not yet represented, create pending owner-bound connection/binding and corpus/schedule/subscriber rows by SQL: one connection per owner/provider, content and ACL-refresh schedules per canonical drive/item scope, preserving single-file/descendant semantics and label/path, using `control.sync_policy` cadence. No usable custody or enabled grant without proven source identity and fresh consent. Reconnect through the user's normal login after reopening. Google Drive is parked; Confluence is excluded. Count covered KBs through `collection_schedule`, not the corpus key. |
| Local KB re-ingest | Do not commission jobs through OWUI. Reconcile each OWUI `knowledge_file`/`file` against `data.collection`/`data.data_item` by collection key and source id. Copy existing documents, chunks in `data.chunk_<space.key>`, and original objects, preserving version, digest, paths, ACLs and embedding-space identity while restamping mapped principals. If only legacy originals/vectors exist, the cutover tooling must explicitly import them into those tables/object storage; SQL cannot create embeddings from bytes. Missing originals or incompatible vectors must be reported and block reopening until the conductor resolves them. |
| Chat, folder and assistant uploads | Enumerate nondeleted `chat.chat` files at chat level, `history.messages[*].files` and legacy `messages[*].files`, plus `folder.data.files` and explicit `model.meta.knowledge` file entries. Exclude images, KB entries and reference attachments from byte import. Join `file`/`knowledge_file`/`user`; refuse assistant files not owned by the model owner. Populate `data.collection`/`data.data_item` plus matching chunks/original objects for `owui-attachments-<user id>`, `owui-assistant-<model id>` and shared `owui-folder-<folder id>` targets as appropriate, with mapped owner/audience. Rewrite retained File metadata (`collection_name`, `soev_collection_key`, `soev_job`) and imported thread attachment references consistently. Deduplicate existing documents; report gone rows, missing originals and failed/in-flight jobs; no autonomous impersonation or fabricated success. |
| Memories | Delete re-embedding outright. Copy OWUI `memory` rows and existing `user-memory-<user id>` vectors into the platform memory contract with mapped ownership and owner-only labels; no known provenance means owner-only fallback. The step 7 memory unit owns destination DDL (not specified by this lane); use its schema, never invent a runtime door. Verify bge-m3 model identity, dimensions, ids and row/vector coverage per tenant; a mismatch fails the rehearsal. Drop local rows/vectors only after verified copy. |
| Config/model rewrite and sign-out | Move the former local-only steps into the cutover transaction/journal: snapshot OWUI `config`; apply reviewed feature settings, retire inference settings; map model ids in `chat.chat`, `model.id`/`base_model_id`, model `access_grant.resource_id`, `user.settings`, automations and config model lists. Clear legacy default selections; preserve assistants and select the global system prompt explicitly when distinct prompts conflict. Retain existing `owui_v2_migration` state as historical evidence, not a runtime command. Invalidate OWUI sessions at reopening; platform access still requires each person's own Keycloak token. Rollback is the coordinated image/DB/object backup restore, not this removed CLI's `--restore`. |

Before reopening, record durable phase completion in `control.cutover_phase`:
zero unmapped principal-bearing values, exact per-collection source-id/path and
row/vector/object coverage, all cloud subscription/binding relationships intact,
all chat branches/attachments accounted for, no pending upload/OAuth sessions, and
no migration-only privileges remaining. Never rerun the old API reconciliation
as users. Actual counts and unresolved imports belong in the rehearsal report;
this change does not perform a tenant cutover.

## Startup DDL inventory (owui-db-role-1116)

With `VECTOR_DB=pgvector`, `AGENT_API_ENABLED=true`, and `SOEV_API_URL` set,
the only database DDL outside Alembic is in `retrieval/vector/dbs/pgvector.py`:

| Operation | Migration/runtime handling |
| --- | --- |
| `CREATE EXTENSION vector` in a conditional DO block | Migration only, still gated by `PGVECTOR_CREATE_EXTENSION`; runtime checks `pg_extension` even when creation is disabled. |
| `CREATE EXTENSION pgcrypto` in a conditional DO block | Migration only when `PGVECTOR_PGCRYPTO=true`; runtime checks the extension when encryption is enabled. |
| `Base.metadata.create_all` for `document_chunk` (including its primary key) | Migration only; runtime checks table/column existence and vector type/dimensions. |
| `CREATE INDEX idx_document_chunk_vector` (ivfflat or HNSW, vector or halfvec) | Migration only; runtime checks that the index is ready/valid and has the configured method. |
| `CREATE INDEX idx_document_chunk_text_search` (GIN) | Migration only for unencrypted text; runtime checks it in that mode. |
| `CREATE INDEX idx_document_chunk_collection_name` (btree) | Migration only; runtime checks it. |

The migration entry point runs those operations after Alembic. Startup skips all
of them with `ENABLE_DB_MIGRATIONS=false`; missing vector objects fail before
serving requests with their names and the migration command. The audit found no
active Peewee runner or other application `create_all` outside vector adapters.

Other SQL vector adapters also contain DDL, but are not imported with
`VECTOR_DB=pgvector` and are unchanged: OpenGauss creates `document_chunk` and
its vector/collection indexes; MariaDB creates `document_chunk` with inline
vector/collection indexes (and uses TRUNCATE for its explicit reset operation);
Oracle creates `document_chunk`, `document_chunk_collection_name_idx`, and
`document_chunk_vector_ivf_idx`. Non-SQL vector backends retain their own remote
collection/index provisioning; they are outside this PostgreSQL startup path.

## Platform deletion feed contract (owui-db-role-1116)

`backend/open_webui/soev/retention.py` polls
`GET <SOEV_API_URL>/v1/retention/deletions?limit=100`, adding `after=<cursor>`
once a cursor has been stored. `build_service_client()` sends
`Authorization: Bearer <SOEV_API_KEY>` as product/service authority. It sends no
`X-Soev-User-Token` and no service-principal header; `SOEV_API_SERVICE_PRINCIPAL`
does not change this request's authentication.

The expected response is a JSON object with `data` (a list of objects containing
`thread_id`) and `next_cursor` (a cursor or null). On the final page, the last
`thread_id` becomes the high-water mark; an empty page keeps the previous cursor.
OWUI persists that cursor and soft-deletes a local chat only when all its
bookmarked platform threads have been deleted. The worker retries every 60
seconds while v2 is enabled and platform thread storage is disabled. A 404 is
logged and retried; it does not indicate an empty feed. Check the platform route,
service credential's access, and any ingress path prefix against this contract.
This is an investigation finding; polling behavior is unchanged.

## Document originals: report and explicit retirement

No Alembic revision, startup path or chart migration container retires document rows
or bytes. The unshipped destructive revision `f286d9b43a71` is removed. A successful
base-code ingest cleared `soev_collection_key`; an empty key is never evidence that
the platform lacks a document. This command replaces the former SQL preflight.

With the application stopped and its usual tenant database/platform configuration
and uploads volume available, run **once per owner**, with that owner's verified login, and retain counts:

```sh
python -m open_webui.soev.retire_document_bytes --owner-id USER_ID --login-token-stdin
```

The command disables schema migrations before loading application config, including
when the inherited environment enables them. The default only reports: `platform_held`, `platform_differs`, `unknown_with_bytes`,
`unknown_without_bytes`, `protected`, `in_flight`, `unverified_storage`, and
`platform_error`. Each file is looked up through public `GET /v1/documents?source_id`
and an original is opened to verify publication before any byte retirement. A held
document must have the row’s recorded SHA256 and byte size (platform `byte_size`);
if local bytes exist, their computed SHA256 and size must match too. A different
platform version or local mismatch is `platform_differs` and is never touched.
“Unknown” means absent from that caller's readable collections, never proof of
absence throughout the platform. Every run requires that owner's login token, verified against `/v1/me`;
service authority and unscoped runs are refused. Counts contain no identifiers or URLs.
Files linked to chats, knowledge bases, skills, assistants or folders are conservatively
protected, including shared-chat snapshots after the original link is removed. Their
bytes stay even when the platform already holds them, preserving every fork reader.

For a reviewed application, use the exact same predicate with explicit `--apply`:

```sh
python -m open_webui.soev.retire_document_bytes --owner-id USER_ID --login-token-stdin --apply
```

This removes **only local bytes** of positively verified published documents and
sets their local paths to null. Each row update commits in its own transaction
**before** unlinking bytes. A private `soev_retire_path` journal lets an explicit
repeat recover an unlink failure or ambiguous commit; no startup worker processes
it. A later row failure cannot roll back earlier committed path updates. It never
deletes a platform-held File row. To act
on an owner's empty unknown rows, supply that owner's real current platform login
token through stdin (never a command argument or file), verified against `/v1/me`:

```sh
python -m open_webui.soev.retire_document_bytes --owner-id USER_ID --login-token-stdin
python -m open_webui.soev.retire_document_bytes --owner-id USER_ID --login-token-stdin --apply
```

Rows may be removed only when no bytes exist, the owner's platform lookup is empty,
no chat/knowledge/skill/assistant/folder link exists, no extracted `data.content`
would be lost, and there is no historical success/source marker.
Base-code completed rows remain even if their platform copy is unreadable. Jobs in
flight, images detected from bytes (including missing/octet-stream MIME), videos,
skill-linked files, generated audio, exports and internal local blobs are protected.
Remote storage URLs, paths outside the uploads root, unreadable paths and shared
paths are not unlinked (including another row’s retirement journal). Platform errors cause no action on that file. Unknown files
with bytes remain on disk until the operator imports or otherwise resolves them;
this command never discards their bytes. Keep the coordinated database/object backup;
byte retirement cannot be rolled back through SQL.

Original reads first use `soev_read_collection`, set at the first successful
publication. Later assistant/folder/knowledge copies never replace it. Base rows
recover through source-id lookup **as the requesting person**, restricted to the
owner’s attachments collection and locally recorded knowledge/assistant/folder
links. Both recorded SHA256 and size must match; a missing recorded hash disables
platform resolution without any remote hashing. Legacy `collection_name` is not
a platform key. Only a verified, locally derived first source is pinned. If that
collection returns 404, a verified alternative may serve that request, but a
reader’s 404 never changes an existing pin. A platform 403 never falls back to local bytes; an unknown legacy original
with local bytes retains the existing owner/chat/knowledge checks. Images sent for
extraction and videos continue to display from local bytes.

Strict egress requires the public `soevApi.url` hostname and
`soevApi.transferStoreHost` (the exact presigned store destination). The chart
passes the store setting as `SOEV_TRANSFER_STORE_HOST` and derives the hostname
for its FQDN selector. Bare host[:port] means HTTPS, default port 443; explicit
store origins select scheme and port for internal development stores. HTTP is
permitted only when that store origin explicitly says HTTP, independently of
the API URL. Uploads fail closed without a store setting. The CI compose base
sets `http://s3:9000`, matching the platform overlay's ACL store endpoint.

Transfer reconciliation skips transfers active in this process and waits for the
job’s `expires_at` (15 minutes), not its upload capability’s 60-second expiry.
Only the relay that finished uploading commits. An abandoned upload fails and
cancels at the job deadline; polling never guesses whether an upload is complete.
Failed copies record their destination outcome in server-owned
`File.meta.soev_copies`, preserving the completed original’s status and read source.
Platform KB memberships are locally recorded in `soev_kb_links` after authorized
association or accepted ingest; legacy `knowledge_file` links also count.

The extracted-text update route refuses platform documents with a translated 409,
because resubmitting their source id replaces their only original. Local-only
files and sniffed image/video media keep the existing behavior.

Skill replacement/deletion unlinks and removes an otherwise unused File in one
database transaction. Physical bytes are purged only after commit and a fresh
check that neither its link, File nor another File holding its path survives. An
exception after a successful commit therefore never deletes linked bytes. Files
with another chat, knowledge, assistant, folder or skill holding remain.
