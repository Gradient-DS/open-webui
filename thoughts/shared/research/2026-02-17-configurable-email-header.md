---
date: 2026-02-17T12:00:00+01:00
researcher: claude
git_commit: 5d18f2c4d
branch: feat/sync-improvements
repository: open-webui
topic: "Making email invite subject/heading fully configurable via admin panel"
tags: [research, codebase, email, configuration, admin-panel, invites]
status: complete
last_updated: 2026-02-17
last_updated_by: claude
---

# Research: Configurable Email Invite Header

**Date**: 2026-02-17
**Researcher**: Claude
**Git Commit**: 5d18f2c4d
**Branch**: feat/sync-improvements
**Repository**: open-webui

## Research Question

Could we make the email header (subject line and heading) fully configurable through the environment and admin panel, while keeping the current `CLIENT_NAME` substitution as the default?

## Summary

The current email invite system has **hardcoded subject/heading templates** in `graph_mail_client.py` with `APP_NAME = "soev.ai"` baked into the strings. `CLIENT_NAME` is a plain env var (not a `PersistentConfig`, not editable in admin panel). Making the header fully configurable requires changes across **6 files** spanning backend config, API endpoints, email rendering, and the admin UI. The change is straightforward and follows existing patterns for `PersistentConfig` + admin panel fields.

## Current State

### How the Email Header Works Today

The email subject and heading are constructed in `graph_mail_client.py:49-68` using a `_STRINGS` dictionary with two locale variants (en, nl):

```python
APP_NAME = "soev.ai"

_STRINGS = {
    "en": {
        "subject_with_client": f"You've been invited to the {APP_NAME} environment of {{client_name}}",
        "subject": f"You've been invited to {APP_NAME}",
        "heading_with_client": f"You've been invited to the {APP_NAME_HTML} environment of {{client_name}}",
        "heading": f"You've been invited to {APP_NAME_HTML}",
        ...
    },
    "nl": { ... }
}
```

- **With `CLIENT_NAME` set**: Subject = `"You've been invited to the soev.ai environment of {CLIENT_NAME}"`
- **Without `CLIENT_NAME`**: Subject = `"You've been invited to soev.ai"`

`CLIENT_NAME` is a plain env var at `env.py:94`:
```python
CLIENT_NAME = os.environ.get("CLIENT_NAME", "")
```

It is NOT a `PersistentConfig` — it can only be changed by redeploying with a new env var, not through the admin panel.

### How the Email Body/Template Works

The HTML template at `graph_mail_client.py:104-122` is a hardcoded f-string with inline CSS. The body text, button label, and footer are also in the `_STRINGS` dict with `{invited_by_name}` and `{expiry_days}` placeholders.

## What Would Need to Change

### Option A: Configurable Subject + Heading Only (Recommended)

Add two new `PersistentConfig` fields for custom subject and heading templates, with template variable support (`{client_name}`, `{app_name}`). When empty (default), fall back to the current locale-based `_STRINGS` logic.

#### Files to Change

| # | File | Change | Effort |
|---|------|--------|--------|
| 1 | `backend/open_webui/config.py` | Add `EMAIL_INVITE_SUBJECT` and `EMAIL_INVITE_HEADING` PersistentConfig vars | Small |
| 2 | `backend/open_webui/main.py` | Import + mount new configs on `app.state.config` | Small |
| 3 | `backend/open_webui/routers/configs.py` | Add fields to `EmailConfigForm`, update GET/POST handlers | Small |
| 4 | `backend/open_webui/services/email/graph_mail_client.py` | Accept optional custom templates, fall back to `_STRINGS` | Medium |
| 5 | `backend/open_webui/routers/invites.py` | Pass custom templates from config to render functions | Small |
| 6 | `src/lib/components/admin/Settings/Email.svelte` | Add input fields for custom subject/heading | Small |

#### Detailed Changes

**1. `config.py` (~lines 2593+)**

Add two new PersistentConfig entries:

```python
EMAIL_INVITE_SUBJECT = PersistentConfig(
    "EMAIL_INVITE_SUBJECT",
    "email.invite_subject",
    os.environ.get("EMAIL_INVITE_SUBJECT", ""),
)

EMAIL_INVITE_HEADING = PersistentConfig(
    "EMAIL_INVITE_HEADING",
    "email.invite_heading",
    os.environ.get("EMAIL_INVITE_HEADING", ""),
)
```

Default empty string means "use built-in locale-based template".

**2. `main.py` (~line 1069)**

```python
app.state.config.EMAIL_INVITE_SUBJECT = EMAIL_INVITE_SUBJECT
app.state.config.EMAIL_INVITE_HEADING = EMAIL_INVITE_HEADING
```

**3. `configs.py` — `EmailConfigForm`**

Add optional fields:
```python
class EmailConfigForm(BaseModel):
    # ... existing fields ...
    EMAIL_INVITE_SUBJECT: str = ""
    EMAIL_INVITE_HEADING: str = ""
```

Update `get_email_config()` and `set_email_config()` to include these fields.

**4. `graph_mail_client.py` — Rendering functions**

