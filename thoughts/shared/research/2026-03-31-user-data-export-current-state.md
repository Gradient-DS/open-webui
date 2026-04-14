---
date: 2026-03-31T13:28:59Z
researcher: Claude
git_commit: d228b1cdd85cc1288b20ad26bbbe20d568a803f7
branch: feat/dpia
repository: open-webui
topic: 'Current state of user data export — endpoints, formats, env flags, and gaps'
tags: [research, codebase, data-export, gdpr, dpia, archival, user-data]
status: complete
last_updated: 2026-03-31
last_updated_by: Claude
---

# Research: Current State of User Data Export

**Date**: 2026-03-31T13:28:59Z
**Researcher**: Claude
**Git Commit**: d228b1cdd85cc1288b20ad26bbbe20d568a803f7
**Branch**: feat/dpia
**Repository**: open-webui

## Research Question

What is the current state of user data export? What env flags and options exist? What data types are associated with a user, and what can/cannot be exported today?

## Summary

Open WebUI has **extensive per-resource export** (chats, models, tools, prompts, knowledge bases, feedbacks, config) but **no unified "export all my data" endpoint** for GDPR data portability. Users can export their chats (JSON/PDF/TXT) and admins can export the entire database, but user-owned files, memories, notes, settings, and other personal data have no user-facing export path. The GDPR archival system only captures profile + chats.

## Detailed Findings

### 1. All Export Endpoints

#### User Self-Service

| Endpoint                         | Data                                    | Format                         | Permission Gate                          |
| -------------------------------- | --------------------------------------- | ------------------------------ | ---------------------------------------- |
| `GET /api/v1/chats/all`          | User's own chats (full content)         | JSON array of `ChatResponse`   | `chat.export` permission (default: true) |
| `POST /api/v1/utils/pdf`         | Single chat as PDF                      | PDF binary                     | Verified user                            |
| `GET /api/v1/chats/stats/export` | Chat metadata/stats (no message bodies) | JSON or JSONL (`?stream=true`) | Admin or `ENABLE_COMMUNITY_SHARING`      |

#### Admin-Only

| Endpoint                                       | Data                                    | Format              | Permission Gate                     |
| ---------------------------------------------- | --------------------------------------- | ------------------- | ----------------------------------- |
| `GET /api/v1/utils/db/download`                | Raw SQLite database file                | Binary `.db`        | `ENABLE_ADMIN_EXPORT` (SQLite only) |
| `GET /api/v1/utils/db/export`                  | Full database as JSON (all tables)      | JSON file           | `ENABLE_ADMIN_EXPORT`               |
| `GET /api/v1/chats/all/db`                     | All chats across all users              | JSON array          | `ENABLE_ADMIN_EXPORT`               |
| `GET /api/v1/configs/export`                   | App configuration                       | JSON dict           | Admin                               |
| `GET /api/v1/models/export`                    | Model definitions                       | JSON array          | `workspace.models_export` or admin  |
| `GET /api/v1/tools/export`                     | Tool definitions                        | JSON array          | Admin                               |
| `GET /api/v1/functions/export`                 | Function definitions                    | JSON array          | Admin                               |
| `GET /api/v1/skills/export`                    | Skill definitions                       | JSON array          | Admin                               |
| `GET /api/v1/knowledge/{id}/export`            | Knowledge base documents                | ZIP of `.txt` files | Admin                               |
| `GET /api/v1/groups/id/{id}/export`            | Group definition                        | JSON                | Admin                               |
| `GET /api/v1/evaluations/feedbacks/all/export` | All feedback records                    | JSON array          | Admin                               |
| `GET /api/v1/archives/{archive_id}/export`     | Archived user chats (importable format) | JSON array          | Admin                               |
| `POST /api/v1/archives/user/{user_id}`         | Create user archive (profile + chats)   | Stored in DB        | Admin                               |

### 2. Frontend Export UI

| Location                 | What                                               | Formats                            |
| ------------------------ | -------------------------------------------------- | ---------------------------------- |
| Chat sidebar/navbar menu | Individual chat                                    | JSON, TXT, PDF (stylized or plain) |
| Settings > Data Controls | All user chats (bulk)                              | JSON                               |
| Archived Chats Modal     | All archived chats                                 | JSON                               |
| Sidebar folder menu      | Folder chats                                       | JSON                               |
| Playground               | Playground chat                                    | JSON, TXT                          |
| Notes menu               | Individual note                                    | TXT, MD, PDF                       |
| Workspace pages          | Models, Tools, Skills, Prompts, Functions          | JSON (single or bulk)              |
| Admin > Database         | DB download, all chats, users CSV, config          | SQLite, JSON, CSV                  |
| Admin > Evaluations      | All feedbacks                                      | JSON                               |
| Inline content           | Tables → CSV, SVGs → SVG, Images → original format | Various                            |

### 3. Environment Variables & Config Flags

#### Export Toggles

