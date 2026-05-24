---
description: End-of-life for a feature branch (post-PR-merge): nuke stack + remove worktrees + delete local branch
---

# /cleanup_feature — tear down a feature branch's stack + worktrees

After the branch's PR has merged on GitHub, run this to fully clean up
the local artifacts: per-stack containers + volumes, env files, the
worktrees in both repos, and the local feature branch.

Run (use the genai-utils venv's python — `click` is installed there;
system `python` may not have it):

```
cd /Users/lexlubbers/Code/soev/genai-utils && /Users/lexlubbers/Code/soev/genai-utils/.venv/bin/python -m scripts.stack.stack cleanup $ARGUMENTS
```

Surface the command's stdout/stderr to the user. If `$ARGUMENTS` is
empty, run with `--help`.

## Safety

By default, refuses to run if no MERGED PR exists for the branch in
either `Gradient-DS/genai-utils` or `Gradient-DS/open-webui`. Pass
`--force` if the branch was abandoned without a PR (rare).

## Examples

- `/cleanup_feature feat/some-feature` — standard end-of-life
- `/cleanup_feature feat/abandoned-spike --force` — no PR ever existed

## What gets deleted

- Per-stack containers + Docker volumes (compose `down -v`)
- `.env.<project_name>` files in both worktrees
- Registry entry for the branch (`~/.config/soev/stacks.json`)
- `.worktrees/<branch>/` directory in both repos (`git worktree remove --force`)
- Local feature branch in both repos (`git branch -D`)

Idempotent: each step tolerates the resource not existing (single-repo
branch, no stack ever created, etc.).

## What stays

- The MERGED commits (they live on `dev` now via the merge commit)
- Anything not specific to this branch
- The remote feature branch (GitHub usually auto-deletes after merge)

## References

- `/stack` slash command at `.claude/commands/stack.md`
- `/kickoff_features` slash command at `.claude/commands/kickoff_features.md`
- `deploy/projects/soev/LOCAL_DEV.md` § Multi-stack mode
- `thoughts/shared/research/2026-05-24-multi-stack-dev-environment.md`