Update `render_invite_subject()` and `render_invite_email()` to accept an optional custom template:

```python
def render_invite_subject(
    locale: str = "en",
    client_name: str = "",
    custom_template: str = "",  # NEW
) -> str:
    if custom_template:
        return custom_template.format(
            client_name=client_name,
            app_name=APP_NAME,
        )
    # Fall back to existing _STRINGS logic
    strings = _get_strings(locale)
    if client_name:
        return strings["subject_with_client"].format(client_name=client_name)
    return strings["subject"]
```

Similarly for heading in `render_invite_email()`.

**5. `invites.py` — Pass config values**

At line 139 (create) and 392 (resend), pass the custom templates from `request.app.state.config`:

```python
subject=render_invite_subject(
    locale=locale,
    client_name=CLIENT_NAME,
    custom_template=request.app.state.config.EMAIL_INVITE_SUBJECT,
)
```

**6. `Email.svelte` — New UI fields**

Add a new "Email Content" section (between Sender and Invite Settings) with two text inputs:
- "Invite Subject" — placeholder showing the default, e.g., `"You've been invited to soev.ai environment of {client_name}"`
- "Invite Heading" — same placeholder logic
- Help text: `"Leave empty for default. Use {client_name} and {app_name} as placeholders."`

### Option B: Full Template Customization (More Complex)

Make ALL email text configurable (subject, heading, body, button text, footer). This would require:
- 5+ additional `PersistentConfig` fields
- More complex admin UI (possibly a textarea for HTML body)
- Template variable validation/sanitization (security concern with arbitrary HTML)
- Loss of built-in i18n (or a more complex override-per-locale system)

**Not recommended** for the initial change — Option A covers the primary use case and is much simpler.

### Option C: CLIENT_NAME as PersistentConfig (Minimal Change)

Just convert `CLIENT_NAME` from a plain env var to a `PersistentConfig` so it's editable in the admin panel. This doesn't make the template text configurable, but lets admins change the client branding without redeploying.

**Simplest change** but doesn't achieve "fully configurable header".

## Template Variable Design

For custom templates, support these variables:

| Variable | Value | Example |
|----------|-------|---------|
| `{client_name}` | `CLIENT_NAME` env var | `"Acme Corp"` |
| `{app_name}` | `APP_NAME` constant | `"soev.ai"` |
| `{invited_by_name}` | Name of inviting admin (heading only) | `"Jane Doe"` |

Use Python's `str.format()` with `**kwargs` for safe interpolation. Unknown variables will raise `KeyError` — catch and fall back to the built-in template.

## Considerations

### i18n Impact
Custom templates override locale-based strings. If an admin sets a custom Dutch subject, it will be used for ALL locales. This is acceptable for single-tenant deployments where the org language is known. For multi-locale support, you'd need per-locale custom templates (significantly more complex — not recommended).

### Security
Subject and heading are plain text (heading goes inside an `<h2>` tag in the HTML template). Since these are admin-only settings, XSS risk is minimal — but the heading should still be HTML-escaped when inserted into the template to prevent accidental HTML injection.

### Migration
No database migration needed — `PersistentConfig` stores values in the existing `config` table as JSON. New entries are created on first access with the default value.

### Helm Chart
Optionally add `emailInviteSubject` and `emailInviteHeading` to `helm/open-webui-tenant/values.yaml` and map them in the configmap template.

## Code References

- `backend/open_webui/services/email/graph_mail_client.py:45-83` — Current hardcoded subject/heading templates
- `backend/open_webui/services/email/graph_mail_client.py:86-122` — HTML template rendering
- `backend/open_webui/config.py:2556-2599` — Email PersistentConfig definitions
- `backend/open_webui/main.py:1063-1069` — Config mounting on app.state
- `backend/open_webui/routers/configs.py:571-629` — Email config API endpoints
- `backend/open_webui/routers/invites.py:118-146` — Email sending in invite creation
- `backend/open_webui/routers/invites.py:371-397` — Email sending in invite resend
- `backend/open_webui/env.py:94` — CLIENT_NAME env var
- `src/lib/components/admin/Settings/Email.svelte` — Admin email settings UI
- `helm/open-webui-tenant/values.yaml:19` — CLIENT_NAME in Helm chart

## Architecture Insights

- The email system uses `PersistentConfig` for all settings except `CLIENT_NAME`, which is a plain env var. This inconsistency means CLIENT_NAME currently requires redeployment to change.
- The `_STRINGS` dict in `graph_mail_client.py` functions as a mini i18n system with only en/nl support, separate from the frontend's i18n system.
- The email template is intentionally simple (inline CSS, no Jinja2) for maximum email client compatibility.
- All email config changes are immediately effective (no restart needed) thanks to `PersistentConfig` storing values in the DB and reading from `app.state.config`.

## Open Questions

1. Should `CLIENT_NAME` itself also become a `PersistentConfig` (editable in admin panel)?
2. Should we support per-locale custom templates, or is a single custom template sufficient?
3. Should the body text, button label, and footer also be configurable, or just subject + heading?
4. Should the test email endpoint also use the custom subject template?