| Variable                                    | Type                          | Default | Helm Default | Location         |
| ------------------------------------------- | ----------------------------- | ------- | ------------ | ---------------- |
| `ENABLE_ADMIN_EXPORT`                       | `bool` (not PersistentConfig) | `True`  | `"false"`    | `config.py:1679` |
| `USER_PERMISSIONS_CHAT_EXPORT`              | `bool`                        | `True`  | `"true"`     | `config.py:1438` |
| `USER_PERMISSIONS_WORKSPACE_MODELS_EXPORT`  | `bool`                        | `False` | —            | `config.py:1334` |
| `USER_PERMISSIONS_WORKSPACE_PROMPTS_EXPORT` | `bool`                        | `False` | —            | `config.py:1342` |
| `USER_PERMISSIONS_WORKSPACE_TOOLS_EXPORT`   | `bool`                        | `False` | —            | `config.py:1350` |
| `ENABLE_COMMUNITY_SHARING`                  | `PersistentConfig`            | `True`  | `"false"`    | `config.py:1793` |

#### Archival/GDPR Config

| Variable                             | Type               | Default                     | Location         |
| ------------------------------------ | ------------------ | --------------------------- | ---------------- |
| `ENABLE_USER_ARCHIVAL`               | `PersistentConfig` | `False`                     | `config.py:1696` |
| `DEFAULT_ARCHIVE_RETENTION_DAYS`     | `PersistentConfig` | `1095` (3 years, ISO 27001) | `config.py:1705` |
| `ENABLE_AUTO_ARCHIVE_ON_SELF_DELETE` | `PersistentConfig` | `False`                     | `config.py:1712` |
| `AUTO_ARCHIVE_RETENTION_DAYS`        | `PersistentConfig` | `365`                       | `config.py:1718` |

#### Sharing Permissions (related but distinct)

| Variable                                                                                        | Default | Helm Default |
| ----------------------------------------------------------------------------------------------- | ------- | ------------ |
| `USER_PERMISSIONS_CHAT_SHARE`                                                                   | `True`  | `"true"`     |
| `USER_PERMISSIONS_WORKSPACE_*_ALLOW_SHARING` (models, knowledge, prompts, tools, skills, notes) | `False` | `"false"`    |
| `USER_PERMISSIONS_WORKSPACE_*_ALLOW_PUBLIC_SHARING` (same set)                                  | `False` | `"false"`    |

### 4. Complete User Data Inventory

| Data Type           | Table(s)                      | Vector DB Collection    | File Storage                     | Exportable Today?                                                              |
| ------------------- | ----------------------------- | ----------------------- | -------------------------------- | ------------------------------------------------------------------------------ |
| Profile             | `user`                        | —                       | —                                | Only via admin DB export or GDPR archive (profile subset)                      |
| Auth/Credentials    | `auth`                        | —                       | —                                | Admin DB export only (hashed password, TOTP secret)                            |
| API Keys            | `api_key`                     | —                       | —                                | Admin DB export only                                                           |
| Chats               | `chat`, `chat_file`           | —                       | —                                | **Yes** — user self-service (JSON/PDF/TXT)                                     |
| Files               | `file`                        | `file-{id}`             | `uploads/` dir (or S3/GCS/Azure) | **No user-facing export** — files accessible individually but no bulk download |
| Knowledge Bases     | `knowledge`, `knowledge_file` | `{kb_id}`               | —                                | Admin only (ZIP of .txt) — no user self-service                                |
| Memories            | `memory`                      | `user-memory-{user_id}` | —                                | **No export endpoint at all**                                                  |
| Tools               | `tool`                        | —                       | —                                | Admin only or `workspace.tools_export` permission                              |
| Functions           | `function`                    | —                       | —                                | Admin only                                                                     |
| Prompts             | `prompt`, `prompt_history`    | —                       | —                                | Admin only or `workspace.prompts_export` permission                            |
| Custom Models       | `model`                       | —                       | —                                | Admin only or `workspace.models_export` permission                             |
| Feedbacks           | `feedback`                    | —                       | —                                | Admin only                                                                     |
| Notes               | `note`                        | —                       | —                                | Individual download (TXT/MD/PDF) — no bulk export                              |
| Tags                | `tag`                         | —                       | —                                | **No export**                                                                  |
| Folders             | `folder`                      | —                       | —                                | **No export** (structure only, chats exportable per-folder)                    |
| Channel Messages    | `channel`, `message`          | —                       | —                                | **No export**                                                                  |
| Groups (membership) | `group_member`                | —                       | —                                | **No export**                                                                  |
| OAuth Sessions      | `oauth_session`               | —                       | —                                | **No export** (security-sensitive)                                             |
| Access Grants       | `access_grant`                | —                       | —                                | **No export**                                                                  |
| User Settings       | `user.settings` JSON          | —                       | —                                | **No export**                                                                  |

### 5. GDPR Archival System (Custom soev Feature)

The archival system at `backend/open_webui/services/archival/service.py` captures a **limited subset**:

```python
# collect_user_data() captures:
{
    "version": "1.0",
    "archived_at": timestamp,
    "user_profile": {id, name, email, role, profile_image_url, timestamps},
    "chats": [ChatResponse array],
    "stats": {"chat_count": N}
}
```

