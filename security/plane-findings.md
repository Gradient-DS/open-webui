RUN-TAG: owui-phase7a-live-hardening

## PLANE-001: Stored upload-limit string causes persistent upload denial of service

Status: **open, to be fixed on dev**. Reported by the live reviewer on
2026-09-08; this branch changes only the attack plane, not the application.

Source locations relative to `backend/open_webui/`:

- `routers/retrieval.py:882` accepts `FILE_MAX_SIZE` as `int | str | None`.
  `routers/retrieval.py:1152` retains any nonempty string and
  `routers/retrieval.py:1258` writes it to `rag.file.max_size`.
- `routers/configs.py:102` also accepts arbitrary values through config import.
- `routers/files.py:296` reads the stored value; `routers/files.py:303` calls
  `int(max_size_mb)` without guarding conversion errors. The neighbouring
  Content-Length conversion is guarded, but the stored limit conversion is not.

An authenticated admin can persist a non-numeric upload limit. Every subsequent
multipart upload with Content-Length then fails with HTTP 500 for every user,
until the value is repaired. The reviewer observed the stored value
`![x](file:///etc/passwd#gdsprobe7f3a)` and its `ValueError` in the live stack.
This finding is a stored-value denial of service; it does not establish file read.

Exact reproduction against the affected live build (use valid admin and user
bearers in `ADMIN_TOKEN` and `USER_TOKEN`, and the stack URL in `ATTACK_BASE_URL`):

```sh
curl --fail-with-body -X POST "$ATTACK_BASE_URL/api/v1/retrieval/config/update" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  --data '{"FILE_MAX_SIZE":"![x](file:///etc/passwd#gdsprobe7f3a)"}'
curl --fail-with-body "$ATTACK_BASE_URL/api/v1/configs/export" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
curl -i -X POST "$ATTACK_BASE_URL/api/v1/files/?process=false" \
  -H "Authorization: Bearer $USER_TOKEN" \
  -F 'file=@security/plane-findings.md;type=text/plain'
```

The export contains `"rag.file.max_size":"![x](file:///etc/passwd#gdsprobe7f3a)"`;
the upload returns 500 and logs
`ValueError: invalid literal for int() with base 10: '![x](file:///etc/passwd#gdsprobe7f3a)'`.
The import equivalent is `POST /api/v1/configs/import` with
`{"config":{"rag.file.max_size":"![x](file:///etc/passwd#gdsprobe7f3a)"}}`.
After reproduction, restore the original limit through config import (or set
`{"config":{"rag.file.max_size":10}}` for a finite 10 MiB recovery limit).

Plane containment: both write routes and all their derived fields remain driven.
Each write snapshots this key, records changed values in the pass artifact's
`config_findings`, and restores and verifies the snapshot in `finally`. Recovery
HTTP calls never count as route hits. Before resolving any fixtures, setup reads
the key and repairs a non-numeric value left by an interrupted or older run to
10 MiB, reporting the original value. Valid existing limits, including numeric
strings and null/unlimited, are preserved. Failures to read or restore are loud.
The rule and rationale live in `security/attack-surface.toml`.
