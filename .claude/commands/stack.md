---
description: Multi-stack dev environment manager (up/down/list/open/logs/nuke per branch)
---

# /stack — soev multi-stack dev environment manager

Spin up parallel feature-branch dev stacks on macOS (or Linux) with any
Docker-compatible runtime. Default per-stack: agent + OWUI; everything
else shared from an always-on `gradient-core` Compose project.

Run (use the genai-utils venv's python — `click` is installed there;
system `python` may not have it):

```
cd /Users/lexlubbers/Code/soev/genai-utils && /Users/lexlubbers/Code/soev/genai-utils/.venv/bin/python -m scripts.stack.stack $ARGUMENTS
```

Surface the command's stdout/stderr to the user. If `$ARGUMENTS` is
empty, run with `--help`.

## Examples

- `/stack up feat/some-feature` — bring up a stack on `.worktrees/...`
- `/stack down feat/some-feature` — stop containers, keep volumes
- `/stack list` — see all registered stacks + their URLs + reconciled status
- `/stack open feat/some-feature` — open OWUI in the default browser
- `/stack open feat/some-feature agent` — open the agent URL instead
- `/stack logs feat/some-feature` — attach to tmux session (host process logs)
- `/stack logs feat/some-feature postgres` — `docker compose logs -f postgres`
- `/stack nuke feat/some-feature` — typed confirmation, removes volumes too

## Prerequisites

- Any Docker runtime: Docker Desktop, Colima, OrbStack, Podman Desktop,
  Rancher Desktop. `docker info` must succeed.
- `tmux`: `brew install tmux`
- Core stack up:
  `docker compose --env-file genai-utils/.env -f genai-utils/deploy/projects/soev/compose.core.yaml up -d`
- Worktree exists in both repos:
  `git worktree add .worktrees/<branch> -b <branch> origin/dev`
- Stack manager deps:
  `pip install -r genai-utils/scripts/stack/requirements.txt`

## References

- Design: `thoughts/shared/research/2026-05-24-multi-stack-dev-environment.md`
- Plan: `thoughts/shared/plans/2026-05-24-multi-stack-dev-environment-v1.md`
- LOCAL_DEV.md: `genai-utils/deploy/projects/soev/LOCAL_DEV.md`
