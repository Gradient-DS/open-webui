---
date: 2026-07-03T21:04:42+02:00
researcher: Lex Lubbers (orchestrated by Claude)
git_commit: 7ea582e958a6720cfeec74af6a6fbe1323132923
branch: feat/upstream-v0.10.2-merge
repository: open-webui (Gradient-DS fork) + genai-utils (parallel track)
topic: 'Phase 9 Warren Format Parity + Full-Stack Local E2E Implementation Strategy'
tags: [implementation, strategy, warren, doc-pipeline, cloud-sync, upstream-merge, e2e]
status: complete
last_updated: 2026-07-03
last_updated_by: Lex Lubbers
type: implementation_strategy
---

# Handoff: Warren format parity (Phase 9) + full-stack local e2e before PR #225 un-drafts

## Task(s)

1. **COMPLETED — upstream v0.10.2 merge (Phases 1–8)**: plan `thoughts/shared/plans/2026-07-02-upstream-v0.10.2-merge.md` fully executed via subagent-driven development. 148 conflicts resolved, every task independently reviewed. Branch commits: merge `19ce61c7b` → alembic head-merge `3acd88373` → docs `9bd30b815` → scheduler config fix `641531a22` → cloud-sync `.value` sweep `7ea582e95`. **PR #225 to dev is open but set to DRAFT** at user request — it must not leave draft until the full-stack local e2e below proves real syncs work through warren.
2. **IN PROGRESS — full-stack local e2e environment (stack-1)**: a subagent was standing up the warren distributed pipeline (RabbitMQ + pipeline API + parser/chunker/embedder workers) as compose project `stack-1-dp` from the genai-utils worktree, wiring OWUI's `doc_pipeline.*` config to it, adding tmux windows (`dp-logs`) to session `stack-1`, and proving txt+pdf uploads flow pending→processing→completed with chunks landing. Its report (check first!): `.superpowers/sdd/task-30-stack-report.md` in the OWUI worktree. If absent/incomplete, the bring-up needs finishing — the dispatch spec is reproduced in "Action Items" below.
3. **PLANNED — Phase 9 warren format parity** (plan §9, genai-utils): make every one of the 27 tenant `allowed_extensions` parse through warren end-to-end or get explicitly dropped. Baseline (verified 02-07): 14/27 supported; `htm` parser-capable but not routed; 12 unsupported.

## Critical References

- `thoughts/shared/plans/2026-07-02-upstream-v0.10.2-merge.md` — §Phase 9 (lines 243-268) is the spec for this session; D2 addendum matters for sync-protocol work.
- `.superpowers/sdd/progress.md` (OWUI worktree, git-excluded) — complete 30-task ledger with per-task review outcomes, minor-findings list, cross-task notes.
- PR #225 description — verification evidence + port-later backlog + items flagged for human decision.

## Recent changes

All merge-phase changes are in the 5 branch commits (see `git log 10a8d1835..HEAD`). Directly relevant to this session's work:
- `backend/open_webui/services/sync/scheduler.py` — now takes dotted config keys, `await Config.get` per tick (commit `641531a22`).
- 19 cloud-sync files (`services/{topdesk,confluence,google_drive,onedrive}/**`, `services/sync/base_worker.py`, sync routers) — PersistentConfig-era `.value` reads eliminated; many auth helpers became async (commit `7ea582e95`).
- `backend/open_webui/utils/doc_pipeline.py` — untouched by merge; Phase 9a's one-liner (`'htm'` into `PIPELINE_SUPPORTED_FORMATS`) is still TODO.

## Learnings

- **Config is per-key now**: every read is `await Config.get('dotted.key')`; keys MUST exist in `config.py::DEFAULT_CONFIG` (silent None otherwise). Fork key map: `.superpowers/sdd/task-4-inventory.md`. Any `.value` on a config module var is a bug (class made extinct — see sweep table at end of `.superpowers/sdd/task-28-report.md`).
- **Scratch-DB env trap**: root `.env` DB_VARS cause `env.py:281-284` to reconstruct a postgres DATABASE_URL over yours. Set `DATABASE_TYPE='' DATABASE_USER='' DATABASE_PASSWORD='' DATABASE_HOST='' DATABASE_PORT='' DATABASE_NAME=''` (empty strings) + `DATABASE_URL=sqlite:///...`.
- **Port map**: `:5173/:8080` = MAIN checkout (different branch!). Stack-1 (this branch) = fe `:18173`, be `:18180`, postgres `:18132`, weaviate `:18182`/`:18153` (registry `~/.config/soev/stacks.json`). gradient-core shared services incl. LEGACY doc-processor `:8002` (do not confuse with warren).
- **Warren deploy facts** (plan §9c): dp-worker image must pin `warren[...http]==0.2.2` (`document_processing/distributed/pipeline/deployment/requirements.worker.txt`) — 0.2.1 lacks the `url` resolver, every presigned-GET fetch hard-fails with `UnknownLocationTypeError`. Presign TTL (`PIPELINE_PRESIGN_TTL_SECONDS`) must survive RMQ backlog + retry ladder (expired presign = 403 = hard failure; only 404 retries).
- **Open runtime question from smoke**: chat streaming on stack-1 produced 0 tokens/120s with no backend error — suspected stack provider (agent API) config, not merge code. Worth resolving while doing e2e (same stack).
- **stack manager**: `cd genai-utils && ./.venv/bin/python -m scripts.stack.stack {up,down,list,logs}` — per-stack = agent+OWUI+postgres+weaviate+loader; shared = `gradient-core` compose project; warren pipeline is NOT part of either (hence the custom `stack-1-dp` bring-up).

