---
date: 2026-04-09T15:18:00+02:00
researcher: Claude
git_commit: c95a0823c99b99e900af1e277b3e6bb7c3c515d5
branch: feat/proprietary-warnings
repository: open-webui
topic: 'PII and Credentials in Production Logs'
tags: [research, logging, pii, security, uvicorn, httpx, oauth, gdpr]
status: complete
last_updated: 2026-04-09
last_updated_by: Claude
---

# Research: PII and Credentials in Production Logs

**Date**: 2026-04-09T15:18:00+02:00
**Researcher**: Claude
**Git Commit**: c95a0823c
**Branch**: feat/proprietary-warnings
**Repository**: open-webui

## Research Question

Identify all code locations in Open WebUI that cause PII or credentials to appear in production logs. Three categories: (1) Uvicorn access logs with client IPs, (2) httpx logging SharePoint/OneDrive tokens in URLs, (3) UserModel/email serialization in logs.

## Summary

Three distinct sources of PII/credential leakage were confirmed. The uvicorn access log issue is a configuration gap (access logging enabled by default, no IP stripping). The httpx token leakage is caused by missing logger-level configuration — httpx inherits `GLOBAL_LOG_LEVEL=INFO` and logs full URLs including `?token=` and `&tempauth=` params. The UserModel issue is narrower than expected: no code path logs a `UserModel` object directly, but 21 locations log `user.email`, raw `email` params, or full OAuth token/claims dicts.

## Detailed Findings

### P1: Uvicorn Access Logs with Client IPs

**Root cause:** Uvicorn's `access_log` defaults to `True` and is never explicitly disabled. With `forwarded_allow_ips='*'`, uvicorn trusts X-Forwarded-For headers and logs real client IPs.

**Uvicorn startup locations (3 entry points):**

| Entry point         | File                             | Line  | Notes                                               |
| ------------------- | -------------------------------- | ----- | --------------------------------------------------- |
| `serve` CLI command | `backend/open_webui/__init__.py` | 71    | `forwarded_allow_ips='*'`, no `access_log` param    |
| `dev` CLI command   | `backend/open_webui/__init__.py` | 86    | Same                                                |
| Docker `start.sh`   | `backend/start.sh`               | 83-87 | `--forwarded-allow-ips '*'`, no `--access-log` flag |

None of the three paths set `access_log=False`, `proxy_headers`, or `log_config`.

**Logging pipeline for access logs:**

- `backend/open_webui/utils/logger.py:187-190` — `uvicorn.access` logger is reconfigured with `InterceptHandler`, routing access logs through Loguru
- `backend/open_webui/config.py:42-48` — `EndpointFilter` only suppresses `/health` requests from `uvicorn.access`, all other requests (with IPs) pass through
- `backend/open_webui/env.py:108` — `GLOBAL_LOG_LEVEL` defaults to `INFO`

**Fix approach:** Disable uvicorn access logging (`access_log=False` in `uvicorn.run()` / `--no-access-log` in start.sh), or set the `uvicorn.access` logger to WARNING+ in `logger.py`. The audit log system already captures access information separately.

### P2: httpx Logging Tokens in URLs

**Root cause:** httpx's internal logger logs all HTTP requests at INFO level. No httpx-specific log level override exists anywhere in the codebase. The `GLOBAL_LOG_LEVEL=INFO` default propagates to httpx.

**httpx client locations with credential exposure:**

| File                                     | Line         | Pattern                                                                       | Credential Risk                                                       |
| ---------------------------------------- | ------------ | ----------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `services/onedrive/graph_client.py`      | 27, 208      | Persistent client, `download_file` with `follow_redirects=True`               | **High** — redirect URLs contain `?token=` and `&tempauth=` with JWTs |
| `services/google_drive/drive_client.py`  | 52, 279, 291 | Persistent client, `download_file`/`export_file` with `follow_redirects=True` | Possible redirect URLs with tokens                                    |
| `services/onedrive/token_refresh.py`     | 63           | Ephemeral client, POST to Microsoft token endpoint                            | Token endpoint URL (fixed, but POST body logged?)                     |
| `services/google_drive/token_refresh.py` | 58           | Ephemeral client, POST to Google token endpoint                               | Same                                                                  |
| `services/onedrive/auth.py`              | 140          | Ephemeral client, authorization code exchange                                 | Auth code in URL                                                      |
| `services/google_drive/auth.py`          | 136          | Ephemeral client, authorization code exchange                                 | Auth code in URL                                                      |
| `services/email/graph_mail_client.py`    | 29           | Ephemeral client                                                              | Graph API calls                                                       |
| `services/email/auth.py`                 | 26           | Ephemeral client                                                              | Token endpoint                                                        |

**Additional exposure:** When OTEL is enabled, `utils/telemetry/instrumentors.py:100-111` writes the full `str(request.url)` into OTEL span attributes via `httpx_request_hook()`.

**Fix approach:** Set `logging.getLogger("httpx").setLevel(logging.WARNING)` in `logger.py:start_logger()`. This silences httpx's request logging while still allowing warnings/errors.