**Not captured**: files, knowledge bases, memories, tools, functions, prompts, notes, feedbacks, settings, tags, folders, channel messages, groups.

### 6. Import Capabilities

| Data Type | Import Endpoint                 | Supports                                 |
| --------- | ------------------------------- | ---------------------------------------- |
| Chats     | `POST /api/v1/chats/import`     | Native format + OpenAI format conversion |
| Models    | `POST /api/v1/models/import`    | JSON array                               |
| Tools     | `POST /api/v1/tools/import`     | JSON array                               |
| Functions | `POST /api/v1/functions/import` | JSON array                               |
| Skills    | `POST /api/v1/skills/import`    | JSON array                               |
| Prompts   | `POST /api/v1/prompts/import`   | JSON array                               |
| Config    | `POST /api/v1/configs/import`   | JSON dict                                |

No import for: files, knowledge bases, memories, notes, feedbacks, user settings.

## Code References

- `backend/open_webui/config.py:1334-1438` — User permission variables for export
- `backend/open_webui/config.py:1679` — `ENABLE_ADMIN_EXPORT`
- `backend/open_webui/config.py:1696-1722` — Archival config variables
- `backend/open_webui/config.py:1793-1797` — `ENABLE_COMMUNITY_SHARING`
- `backend/open_webui/routers/chats.py:392-489` — Chat stats export endpoints
- `backend/open_webui/routers/chats.py:673-716` — Chat bulk export endpoints
- `backend/open_webui/routers/utils.py:91-202` — DB download/export + PDF
- `backend/open_webui/routers/archives.py` — GDPR archival endpoints
- `backend/open_webui/routers/knowledge.py:1226-1265` — Knowledge base ZIP export
- `backend/open_webui/services/archival/service.py:62-101` — Archive data collection
- `backend/open_webui/services/deletion/service.py:383-559` — User deletion cascade
- `backend/open_webui/storage/provider.py:40-55` — Storage abstraction (local/S3/GCS/Azure)
- `src/lib/components/chat/Settings/DataControls.svelte` — User export UI
- `src/lib/components/admin/Settings/Database.svelte` — Admin export UI
- `helm/open-webui-tenant/values.yaml:248-307` — Helm export/sharing defaults

## Architecture Insights

1. **No unified data portability endpoint**: The biggest gap for GDPR Article 20 (right to data portability). Each data type has its own export mechanism (if any), with inconsistent permission models.

2. **Files are the hardest part**: User files live in object storage (local disk, S3, GCS, or Azure) and have corresponding vector DB collections (`file-{id}`). The research question correctly identifies that vectors should not be exported (they can be regenerated). The files themselves need a bulk download mechanism.

3. **GDPR archive is incomplete**: The archival service was built for compliance retention (keeping data after deletion), not for data portability. It only captures profile + chats.

4. **Standard formats**: Chat export uses a proprietary JSON format (array of `ChatResponse`). There's OpenAI format detection for _import_ but not for _export_. No standard like ActivityPub, JSON-LD, or MBOX is used.

5. **Permission model is fragmented**: Chat export has a dedicated user permission (`chat.export`), workspace items have separate `*_export` permissions (default off), and admin export has a global toggle. A unified "export my data" feature would need its own permission.

## Gaps for GDPR-Compliant Full Data Export

| Gap                                                 | Priority | Complexity                                           |
| --------------------------------------------------- | -------- | ---------------------------------------------------- |
| Unified "export all my data" endpoint               | High     | Medium — orchestrate existing queries                |
| Include files (original uploads) in export          | High     | Medium — bulk download from storage provider         |
| Include memories in export                          | Medium   | Low — simple DB query                                |
| Include notes in export                             | Medium   | Low — simple DB query                                |
| Include user settings/preferences                   | Medium   | Low — already in user record                         |
| Include knowledge base metadata + file associations | Medium   | Low — DB query                                       |
| Include prompts, tools, functions (user-created)    | Medium   | Low — existing export logic, just needs user-scoping |
| Include feedbacks                                   | Low      | Low — DB query                                       |
| Include folder structure + tags                     | Low      | Low — DB query                                       |
| Standard format (e.g., JSON-LD, structured archive) | Medium   | Medium — define schema                               |
| Include channel messages                            | Low      | Low — DB query                                       |
| Self-service UI for full export                     | High     | Medium — new component                               |

## Open Questions

1. **What standard format?** For chats, should we export as MBOX, JSON-LD, or keep the existing format with a schema definition? For the overall archive, a ZIP with a manifest might be most practical.
2. **File handling at scale**: For users with many large files, should the export be async (generate → notify → download)?
3. **Should vectors be excluded or optionally included?** The user indicated vectors should be excluded, but some users might want them for portability to another instance.
4. **Retention of export requests**: Should we log that a data export was requested (GDPR audit trail)?
5. **Rate limiting**: Should there be a cooldown on full data exports to prevent abuse?
