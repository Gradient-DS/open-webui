---
description: Kick off N parallel feature/fix branches with running dev stacks — one per task in $ARGUMENTS
---

# /kickoff_features — kickoff N parallel branches

You are setting up a parallel multi-stack dev session. The user gave a list of task descriptions in `$ARGUMENTS`; for each one, create a branch in both repos, spin up a worktree-isolated stack, and report back with URLs ready for the user to start work.

## Inputs

`$ARGUMENTS` is one or more tasks, separated by newlines or semicolons. Each task is freeform.

For each task derive a branch name:
- Default prefix `feat/`.
- Use `fix/` if the task mentions bug / security / vulnerability / regression / hotfix / CI-failure.
- Use `chore/` for dependency bumps, lint fixes, refactors with no behavior change.
- Use `docs/` for documentation-only changes.
- Use `merge/` for merge reconciliation branches.
- If the task description already starts with `<prefix>/...` or includes a literal branch name (`branch: feat/foo`), honor it.
- If a Linear ticket ID (`ENG-XXXX`) is present, include it in the slug.
- Slugify: lowercase, alphanumeric + hyphens, ≤ 40 chars after the prefix.

If `$ARGUMENTS` is empty, ask the user for the task list before doing anything else.

## Steps

### 0. Sanity-check the host environment

Run each check; if any fails, stop and surface a clear hint:

- `docker info` returns exit 0 (Docker daemon running)
- `tmux -V` returns a version (`brew install tmux` if missing)
- `source /Users/lexlubbers/Code/soev/genai-utils/.venv/bin/activate && python -c "import click"` succeeds (the stack manager's deps are installed)

### 1. Make sure the core stack is up

```
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8003/health
```

If not 200:
```
cd /Users/lexlubbers/Code/soev/genai-utils && docker compose --env-file .env -f deploy/projects/soev/compose.core.yaml up -d
```
Wait for `localhost:8003/health` to return 200 before continuing.

### 2. Fetch latest in both repos

```
cd /Users/lexlubbers/Code/soev/genai-utils && git fetch origin dev
cd /Users/lexlubbers/Code/soev/open-webui && git fetch origin dev
```

### 3. Determine the worktree parent

Check whether the multi-stack compose files are on `origin/dev` yet:

```
cd /Users/lexlubbers/Code/soev/genai-utils && git ls-tree -r origin/dev --name-only | grep -E "^deploy/projects/soev/compose\.stack\.yaml$"
cd /Users/lexlubbers/Code/soev/open-webui && git ls-tree -r origin/dev --name-only | grep -E "^docker-compose\.soev-stack\.yaml$"
```

If BOTH found → parent = `origin/dev` (per-repo). If either is missing → parent = `feat/multi-stack-dev-environment-v1` (pre-merge state; PRs Gradient-DS/genai-utils#141 + Gradient-DS/open-webui#136). Use whichever parent works PER REPO.

### 4. For each task

Derive `BRANCH = <prefix>/<slug>`. Then:

```
cd /Users/lexlubbers/Code/soev/genai-utils && git worktree add .worktrees/$BRANCH -b $BRANCH <genai-parent>
cd /Users/lexlubbers/Code/soev/open-webui && git worktree add .worktrees/$BRANCH -b $BRANCH <owui-parent>
```

Copy the gitignored env files into each worktree:
```
cp /Users/lexlubbers/Code/soev/genai-utils/.env /Users/lexlubbers/Code/soev/genai-utils/.worktrees/$BRANCH/.env
cp /Users/lexlubbers/Code/soev/genai-utils/deploy/projects/soev/.env /Users/lexlubbers/Code/soev/genai-utils/.worktrees/$BRANCH/deploy/projects/soev/.env
[ -f /Users/lexlubbers/Code/soev/open-webui/.env ] && cp /Users/lexlubbers/Code/soev/open-webui/.env /Users/lexlubbers/Code/soev/open-webui/.worktrees/$BRANCH/.env || true
```

Bring the stack up:
```
cd /Users/lexlubbers/Code/soev/genai-utils && python -m scripts.stack.stack up $BRANCH
```

### 5. Report

Print a markdown table with one row per task:

| Task | Branch | OWUI FE | Agent | Logs |
|------|--------|---------|-------|------|

For each: branch name, `http://localhost:<owui_fe>`, `http://localhost:<agent>`, `tmux attach -t stack-<idx>`.

Then suggest the next move: "Open <N> Claude Code windows, one per worktree (`cd <repo>/.worktrees/<branch>`), and `/implement_plan <plan-path>` or start the work directly."

## Failure modes to handle clearly

- **Port collision** with an existing stack → suggest `python -m scripts.stack.stack list` to find the conflict.
- **`.env` file missing** in either primary repo → ask the user; don't proceed.
- **A task's branch already exists** → ask the user: skip / nuke + recreate / use existing worktree.
- **Compose files missing on a worktree's branch** → the stack manager pre-flights this; re-parent the worktree to the multi-stack branch per the hint it emits.

## What this does NOT do

- Run any per-task implementation work — only sets the stage.

## What this DOES do (since `/stack up` bootstrap, v1.1)

- Auto-creates each worktree's `.venv` (using whatever Python runs the stack manager) and runs `pip install -r agents/requirements.txt` / `pip install -e ".[dev]"` if those weren't already done.
- Auto-runs `npm install` in the open-webui worktree if `node_modules/` is missing.
- After bootstrap, the tmux session's host-process panes (agent / OWUI BE / OWUI FE) actually start cleanly — the FE URL becomes reachable in the browser within ~10–15s.
- First-time bootstrap per worktree takes 3–10 min (depending on pip/npm cache hotness); subsequent `stack up` calls are near-instant since bootstrap is idempotent.
- Pass `--no-bootstrap` to `/stack up` directly if you want to skip it (e.g., on a known-bootstrapped worktree for fast iteration).

## References

- `/stack` slash command at `.claude/commands/stack.md`
- `deploy/projects/soev/LOCAL_DEV.md` § Multi-stack mode
- `thoughts/shared/research/2026-05-24-multi-stack-dev-environment.md`
