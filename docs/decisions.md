# Decisions

Append-only log, one line each: `YYYY-MM-DD  decision  PR`. Newest on top.
Scope-tag module-specific entries, e.g. `[connect]`. Cross-repo decisions go
to `soev-docs/decisions.md`, not here.

2026-10-07  [soev] the v2 migration ingests every loose upload a stored chat attaches into its owner's attachments collection and points the file's `collection_name` there (`--uploads`, chart `mode: uploads`, also in `--apply`); the agent turn takes an attachment's collection from the file record, never from the chat entry, whose v1 `file-<id>` soev-api does not have
2026-10-07  [soev] `migrate --apply --models` (chart `mode: models`) rewrites only stored model ids under its own migration id and snapshot, so catalog id renames reuse the reversible v2 rewrite; several ids mapped to one target merge into the most recently updated model row; the welcome screen shows descriptions for assistants only  #378
2026-09-22  Claude scratch (specs, plans, reviews, handoffs) lives only in git-ignored `thoughts/<kind>/`; `docs/` holds what is true now, never plans or session artefacts  #317
