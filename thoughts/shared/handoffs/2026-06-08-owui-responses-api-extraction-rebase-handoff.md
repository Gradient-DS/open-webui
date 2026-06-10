# Handoff: OWUI `feat/responses-api-extraction` rebased onto `dev` — ready for full-stack testing

**Date:** 2026-06-08 (evening)
**Repo:** `open-webui` (Gradient-DS fork)
**Worktree:** `/Users/lexlubbers/Code/soev/open-webui/.worktrees/feat/responses-api-extraction`
**Branch tip:** `26c165530` — pushed to `origin/feat/responses-api-extraction` with `--force-with-lease`.
**State vs `origin/dev`:** **0 behind / 1 ahead**, clean working tree.

**Companion:** the genai-utils side (`feat/responses-api-extraction` in `genai-utils`) landed its OSS-extraction work + parallel forward-port + merge of `origin/dev` earlier this session — see [`/Users/lexlubbers/Code/soev/genai-utils/.worktrees/feat/responses-api-extraction/thoughts/shared/handoffs/2026-06-08-oss-extraction-merge-complete-handoff.md`](../../../../../genai-utils/.worktrees/feat/responses-api-extraction/thoughts/shared/handoffs/2026-06-08-oss-extraction-merge-complete-handoff.md). With this rebase done, both sides line up and a full-stack local test is unblocked.

---

## What the rebase did

Branch was 82 behind / 2 ahead of `origin/dev` before. Rebase result:

| Original commit | Outcome |
|---|---|
| `daedb7122 fix(chat): drop !_chatId guard so title/tags fire when chat is pre-created` | **Skipped** — dev had converged on the same fix (the `!_chatId` clause is already gone on `dev`, and a clearer `[Gradient]` comment block was added at the same site). The conflict was purely in the explanatory comment above the changed line; dev's comment is more current ("pendingAgentId binding lands before /chat/completions") so we took it via `git rebase --skip`. No code change lost. |
| `c378e39c6 fix(middleware): skip tool loop when upstream finish_reason=stop` | **Replayed cleanly** as `26c165530`. Conflict-free — dev's churn was elsewhere. The fix still does what its message says: clears `tool_calls` when `last_finish_reason == 'stop'` so OWUI's middleware doesn't kick off a second `generate_chat_completion` after the agent's intended final answer. Verified earlier against agent's `web_search` / `fetch_url` / `summarize` tool flows; single POST, no spurious second call. |

`git rev-list --left-right --count origin/dev...HEAD` → `0    1`. Working tree clean.

## Why this matters now

The genai-utils side's session merged `origin/dev` and brought in (via `soev-agents` PR #4 forward-port + pin bump to `772ba42`):

- MT-Weaviate dual-read fallback + routing.
- Gemma 4 hotfix v1.0.1 (`repetition_penalty=1.1` + `bind_sampling_defaults`) and the `hide_web_snippets` render-time replacement for the empty-answer-causing forced `tool_choice` on Gemma.
- Per-request `enabled_features["citations"]` gate threaded through both citation dispatchers + `dag_research`.

The OWUI side needs to forward the `citations` capability into the agent payload for the citations gate to fire end-to-end. That counterpart was on `feat/forward-citations-capability` (`911b103e1 feat(agent): forward model citations capability to external agent`) — **already merged into `dev` (and `test`) before this rebase**, so it came along with the rebase onto `origin/dev` automatically. End-to-end citations-toggle testing is unblocked on this branch as-is; no additional merge needed.

## Known upstream issues you may hit during testing

Unchanged from the prior handoffs — both pre-date this work and need fixes on the OWUI fork side:

1. **`raptor_summary` `list_files` 401** — `soev_agents/adapters/openwebui/retrieval/providers/_openwebui_files_client.py:107` hits `/api/v1/knowledge/{kb}/files`, OWUI's public knowledge endpoint, which requires a user-session cookie/JWT. The agent only has the service bearer (`AGENTS_API_KEY`). Two paths to fix:
   - Add `/api/v1/internal/retrieval/knowledge/{kb}/files` to OWUI (Gradient-DS fork), mirroring the auth shape of `/api/v1/internal/retrieval/accessible-kbs`. Then point the soev-agents client at the new URL.
   - Or teach the public endpoint to accept `Bearer + X-Acting-User-Id` as an additional auth path.
2. **`cannot pickle 'coroutine' object` in OWUI's `process_chat`** — `open_webui/main.py:2805` post-response handling crashes after the agent returns 200 cleanly. Co-occurs with `WARNING - No OAuth session found for user …`, suggesting an un-awaited coroutine when the OAuth context isn't ready.

