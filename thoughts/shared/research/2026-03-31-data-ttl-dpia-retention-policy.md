---
date: 2026-03-31T15:22:00+02:00
researcher: Claude
git_commit: d228b1cdd85cc1288b20ad26bbbe20d568a803f7
branch: feat/dpia
repository: open-webui
topic: "Configurable data TTL / retention policy for DPIA compliance"
tags: [research, codebase, dpia, gdpr, ttl, retention, soft-delete, data-lifecycle]
status: complete
last_updated: 2026-03-31
last_updated_by: Claude
last_updated_note: "Added lawful retention period defaults from EU/Dutch regulatory sources"
---

# Research: Configurable Data TTL / Retention Policy for DPIA Compliance

**Date**: 2026-03-31T15:22:00+02:00
**Researcher**: Claude
**Git Commit**: d228b1cdd85cc1288b20ad26bbbe20d568a803f7
**Branch**: feat/dpia
**Repository**: open-webui

## Research Question

How to implement a configurable general TTL on all soev data (soft-delete after a term, e.g. 2 years), configurable via env/Helm? What triggers make sense from a DPIA assessment perspective?

## Summary

The codebase already has substantial data lifecycle infrastructure: soft-delete on chats/knowledge/channels, a GDPR user archive system with configurable retention, a cleanup worker processing deferred deletions every 60 seconds, and `last_active_at` tracking on all authenticated requests. A general data TTL feature can build on these existing patterns. From a DPIA perspective, the trigger should be multi-dimensional: user inactivity (no login), data staleness (not accessed/modified), and archive age — each with a configurable period.

## Detailed Findings

### 1. Existing Data Entities and Their Lifecycle State

The codebase has **20+ database tables**. Here's a lifecycle classification:

#### Already have soft-delete (`deleted_at` column)
| Table | Soft-delete | Archive | TTL/Expiry |
|-------|------------|---------|------------|
| `chat` | `deleted_at` | `archived` boolean | None |
| `knowledge` | `deleted_at` | — | 30-day suspension TTL (cloud KBs only) |
| `channel` | `deleted_at` + `deleted_by` | `archived_at` + `archived_by` | None |

#### Have expiry mechanisms
| Table | Mechanism |
|-------|-----------|
| `user_archive` | `expires_at` computed from `retention_days`, daily cleanup |
| `invite` | `expires_at`, `revoked_at` |
| `oauth_session` | `expires_at` (no cleanup worker) |
| `api_key` | `expires_at`, `last_used_at` |

#### No lifecycle management (hard-delete only or no delete)
- `user`, `auth`, `file`, `folder`, `feedback`, `memory`, `note`, `prompt`, `prompt_history`, `message` (channel messages), `message_reaction`, `model`, `function`, `skill`, `tool`, `tag`, `group`, `group_member`, `access_grant`, `recovery_code`

### 2. Key Timestamp Fields for TTL Triggers

| Entity | Created | Updated | Last Active/Used | Archived |
|--------|---------|---------|------------------|----------|
| User | `created_at` | `updated_at` | `last_active_at` (throttled, every request) | — |
| Chat | `created_at` | `updated_at` (every message) | — | `archived` boolean |
| Knowledge | `created_at` | `updated_at` | — | — |
| File | `created_at` | `updated_at` | — | — |
| Channel | `created_at` | `updated_at` | — | `archived_at` |
| API Key | `created_at` | `updated_at` | `last_used_at` | — |

### 3. Existing Background Workers

Six workers run during app lifespan (`main.py:763-784`):

1. **Usage pool cleanup** — reaps expired WebSocket entries
2. **Session pool cleanup** — reaps orphaned sessions
3. **Archive expiry cleanup** — daily, deletes expired `user_archive` rows
4. **OneDrive sync scheduler** — periodic cloud sync
5. **Google Drive sync scheduler** — periodic cloud sync
6. **Deletion cleanup worker** — every 60s, processes soft-deleted KBs/chats + expired suspensions

### 4. Configuration Pipeline Pattern

```
Helm values.yaml (camelCase) → configmap.yaml (SCREAMING_SNAKE) → env var → config.py (PersistentConfig) → app.state.config → /api/config → frontend
```