### P3: Email/PII in Log Messages

**Contrary to initial assumption:** No code path logs a `UserModel` object directly via `{user}` interpolation. The Pydantic default `__repr__` (which would dump all fields) is never triggered in logging. However, `user.email` and raw email parameters are logged explicitly in 21 locations.

**UserModel definition:** `backend/open_webui/models/users.py:83-121` — No custom `__repr__`/`__str__`. PII fields: `email`, `name`, `username`, `bio`, `gender`, `date_of_birth`, `oauth`.

#### Email in debug logs (9 locations)

| File               | Lines                             | Level | Pattern                 |
| ------------------ | --------------------------------- | ----- | ----------------------- |
| `routers/tasks.py` | 183, 260, 328, 390, 470, 548, 610 | debug | `for user {user.email}` |
| `utils/oauth.py`   | 1511, 1541                        | debug | `for user {user.email}` |

These only appear when `GLOBAL_LOG_LEVEL=DEBUG` — low risk in production.

#### Email in info/error logs (7 locations)

| File                            | Lines   | Level | Pattern                                          |
| ------------------------------- | ------- | ----- | ------------------------------------------------ |
| `models/auths.py`               | 128     | info  | `authenticate_user: {email}`                     |
| `models/auths.py`               | 160     | info  | `authenticate_user_by_email: {email}`            |
| `services/retention/service.py` | 163     | info  | `sent warning email to {user.email}`             |
| `services/retention/service.py` | 165     | error | `failed to send warning to {user.email}`         |
| `services/retention/service.py` | 204-205 | info  | `archived user {user.id} ({user.email})`         |
| `services/retention/service.py` | 215-216 | info  | `deleted inactive user {user.id} ({user.email})` |
| `services/archival/service.py`  | 199     | info  | `for user {archive.user_email}`                  |

**These appear at INFO level in production.**

#### Full OAuth dicts in warning/error logs (5 locations)

| File             | Lines | Level   | Pattern       | Data exposed                                                  |
| ---------------- | ----- | ------- | ------------- | ------------------------------------------------------------- |
| `utils/oauth.py` | 1418  | warning | `{token}`     | Full OAuth token dict (access_token, refresh_token, id_token) |
| `utils/oauth.py` | 1428  | warning | `{user_data}` | Full identity claims (email, name, sub, picture)              |
| `utils/oauth.py` | 1473  | warning | `{user_data}` | Same                                                          |
| `utils/oauth.py` | 1482  | warning | `{user_data}` | Same                                                          |
| `utils/oauth.py` | 872   | error   | `{token}`     | Full token response (access_token, refresh_token)             |

**These are the most severe — they dump entire credential/identity objects to logs.**

**Fix approach:** Replace `user.email` with `user.id` in all log messages. For OAuth error paths, redact token values and extract only non-sensitive fields for logging. Add `__repr__` to UserModel as a defense-in-depth measure.

## Code References

- `backend/open_webui/__init__.py:71,86` — uvicorn.run() calls without access_log param
- `backend/start.sh:83-87` — Docker uvicorn startup without --no-access-log
- `backend/open_webui/utils/logger.py:180-190` — Logger setup, InterceptHandler, uvicorn logger config
- `backend/open_webui/config.py:42-48` — EndpointFilter (only filters /health)
- `backend/open_webui/env.py:108` — GLOBAL_LOG_LEVEL default
- `backend/open_webui/services/onedrive/graph_client.py:208` — download_file with token URLs
- `backend/open_webui/services/google_drive/drive_client.py:279,291` — download/export with redirects
- `backend/open_webui/models/users.py:83-121` — UserModel without **repr**
- `backend/open_webui/models/auths.py:128,160` — Email logged at INFO
- `backend/open_webui/utils/oauth.py:872,1418,1428,1473,1482` — Full token/claims dicts logged
- `backend/open_webui/services/retention/service.py:163-216` — Email in retention logs
- `backend/open_webui/routers/tasks.py:183-610` — Email in debug logs
- `backend/open_webui/utils/telemetry/instrumentors.py:100-111` — Full URL in OTEL spans

## Architecture Insights

The logging architecture has a clean pipeline (standard logging → InterceptHandler → Loguru) that makes fixes straightforward:

- Third-party library loggers (httpx, uvicorn) can be silenced by setting their log level in `start_logger()`
- The audit log system (`AUDIT_LOG_LEVEL`) is separate and already captures access information, making uvicorn access logs redundant
- OTEL instrumentation adds a parallel exposure path that also needs addressing if OTEL is enabled

## Open Questions

1. Should we add a defensive `__repr__` to UserModel even though no current code logs the full object? (Recommended: yes, as defense-in-depth)
2. For retention/archival logs that genuinely need to identify users — should we use `user.id` only, or is there a need for a separate audit channel?
3. The OTEL httpx hook at `instrumentors.py:108` writes full URLs to spans — should this also be sanitized?