Either bug will surface during a real smoke test; both belong upstream, not on this branch.

## What to test (suggested order)

Build on the genai-utils smoke results from the earlier handoff. With the OWUI branch now up to date, the basic round of agents should all still reach their code paths:

| Agent | Expected |
|---|---|
| `soev_chat_autonomous` | ✅ works |
| `soev_chat_manual` (`IfcChatAgent`) | ✅ works |
| `assistant_onboarding` (`ConversationalDraftAgent`, relocated) | ✅ works (post `:groq` strip in `08e3b052`) |
| `soev_kb_summary` (`RaptorSummaryAgent`, relocated) | Reaches its code path; blocked by the OWUI `list_files` 401 above |

**New surfaces worth touching while you're in here:**

- **Citations toggle** — only end-to-end if OWUI also forwards the capability (see the `forward-citations-capability` note above). Without it, the agent stays in default-True mode and behaves as before.
- **MT-Weaviate dual-read** — requires a tenant configured with `ENABLE_WEAVIATE_MULTITENANCY_MODE` AND the deploy YAML setting `weaviate_multitenancy_enabled: true` for the openwebui_direct provider (default is `false`/behavior-neutral). If you're not in a migrating tenant, this path is dormant.
- **Gemma 4 `hide_web_snippets`** — only fires when the deploy hits `google/gemma-4-31B-it`; on other models the renderer keeps snippet bodies.
- **Title + tags fire on chat 1** — the change that converged with `daedb7122`. Still worth a quick visual check: send the first message of a fresh chat, confirm title and tags appear.

## Quick start (full stack)

```bash
# 1) genai-utils side (already at the right tip)
cd /Users/lexlubbers/Code/soev/genai-utils/.worktrees/feat/responses-api-extraction
git status            # expect: M deploy/projects/soev/config/agents/base.yaml (personal tracing toggle)
git log --oneline -3
uv pip install -q -r agents/requirements.txt -r api/requirements.txt
uv run python -m pytest agents/tests/ -q | tail -3   # expect: 1343 passed, 27 skipped

# 2) OWUI side (this worktree)
cd /Users/lexlubbers/Code/soev/open-webui/.worktrees/feat/responses-api-extraction
git status            # expect: clean
git log --oneline -3  # 26c165530 should be on top
npm install
# Frontend dev server in one terminal:
npm run dev
# Backend in another terminal (Python deps managed via the project's existing setup):
open-webui dev

# 3) Launch the local agents stack (the prior handoff used stack-2):
cd /Users/lexlubbers/Code/soev/genai-utils/.worktrees/feat/responses-api-extraction
set -a; source .env; set +a
python -m agents.deploy.local --project soev --env stack-2
```

If something breaks during the smoke run, the prior handoff has the bootstrap log shape to compare against (`Registered agent ...` lines for the 5 agents) so you can quickly localize whether the failure is in registration, the agent code path, or the OWUI ↔ agent wire.

## Out of scope (don't pick up)

- Opening the genai-utils PR (paused on the genai-utils side too — both branches are pushed but no PR yet).
- Upstream fixes for the `list_files` 401 and `process_chat` coroutine pickle — file against the OWUI fork.
- The personal `genai-utils` `base.yaml` tracing toggle (uncommitted) — keep it local.

## Pointers

- **Prior genai-utils handoff (this session):** `/Users/lexlubbers/Code/soev/genai-utils/.worktrees/feat/responses-api-extraction/thoughts/shared/handoffs/2026-06-08-oss-extraction-merge-complete-handoff.md`
- **OWUI citations-capability companion:** already in `dev` (was `feat/forward-citations-capability`, branch + worktree cleaned up after merge confirmation).
- **soev-agents `main`:** at `772ba42` (PR #4 forward-port + PR #5 gitlink cleanup, both merged).
- **Remaining OWUI worktree on disk:** just this one (`feat/responses-api-extraction`). `forward-citations-capability`, `fix/ingest-event-loop-blocking`, and `fix/render-html-tables` were all merged into `dev` already and their worktrees + local branches were removed end-of-session.

## Sanity script for the next session

```bash
cd /Users/lexlubbers/Code/soev/open-webui/.worktrees/feat/responses-api-extraction
git status
git log --oneline -3
git fetch origin
git rev-list --left-right --count origin/dev...HEAD
```

Expected: clean tree, `26c165530` on top, `0  1` (zero behind, one ahead). If you've been here before and added more commits, that number will grow.