Duration settings follow naming: `{FEATURE}_{UNIT}` (e.g., `DEFAULT_ARCHIVE_RETENTION_DAYS`, `INVITE_EXPIRY_HOURS`, `TWO_FA_GRACE_PERIOD_DAYS`).

Existing retention configs:
- `DEFAULT_ARCHIVE_RETENTION_DAYS` = 1095 (3 years)
- `AUTO_ARCHIVE_RETENTION_DAYS` = 365 (1 year)
- `SUSPENSION_TTL_DAYS` = 30 (hardcoded constant)

## DPIA Assessment: What TTL Triggers Make Sense?

### Relevant GDPR Principles

Under GDPR Article 5(1)(e) — **storage limitation** — personal data must be kept "no longer than is necessary for the purposes for which the personal data are processed." A DPIA (Article 35) for a Dutch public sector AI chat platform should define:

1. **Purpose limitation**: Chat data exists for user utility. When the user stops using the service, the purpose ceases.
2. **Data minimization**: Retain only what's actively needed.
3. **Proportionality**: 2 years is a common baseline in Dutch public sector DPIAs (matching the AP's practical guidance for service data).

### Recommended TTL Triggers (Multi-Dimensional)

A single trigger is insufficient — different data types have different lifecycle characteristics:

#### Trigger 1: User Inactivity → Account + Data Soft-Delete
- **Metric**: `user.last_active_at` — already tracked on every authenticated request
- **Recommended default**: 730 days (2 years) of no login
- **Action**: Soft-delete user account (archive first if `ENABLE_USER_ARCHIVAL` is on), cascade soft-delete all user data
- **Rationale**: Primary DPIA trigger. If a user hasn't logged in for 2 years, the processing purpose has lapsed. This is the strongest legal basis for automated cleanup.
- **Warning flow**: Consider 30-day advance email notification (if email is on file)

#### Trigger 2: Chat Data Staleness → Chat Soft-Delete
- **Metric**: `chat.updated_at` (last message or modification)
- **Recommended default**: 730 days (2 years) since last update
- **Action**: Soft-delete chat (sets `deleted_at`, cleanup worker handles cascade)
- **Rationale**: Individual chats may go stale while the user remains active. Old chat data with potentially sensitive prompts/responses should not be retained indefinitely.
- **Note**: `chat.archived` is user-initiated. An archived chat still counts as "retained" for DPIA purposes — archiving does not reset the clock.

#### Trigger 3: File/Document Staleness → File Cleanup
- **Metric**: `file.updated_at` (or `knowledge.updated_at` for KB-attached files)
- **Recommended default**: Inherit from parent (chat or knowledge base TTL)
- **Action**: Files orphaned by chat/KB deletion are already cleaned up by `delete_orphaned_files_batch()`
- **Rationale**: Files don't need independent TTL — they cascade from their parent entity.

#### Trigger 4: User Archive Expiry (Already Implemented)
- **Metric**: `user_archive.expires_at`
- **Current default**: 1095 days (3 years)
- **Already works**: Daily cleanup in `periodic_archive_cleanup()`

#### Trigger 5: Inactive Knowledge Bases
- **Metric**: `knowledge.updated_at`
- **Recommended default**: Same as chat TTL (730 days)
- **Action**: Soft-delete KB (cleanup worker handles cascade)
- **Rationale**: KBs containing potentially sensitive documents should not persist unused.

### What NOT to TTL
- **User archives**: Already have their own retention mechanism with `never_delete` override
- **Admin/system config**: Models, functions, tools, prompts — these are operational, not personal data
- **Channels**: Team resources, different lifecycle than personal data

### Recommended Configuration Variables

| Env Var | Helm Key | Default | Purpose |
|---------|----------|---------|---------|
| `DATA_RETENTION_TTL_DAYS` | `dataRetentionTtlDays` | `0` (disabled) | Master TTL for all user data |
| `USER_INACTIVITY_TTL_DAYS` | `userInactivityTtlDays` | `730` | Soft-delete inactive users |
| `CHAT_RETENTION_TTL_DAYS` | `chatRetentionTtlDays` | `0` (inherit from master) | Override for chat-specific TTL |
| `KNOWLEDGE_RETENTION_TTL_DAYS` | `knowledgeRetentionTtlDays` | `0` (inherit from master) | Override for KB-specific TTL |
| `DATA_RETENTION_WARNING_DAYS` | `dataRetentionWarningDays` | `30` | Days before TTL to warn user |

A `0` value means "use master TTL" (for entity-specific) or "disabled" (for master). This keeps it simple while allowing per-entity overrides.

### Implementation Architecture

The cleanest approach builds on the existing cleanup worker:

```
New periodic task: periodic_data_retention_cleanup()
  ├── Runs daily (like archive cleanup)
  ├── Phase 1: Find inactive users (last_active_at < now - USER_INACTIVITY_TTL_DAYS)
  │   ├── Optional: send warning email if within WARNING_DAYS window
  │   ├── Archive user if ENABLE_USER_ARCHIVAL
  │   └── DeletionService.delete_user() (existing cascade)
  ├── Phase 2: Find stale chats (updated_at < now - CHAT_RETENTION_TTL_DAYS, user still active)
  │   └── Chats.soft_delete_by_id() (cleanup worker handles rest)
  └── Phase 3: Find stale KBs (updated_at < now - KNOWLEDGE_RETENTION_TTL_DAYS, user still active)
      └── Knowledges.soft_delete_by_id() (cleanup worker handles rest)
```

This reuses:
- `DeletionService.delete_user()` for full user cascade (already two-phase: fast + deferred)
- `Chats.soft_delete_*` + cleanup worker for chat cascade
- `Knowledges.soft_delete_*` + cleanup worker for KB cascade
- `ArchiveService.create_archive()` for pre-deletion archive
- The existing `PersistentConfig` + Helm pipeline for configuration

### Admin UI Considerations

Add a "Data Retention" section to the existing admin panel (possibly under the existing Security or General tab):
- Master TTL toggle + days input
- Per-entity override inputs
- Warning period configuration
- Display of "next scheduled cleanup" and "users/chats approaching TTL"

## Code References

- `backend/open_webui/models/users.py:78` — `last_active_at` column definition
- `backend/open_webui/models/users.py:591-602` — `update_last_active_by_id()` with throttle
- `backend/open_webui/models/chats.py:47-52` — Chat lifecycle columns (`deleted_at`, `archived`)
- `backend/open_webui/models/chats.py:1604-1626` — Chat soft-delete methods
- `backend/open_webui/models/knowledge.py:143` — `SUSPENSION_TTL_DAYS = 30`
- `backend/open_webui/models/knowledge.py:756-778` — Knowledge soft-delete methods
- `backend/open_webui/models/user_archives.py:18-55` — Archive model with retention/expiry
- `backend/open_webui/services/deletion/service.py:383-559` — `delete_user()` cascade
- `backend/open_webui/services/deletion/cleanup_worker.py:59-187` — Cleanup worker processing
- `backend/open_webui/services/archival/service.py:104-162` — Archive creation
- `backend/open_webui/main.py:699-712` — Periodic archive cleanup task
- `backend/open_webui/main.py:763-784` — All background task startups
- `backend/open_webui/config.py:1699-1721` — Archive retention config (PersistentConfig pattern)
- `helm/open-webui-tenant/values.yaml:110-396` — Helm configuration values
- `helm/open-webui-tenant/templates/open-webui/configmap.yaml` — Helm-to-env mapping

## Architecture Insights

1. **Soft-delete + cleanup worker pattern is already established** — chats and KBs use `deleted_at` with a 60-second worker processing deferred cascade. New TTL logic just needs to *set* `deleted_at`; the existing worker handles the rest.

2. **User deletion is already two-phase** — fast path (soft-delete relational data, hard-delete simple tables) + deferred path (vector DB, storage cleanup). No new cascade logic needed.

3. **`last_active_at` is already comprehensive** — updated on JWT auth, API key auth, and WebSocket heartbeat. Optional throttle via `DATABASE_USER_ACTIVE_STATUS_UPDATE_INTERVAL`. This is the ideal inactivity metric.

4. **Archive-before-delete already exists** — `DeletionService` supports optional archival. The TTL worker just needs to call the same archive flow.

5. **PersistentConfig allows runtime adjustment** — admins can change TTL values without redeployment, important for responding to DPIA reviews or AP (Autoriteit Persoonsgegevens) guidance changes.

## Open Questions

1. **Should archived chats reset the TTL clock?** — When a user archives a chat, does that count as "last use"? DPIA-wise, archiving is a user action (interaction), so it could reset the clock. But the user's *intent* is to put it away, not to keep it forever.

2. **Warning mechanism** — Should the system send email warnings before TTL deletion? This requires email integration (Microsoft Graph is already in place for invites). What's the right UX for "your data will be deleted in 30 days"?

3. **Admin override per user** — Should admins be able to exempt specific users from TTL (e.g., service accounts)? The `never_delete` pattern on `user_archive` could be extended to a user-level flag.

4. **Audit trail** — GDPR requires demonstrating compliance. Should TTL deletions be logged to an audit table? The archive system already captures snapshots, but automated TTL deletions of chats/KBs without archival need a trail.

5. **Channel data** — Channels are collaborative. Should channel messages follow owner TTL, or stay until the channel is deleted? Multi-user data retention is more complex from a DPIA perspective.

## Follow-up Research: Lawful Retention Period Defaults

### Key Regulatory Sources

There is no single "correct" retention period under GDPR — the law deliberately requires justification per purpose rather than prescribing numbers. However, strong regulatory precedents and Dutch legal floors exist.

### Hard Legal Floors (Dutch Law)

| Law | Applies To | Period |
|-----|-----------|--------|
| **AWR (Algemene wet inzake rijksbelastingen)** | Financial/tax records, business administration | **7 years** |
| **Archiefwet 2021** (new version effective ~July 2026) | Government archival records | Per selectielijst (1 day to permanent); transfer to National Archives after **10 years** (down from 20) |
| **AVG/GDPR Art. 17** | Erasure requests | "Without undue delay" — practically **30 days** |
| **BW Art. 3:307-3:310** | Limitation of legal claims | **5 years** (general) / **20 years** (professional liability) |
| **Selectielijst gemeenten 2020** | Municipal digital communications | Varies; email: **7-10 years**; key function emails: potentially **permanent** |

### EU DPA Enforcement Precedents

- **CNIL (France) — Discord case (SAN-2022-020)**: EUR 800k fine for retaining 2.4M French accounts inactive for 3+ years. Discord now deletes after **2 years of inactivity**. This is the strongest precedent for user account TTL.
- **EDPS GenAI Orientations v2 (October 2025)**: Establishes **30 days** as reference for AI conversation content retention for EU institutions. Most directly applicable to an AI chat platform.
- **EDPB 2025 Coordinated Enforcement**: Found lack of automated deletion as a "persistent weakness" across data controllers.

### SaaS Industry Benchmarks

| Provider | Data Type | Default Retention |
|----------|-----------|-------------------|
| **Microsoft 365** | Audit logs | 180 days (standard), 1 year (E5), up to 10 years |
| **Microsoft 365** | Deleted user content | 30 days active, 180 days passive |
| **Google Workspace** | Audit/admin logs | 6 months |
| **Slack (paid)** | Messages | Indefinite (configurable) |
| **Slack (free)** | Messages | 1 year (90 days visible) |
| **Zoom** | Meeting logs | 180 days |

### Recommended Retention Schedule for soev.ai DPIA

| Data Category | Recommended Period | Legal Basis / Justification | Configurable? |
|--------------|-------------------|---------------------------|---------------|
| **Active user account** | Duration of account + 30 days | Contract performance (Art. 6(1)(b)) | No (inherent) |
| **Inactive account** | **2 years** inactivity -> delete | Storage limitation (Art. 5(1)(e)); CNIL Discord precedent | Yes (`USER_INACTIVITY_TTL_DAYS=730`) |
| **Chat/conversation logs** | **90 days** -> soft-delete | EDPS GenAI Orientations (30d reference); 90d allows reasonable review period | Yes (`CHAT_RETENTION_TTL_DAYS=90`) |
| **Archived chats** | **1 year** -> soft-delete | User explicitly kept them, but not indefinitely | Yes (same config, clock from `archived` toggle) |
| **Knowledge bases (local)** | **2 years** since last update | Purpose limitation; matches account TTL | Yes (`KNOWLEDGE_RETENTION_TTL_DAYS=730`) |
| **Knowledge bases (cloud sync)** | Tied to source; 30-day suspension TTL | Already implemented; source is authoritative | Already exists |
| **Uploaded files** | Cascade from parent (chat/KB) | No independent retention justification | Inherits parent TTL |
| **User preferences/settings** | Duration of account | Contract performance | No (inherent) |
| **Audit/access logs** | **1 year** | Legitimate interest (security); Dutch practice norm | Yes (`AUDIT_LOG_RETENTION_DAYS=365`) |
| **User archives (GDPR)** | **3 years** (already default) | Legal obligation (demonstrating compliance) | Already exists (`DEFAULT_ARCHIVE_RETENTION_DAYS=1095`) |
| **Financial/billing records** | **7 years** | AWR tax law obligation | Out of scope (external billing system) |
| **Anonymized analytics** | No limit | Not personal data | N/A |

### Revised Default Configuration

Based on the regulatory research, the recommended defaults shift significantly for chat data:

```env
# Master TTL (0 = disabled, non-zero enables the retention system)
DATA_RETENTION_TTL_DAYS=0

# Per-entity overrides (0 = inherit master TTL, -1 = no TTL)
USER_INACTIVITY_TTL_DAYS=730       # CNIL precedent: 2 years
CHAT_RETENTION_TTL_DAYS=90         # EDPS GenAI guidance: 30d, we use 90d for review buffer
KNOWLEDGE_RETENTION_TTL_DAYS=730   # Same as user inactivity

# Warning before deletion
DATA_RETENTION_WARNING_DAYS=30
```

**Key insight**: Chat TTL should be **90 days**, not 730. The EDPS GenAI Orientations v2 (October 2025) — the most directly applicable regulatory guidance for an AI chat platform — establishes 30 days as reference for conversation content. 90 days gives a reasonable review buffer while staying well within defensible territory. This is the most impactful default.

### Archiefwet Caveat

For Dutch public sector customers, AI chat logs *may* qualify as "archiefbescheiden" (archival records) if they inform government decisions:
- The selectielijst of the specific government body determines retention (typically 5-10 years for non-permanent categories)
- soev.ai acts as *processor* — the customer (controller) must classify their data
- The DPIA should include a **customer-configurable override** for longer retention
- The `DATA_RETENTION_TTL_DAYS=0` (disabled) default respects the customer's obligation to determine their own archival requirements before automated deletion kicks in

### Sources

- [Autoriteit Persoonsgegevens — Retention of personal data](https://www.autoriteitpersoonsgegevens.nl/en/themes/basic-gdpr/privacy-and-personal-data/retention-of-personal-data)
- [CNIL Discord enforcement (SAN-2022-020)](https://gdprhub.eu/index.php?title=CNIL_(France)_-_SAN-2022-020)
- [EDPS GenAI Orientations v2 (October 2025)](https://www.edps.europa.eu/press-publications/press-news/press-releases/2025/edps-unveils-revised-guidance-generative-ai-strengthening-data-protection-rapidly-changing-digital-era_en)
- [EDPB Guidelines 4/2019 on DPbD](https://www.edpb.europa.eu/sites/default/files/files/file1/edpb_guidelines_201904_dataprotection_by_design_and_by_default_v2.0_en.pdf)
- [Dutch legal retention periods (Archive-IT)](https://www.archive-it.eu/knowledge-base/overview-of-legal-retention-periods-in-the-netherlands)
- [Selectielijst gemeenten 2020 (Nationaal Archief)](https://www.nationaalarchief.nl/sites/default/files/field-file/Selectielijst_20200214.pdf)
- [AP — Public Records Act and GDPR](https://www.autoriteitpersoonsgegevens.nl/en/themes/government/archiving-by-the-government/public-records-act-and-gdpr)
- [EDPB 2025 Coordinated Enforcement on Right to Erasure](https://www.reedsmith.com/our-insights/blogs/viewpoints/102mm9l/edpb-report-on-the-right-to-erasure-key-takeaways-from-the-2025-coordinated-enfo/)
