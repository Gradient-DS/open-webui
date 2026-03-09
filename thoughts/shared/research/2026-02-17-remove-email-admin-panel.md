---
date: 2026-02-17T12:00:00+01:00
researcher: claude
git_commit: 5d18f2c4d
branch: feat/sync-improvements
repository: open-webui
topic: "Remove email admin panel section - keep env-only configuration"
tags: [research, codebase, email, admin-panel, deployment, helm]
status: complete
last_updated: 2026-02-17
last_updated_by: claude
---

# Research: Remove Email Admin Panel Section

**Date**: 2026-02-17
**Researcher**: claude
**Git Commit**: 5d18f2c4d
**Branch**: feat/sync-improvements
**Repository**: open-webui

## Research Question
Remove the email configuration section from the admin panel UI and backend API. Email settings should only be configurable via environment variables. Also remove references from deployment values patches.

## Summary

The email admin panel feature spans **frontend** (Svelte component + API client), **backend** (FastAPI endpoints + PersistentConfig), **Helm chart** (values, configmap, secrets, deployment), and **GitOps** (2 tenant values patches). The changes needed are:

1. **Delete** the Email.svelte component
2. **Remove** email tab from Settings.svelte (import + tab button + content rendering)
3. **Remove** `'email'` from ADMIN_SETTINGS_TABS in features.ts
4. **Remove** getEmailConfig/setEmailConfig/testEmailConfig from configs API client
5. **Remove** EmailConfigForm, GET/POST /configs/email, POST /configs/email/test from configs.py
6. **Convert** PersistentConfig to plain env reads in config.py (so admin panel can't override)
7. **Remove** email config from app.state.config wiring in main.py (keep only env-based reads)
8. **Remove** `"email"` from featureAdminSettingsTabs in values-patch.yaml for gradient and demo tenants
9. **Keep** email env vars in Helm configmap/secrets/deployment (they still pass through to env)

## Detailed Findings

### Frontend: Admin Panel Email Tab

#### `src/lib/components/admin/Settings/Email.svelte` (DELETE entire file)
- Svelte component for configuring Graph API credentials, from address, from name, invite expiry
- Uses `getEmailConfig`, `setEmailConfig`, `testEmailConfig` from API client
- **Action**: Delete this file entirely

#### `src/lib/components/admin/Settings.svelte`
- **Line 27**: `import Email from './Settings/Email.svelte';` — REMOVE
- **Lines 427-455**: Email tab button in sidebar — REMOVE
- **Lines 570-578**: `{:else if selectedTab === 'email'}` rendering block — REMOVE

#### `src/lib/utils/features.ts`
- **Line 101**: `'email'` in ADMIN_SETTINGS_TABS array — REMOVE

#### `src/lib/apis/configs/index.ts`
- **Lines 568-648**: `getEmailConfig`, `setEmailConfig`, `testEmailConfig` functions — REMOVE

### Backend: API Endpoints

#### `backend/open_webui/routers/configs.py`
- **Lines 598-662**: Full EmailConfig section — REMOVE:
  - `EmailConfigForm` model (lines 603-610)
  - `GET /configs/email` endpoint (lines 613-623)
  - `POST /configs/email` endpoint (lines 626-641)
  - `POST /configs/email/test` endpoint (lines 644-661)
- **Note**: Keep the InviteContent section (lines 566-595) — it's separate

### Backend: Configuration

#### `backend/open_webui/config.py` (lines 2555-2611)
- Currently uses `PersistentConfig` which allows admin panel to override env values
- **Action**: Convert to plain `os.environ.get()` calls so values are env-only:
  ```python
  # Before (PersistentConfig - admin can override):
  ENABLE_EMAIL_INVITES = PersistentConfig("ENABLE_EMAIL_INVITES", "email.enable_invites", ...)

  # After (env-only):
  ENABLE_EMAIL_INVITES = os.environ.get("ENABLE_EMAIL_INVITES", "False").lower() == "true"
  ```
- Variables to convert: `ENABLE_EMAIL_INVITES`, `EMAIL_GRAPH_TENANT_ID`, `EMAIL_GRAPH_CLIENT_ID`, `EMAIL_GRAPH_CLIENT_SECRET`, `EMAIL_FROM_ADDRESS`, `EMAIL_FROM_NAME`, `INVITE_EXPIRY_HOURS`
- **Keep as PersistentConfig**: `EMAIL_INVITE_SUBJECT`, `EMAIL_INVITE_HEADING` (these are managed via the separate InviteContent admin endpoints, not the email tab)

#### `backend/open_webui/main.py` (lines 1065-1073)
- Wires email config to `app.state.config`
- **Action**: Keep this wiring but assign plain values instead of PersistentConfig objects
- The email services (`services/email/auth.py`, `services/email/graph_mail_client.py`) read from `app.state.config`, so the wiring must stay

### Helm Chart: Values & Templates

#### `helm/open-webui-tenant/values.yaml`
- **Line 206**: `featureAdminSettingsTabs: "models,tools,interface,db,email"` — remove `email` from this default list
- **Lines 303-310**: Email invite config values (enableEmailInvites, emailGraphTenantId, etc.) — KEEP (still needed to pass env vars)
- **Line 477**: `emailGraphClientSecret` secret — KEEP

#### `helm/open-webui-tenant/templates/open-webui/configmap.yaml` (lines 224-234)
- Email env vars mapping — KEEP (still need to pass env vars to container)

#### `helm/open-webui-tenant/templates/secrets.yaml` (lines 20-23)
- Email graph client secret — KEEP

#### `helm/open-webui-tenant/templates/open-webui/deployment.yaml` (lines 73-80)
- EMAIL_GRAPH_CLIENT_SECRET injection — KEEP

### GitOps: Tenant Values Patches

#### `soev-gitops/tenants/previder-prod/gradient/values-patch.yaml`
- **Lines 51-60**: Email config block — KEEP the env vars (lines 52-57), but:
  - **Line 60**: `featureAdminSettingsTabs: "models,tools,interface,db,email"` — remove `,email`
  - Change to: `featureAdminSettingsTabs: "models,tools,interface,db"`

#### `soev-gitops/tenants/previder-prod/demo/values-patch.yaml`
- **Lines 52-61**: Email config block — KEEP the env vars (lines 53-58), but:
  - **Line 61**: `featureAdminSettingsTabs: "models,tools,interface,db,email"` — remove `,email`
  - Change to: `featureAdminSettingsTabs: "models,tools,interface,db"`

### Other Files (NO CHANGES NEEDED)

- `src/lib/stores/index.ts` — `email` field is for the User type (user's email address), unrelated
- `src/lib/apis/invites/index.ts` — invite management, separate from email config admin
- `backend/open_webui/services/email/` — email sending service, reads from app.state.config, keep as-is
- `backend/open_webui/routers/invites.py` — invite router, keep as-is
- `backend/open_webui/models/invites.py` — invite model, keep as-is
- `backend/open_webui/main.py` line 2094 — `enable_email_invites` in features response, KEEP (frontend still needs to know if email invites are enabled)
- i18n locale files — translation keys can stay (unused keys don't cause issues)

## Code References

- `src/lib/components/admin/Settings/Email.svelte:1-213` — DELETE
- `src/lib/components/admin/Settings.svelte:27` — Remove import
- `src/lib/components/admin/Settings.svelte:427-455` — Remove email tab button
- `src/lib/components/admin/Settings.svelte:570-578` — Remove email content rendering
- `src/lib/utils/features.ts:101` — Remove 'email' from ADMIN_SETTINGS_TABS
- `src/lib/apis/configs/index.ts:568-648` — Remove email config API functions
- `backend/open_webui/routers/configs.py:598-662` — Remove email config endpoints
- `backend/open_webui/config.py:2559-2599` — Convert PersistentConfig to env reads
- `backend/open_webui/main.py:1065-1073` — Update to use plain values
- `helm/open-webui-tenant/values.yaml:206` — Remove 'email' from featureAdminSettingsTabs
- `soev-gitops/tenants/previder-prod/gradient/values-patch.yaml:60` — Remove ',email' from tabs
- `soev-gitops/tenants/previder-prod/demo/values-patch.yaml:61` — Remove ',email' from tabs

## Architecture Insights

The email configuration currently uses `PersistentConfig` which stores values in the database, allowing the admin panel to override environment variables at runtime. By converting these to plain `os.environ.get()` calls, the values become immutable after deployment — only changeable via env vars (Helm values / .env file).

The `EMAIL_INVITE_SUBJECT` and `EMAIL_INVITE_HEADING` should remain as PersistentConfig because they're managed through the separate "Invite Content" admin endpoints (lines 566-595 in configs.py), not the email tab being removed.

## Open Questions

1. Should the invite content endpoints (subject/heading) also be converted to env-only, or keep them as admin-configurable?
2. The `enable_email_invites` flag is still exposed in the features response (main.py:2094) — this is needed for the invite UI to know whether to show "send email" options. Keep as-is.