## Artifacts

- `thoughts/shared/plans/2026-07-02-upstream-v0.10.2-merge.md` (committed) — master plan.
- `thoughts/shared/research/v0.10.2-carveout-diffs/*.md` (committed) — per-hunk carve-out port audits.
- `.superpowers/sdd/progress.md` + `task-{1..28}-report.md` + `task-4-inventory.md` (OWUI worktree, git-excluded) — full execution trail.
- `.superpowers/sdd/task-30-stack-report.md` — full-stack bring-up report (check existence — may still be being written).
- `.superpowers/sdd/task-28-report.md` — smoke results + `.value` sweep table; screenshots in `.superpowers/sdd/t28-shots/`.
- PR: https://github.com/Gradient-DS/open-webui/pull/225 (DRAFT).

## Action Items & Next Steps

1. **Verify/finish the stack-1-dp bring-up** (read `.superpowers/sdd/task-30-stack-report.md` first). Required end state: RabbitMQ + pipeline API + parser/chunker/embedder workers running (compose project `stack-1-dp`, genai-utils worktree checkout, 181xx ports); OWUI stack env has `doc_pipeline.*` family pointing at it (DISTRIBUTED_DOC_PIPELINE enabled); `owui-be` tmux window restarted; tmux window `dp-logs` tailing pipeline logs; e2e proof: txt + pdf upload → pending→processing→completed → chunks in weaviate/results store.
2. **Real-sync e2e**: with the stack up, run a real cloud sync (OneDrive or Confluence vs a test tenant — creds availability per stack .env) end-to-end through warren; confirm the reconciler (`services/doc_pipeline_reconciler.py`) closes the loop and no file sticks at `ingesting` (the pre-merge bug class from `f91f47242`).
3. **Phase 9a (OWUI one-liner)**: add `'htm'` to `PIPELINE_SUPPORTED_FORMATS` in `backend/open_webui/utils/doc_pipeline.py`; verify `should_route_to_pipeline('x.htm', ...)` → True; commit on this branch.
4. **Phase 9b (genai-utils, in its worktree `.worktrees/feat/upstream-v0.10.2-merge/`)**: per plan tiers — trivial: `tsv`/`json`/`rst` via existing text/csv processors' `supported_types`; moderate: `odt`/`ods`/`odp` (odfpy), `epub` (ebooklib), `msg` (extract-msg), `rtf` (striprtf); each = processor + registration + bytes-sniffing entry + OWUI `PIPELINE_SUPPORTED_FORMATS` + fixture parse test, e2e-verified against the local stack. **Hard tier decision needed from user: build LibreOffice-conversion sidecar for `doc`/`xls`/`ppt` vs drop from tenant list.**
5. **Phase 9c**: verify `requirements.worker.txt` warren pin ==0.2.2 + CI/image check; review `PIPELINE_PRESIGN_TTL_SECONDS` default vs observed queue latency in the local stack.
6. **Before un-drafting PR #225**: chat-stream 0-token issue resolved/explained; full-stack e2e green (uploads + one real sync); then `gh pr ready 225`. Also carry the PR's "needs human attention" items (ENABLE_MEMORIES default, max-files restart note) to the user.

## Other Notes

- Success criteria for Phase 9 are in the plan §9 (lines 265-268): every format in the 27-list parses through warren or is documented-dropped; `htm` routes; image/pin check green; presign TTL reviewed.
- OWUI-side pipeline touchpoints: `utils/doc_pipeline.py`, `services/doc_pipeline_reconciler.py`, `routers/retrieval.py::submit_existing_file_to_pipeline` (hard-imported by `routers/integrations.py`), `services/files.py` emit_file_status.
- genai-utils warren layout: `document_processing/distributed/{warren,pipeline,e2e_test}/` — `pipeline/deployment/` has the compose files + Dockerfiles; `document_processing/` is officially deprecated EXCEPT this distributed subtree is the active warren work (per merge-plan §9 it's the only acceptable parse route going forward).
- The `validate-owui` skill (browser e2e via owui-e2e agent) needs the playwright MCP server approved in the session — it wasn't connectable this session; scripted fallback used `.superpowers/sdd/t28-smoke.py`.
- Port-later backlog + deferred decisions live in PR #225's description and the plan §8 table — don't re-derive.
