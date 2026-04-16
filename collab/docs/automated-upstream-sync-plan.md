# Automated Upstream Sync — Implementation Plan

## Overview

Set up a GitHub Actions workflow that daily checks `open-webui/open-webui:main` for new commits, creates a merge branch, uses `anthropics/claude-code-action@v1` with a Claude Max OAuth token (Opus model) to resolve conflicts and generate a PR summary, then opens a draft PR targeting our `dev` branch.

## Current State Analysis

- **5 active workflows** in `.github/workflows/` — Docker build, PR checks, branch guard, security scanning, E2E tests
- **Branch guard** enforces: `dev → test → main` promotion. PRs to `dev` are unrestricted — upstream sync PRs can target `dev` directly.
- **Upstream remote** already configured: `open-webui/open-webui`
- **CLAUDE.md at repo root** contains upstream merge policy ("always preserve custom changes") — automatically picked up by claude-code-action
- **No existing automation** for upstream syncing — currently manual 20-phase process

### Key Discoveries:

- claude-code-action v1 moved `allowed_tools`, `model`, `max_turns` into `claude_args` parameter (`.github/workflows/pr-checks.yaml:1`)
- Existing workflows use `actions/checkout@v5` consistently (`.github/workflows/docker-build-soev.yaml:49`)
- PR checks run on PRs to `dev`, `test`, `main` — the sync PR will trigger them automatically (`.github/workflows/pr-checks.yaml:4-8`)

## Desired End State

1. Daily at 06:00 UTC (or manual trigger), workflow checks if we're behind `upstream/main`
2. If behind: reuses the `upstream-sync` branch (reset to `dev`, re-merge `upstream/main`, force-push) — or creates it fresh if it doesn't exist
3. Claude (Opus) resolves conflicts following our merge policy, generates categorized PR summary
4. If an open PR already exists for `upstream-sync → dev`, it updates automatically (force-push updates the diff). Otherwise a new draft PR is opened.
5. PR checks (build, lint, tests) run automatically on each update
6. You decide when the PR is ready to merge — no accumulating stale PRs

**Single-branch approach:** One long-lived `upstream-sync` branch, one PR. Each daily run rebuilds the branch from scratch (reset to current `dev` + fresh merge). This means the PR always shows the clean delta between your `dev` and upstream, and you merge it when you're ready.

### Verification:

- `workflow_dispatch` trigger works manually
- Clean merges (no conflicts) produce a PR with upstream changelog
- Conflicted merges produce a draft PR with resolution details and "needs-review" flags
- CLAUDE.md instructions are followed (custom code preserved)
- PR checks trigger on the opened PR

## What We're NOT Doing

- **Not auto-merging** — always opens a draft PR for human review
- **Not creating a new PR every day** — single `upstream-sync` branch is rebuilt daily, one PR stays current
- **Not replacing the manual merge process** for large catch-up merges (like the current 245-commit one) — this is for incremental daily sync
- **Not running on a self-hosted runner** — standard `ubuntu-latest` is sufficient
- **Not setting up Slack/email notifications** — GitHub's built-in PR notifications suffice for now

## Implementation Approach

Two files to create, one secret to configure. The `.github/CLAUDE.md` gives Claude merge-specific context beyond the repo-root CLAUDE.md. The workflow file handles the full automation pipeline.

---

## Phase 1: Create `.github/CLAUDE.md` for Merge Context

### Overview

Create a dedicated CLAUDE.md in `.github/` with merge-specific instructions. The claude-code-action reads both repo-root and `.github/` CLAUDE.md files. This one focuses on conflict resolution strategy.

### Changes Required:

#### 1. `.github/CLAUDE.md`

**File**: `.github/CLAUDE.md`
**Changes**: New file — merge-specific instructions for automated conflict resolution

```markdown
# Upstream Sync — Merge Instructions

You are resolving merge conflicts between our fork (Gradient-DS/open-webui) and upstream (open-webui/open-webui).

## Golden Rule

**Always preserve our custom code.** When in doubt, keep our version and flag it for human review.

## Custom Features (NEVER remove these)

Our fork adds these features on top of upstream Open WebUI:

- **Cloud sync integrations**: OneDrive (`routers/sync_onedrive.py`), Google Drive (`routers/sync_google_drive.py`), shared abstraction (`routers/sync_utils.py`, `models/sync_*.py`)
- **TOTP 2FA**: `routers/auths.py` (2FA endpoints), `models/auths.py` (totp fields), frontend `TwoFactor*.svelte`
- **Email invites**: Microsoft Graph integration in `routers/auths.py`
- **GDPR archival**: `routers/auths.py` (archive endpoint), `models/auths.py` (archived fields)
- **Data retention**: `utils/retention_service.py`, admin Database panel
- **Data export**: `routers/export.py`, background zip generation
- **Feature flags**: `FEATURE_*` env vars in `config.py`, exposed via `/api/config`
- **Agent proxy**: `routers/agent.py`, `ENABLE_AGENT_PROXY`
- **External pipeline/integration providers**: `EXTERNAL_RETRIEVAL_*` in `config.py` and `routers/retrieval.py`
- **Acceptance modal & feedback config**: `config.py` settings, frontend components
- **Typed knowledge bases**: `type` column in knowledge model, type validation in endpoints
- **Custom Helm chart**: `helm/open-webui-tenant/`

## Conflict Resolution Strategy

### Backend Python files:

1. If the conflict is in a file we've customized (see list above): **keep our code**, integrate upstream's non-conflicting changes around it
2. If the conflict is in a file we haven't customized: **accept upstream's version** (`git checkout --theirs <file>`)
3. For `config.py`: keep ALL our custom `FEATURE_*`, `ENABLE_*`, and integration env vars. Accept upstream's new additions.
4. For `main.py`: keep our custom router mounts and middleware. Accept upstream's new features.
5. For model files (`models/*.py`): keep our custom columns and methods. Accept upstream's refactors for non-custom parts.

### Frontend files:

1. For components we've customized: keep our additions, accept upstream's refactors for non-custom parts
2. For `translation.json` (i18n): keep ALL our custom translation keys. Accept upstream's new keys. Merge both.
3. For Svelte stores: keep our custom store additions. Accept upstream's changes to existing stores.

### When you can't resolve cleanly:

1. Add conflict markers with a comment: `// UPSTREAM-SYNC: needs human review — <reason>`
2. Stage the file as-is (with the marker comment, NOT with git conflict markers)
3. Note the file in the PR description under "Needs Your Review"

## PR Description Format

After resolving, generate `/tmp/pr_body.md` with these sections:

```
## What's New Upstream
(summarize notable upstream commits)

## Conflicts Resolved
(list files, what you did, and why)

## Needs Your Review
(files that need human attention — complex merges, judgment calls)

## Safe to Merge
(changes that are clearly safe — no conflicts, or trivial resolutions)

## Stats
- Upstream commits: N
- Files conflicted: N
- Auto-resolved: N
- Needs review: N
```
```

### Success Criteria:

#### Automated Verification:

- [ ] File exists at `.github/CLAUDE.md`
- [ ] Lists all custom features
- [ ] Contains conflict resolution strategy

#### Manual Verification:

- [ ] Instructions are accurate and complete for current custom features

---

## Phase 2: Create the Workflow File

### Overview

Create `.github/workflows/upstream-sync.yaml` — the main automation workflow.

### Changes Required:

#### 1. `.github/workflows/upstream-sync.yaml`

**File**: `.github/workflows/upstream-sync.yaml`
**Changes**: New file — full upstream sync automation

```yaml
name: Sync Upstream (Open WebUI)

on:
  schedule:
    - cron: '0 6 * * *'   # Daily at 06:00 UTC
  workflow_dispatch:        # Manual trigger

concurrency:
  group: upstream-sync
  cancel-in-progress: true

jobs:
  sync:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    permissions:
      contents: write
      pull-requests: write
      issues: write

    steps:
      - name: Checkout repository
        uses: actions/checkout@v5
        with:
          ref: dev
          fetch-depth: 0
          token: ${{ secrets.GITHUB_TOKEN }}

      - name: Configure git
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"

      - name: Fetch upstream and check for changes
        id: check
        run: |
          git remote add upstream https://github.com/open-webui/open-webui.git || true
          git fetch upstream main
          git fetch origin upstream-sync || true

          # Compare dev against upstream/main
          BEHIND=$(git rev-list dev..upstream/main --count)
          echo "behind=$BEHIND" >> "$GITHUB_OUTPUT"
          echo "Commits behind upstream/main: $BEHIND"

      - name: Check for manual-edits label
        if: steps.check.outputs.behind != '0'
        id: guard
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          # If an open PR exists with 'manual-edits' label, skip the rebuild
          LABEL=$(gh pr list --head upstream-sync --base dev --state open \
            --json labels --jq '.[0].labels[].name // empty' | grep -c '^manual-edits$' || true)
          if [ "$LABEL" -gt 0 ]; then
            echo "skip=true" >> "$GITHUB_OUTPUT"
            echo "Skipping — PR has 'manual-edits' label. Remove the label to resume auto-sync."
          else
            echo "skip=false" >> "$GITHUB_OUTPUT"
          fi

      - name: Prepare upstream-sync branch
        if: steps.check.outputs.behind != '0' && steps.guard.outputs.skip != 'true'
        run: |
          BRANCH="upstream-sync"
          echo "BRANCH=$BRANCH" >> "$GITHUB_ENV"

          # Always rebuild from current dev + fresh upstream merge
          # This ensures the branch is always a clean dev + upstream delta
          git checkout -B "$BRANCH" dev

          if git merge upstream/main --no-edit; then
            echo "CLEAN_MERGE=true" >> "$GITHUB_ENV"
            echo "Clean merge — no conflicts"
          else
            echo "CLEAN_MERGE=false" >> "$GITHUB_ENV"
            echo "Merge has conflicts — Claude will resolve"
            CONFLICTED=$(git diff --name-only --diff-filter=U | wc -l | tr -d ' ')
            echo "Conflicted files: $CONFLICTED"
          fi

      - name: Generate PR body for clean merge
        if: steps.check.outputs.behind != '0' && steps.guard.outputs.skip != 'true' && env.CLEAN_MERGE == 'true'
        run: |
          COMMIT_COUNT=$(git rev-list dev..upstream/main --count)
          cat > /tmp/pr_body.md << PREOF
          ## What's New Upstream ($COMMIT_COUNT commits)

          PREOF

          git log dev..upstream/main --oneline --no-merges | head -50 >> /tmp/pr_body.md

          cat >> /tmp/pr_body.md << 'PREOF'

          ## Conflicts Resolved

          None — clean merge.

          ## Safe to Merge

          All changes merged cleanly with no conflicts.

          ---
          *Last synced: $(date -u '+%Y-%m-%d %H:%M UTC')*
          PREOF

      - name: Claude resolves conflicts and writes PR summary
        if: steps.check.outputs.behind != '0' && steps.guard.outputs.skip != 'true' && env.CLEAN_MERGE == 'false'
        uses: anthropics/claude-code-action@v1
        with:
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
          prompt: |
            You are resolving merge conflicts for an upstream sync of Open WebUI.

            Current state: `git merge upstream/main` was run and produced conflicts.

            Steps:
            1. List conflicted files: `git diff --name-only --diff-filter=U`
            2. For each conflicted file, read it and resolve following .github/CLAUDE.md instructions
            3. After resolving each file, `git add` it
            4. After all files are resolved, run: `git -c core.editor=true merge --continue`
            5. Generate the PR description and save to /tmp/pr_body.md (format in .github/CLAUDE.md)

            If you cannot resolve a file cleanly:
            - Remove git conflict markers
            - Add a `// UPSTREAM-SYNC: needs human review` comment at the conflict site
            - Stage it anyway and note it in the "Needs Your Review" section

            Important: NEVER delete our custom code. See .github/CLAUDE.md for the full list of custom features.
          claude_args: |
            --model claude-opus-4-6
            --max-turns 30
            --allowedTools Bash,Read,Edit,Write,Glob,Grep

      - name: Push branch and create or update PR
        if: steps.check.outputs.behind != '0' && steps.guard.outputs.skip != 'true'
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          # Force-push to rebuild the branch from scratch each run
          git push --force origin "$BRANCH"

          # Default PR body if Claude didn't generate one
          if [ ! -f /tmp/pr_body.md ]; then
            echo "## Upstream Sync" > /tmp/pr_body.md
            echo "" >> /tmp/pr_body.md
            echo "Claude Code was unable to generate a detailed summary." >> /tmp/pr_body.md
            echo "Please review the changes manually." >> /tmp/pr_body.md
          fi

          # Check if a PR already exists for this branch
          EXISTING_PR=$(gh pr list --head "$BRANCH" --base dev --state open --json number --jq '.[0].number // empty')

          if [ -n "$EXISTING_PR" ]; then
            echo "Updating existing PR #$EXISTING_PR"
            gh pr edit "$EXISTING_PR" \
              --title "chore: sync upstream open-webui ($(date +%Y-%m-%d))" \
              --body-file /tmp/pr_body.md
          else
            echo "Creating new draft PR"
            gh pr create \
              --title "chore: sync upstream open-webui ($(date +%Y-%m-%d))" \
              --body-file /tmp/pr_body.md \
              --label "upstream-sync" \
              --base dev \
              --draft
          fi

      - name: Already up to date
        if: steps.check.outputs.behind == '0'
        run: echo "Fork is up to date with upstream/main. No PR needed."
```

### Design Decisions:

1. **Single long-lived branch** — `upstream-sync` is rebuilt from `dev` each run (`git checkout -B upstream-sync dev`), then force-pushed. No accumulating branches or PRs.
2. **Create-or-update PR logic** — checks if an open PR already exists for `upstream-sync → dev`. If yes, updates title + body. If no, creates a new draft PR. The force-push automatically updates the PR diff.
3. **Force-push is safe** — this is a bot-owned branch that nobody commits to. The branch is always disposable (rebuilt from scratch).
4. **`manual-edits` label guard** — if you pull the branch to fix conflicts yourself, add the `manual-edits` label to the PR. The workflow will skip the rebuild and log why. Remove the label when you're done (or merge the PR) to resume auto-sync.
4. **PR title updates with date** — so you can see at a glance when it was last synced.
5. **`timeout-minutes: 30`** — caps the total job time. Opus with 30 turns on a complex merge should fit.
6. **`concurrency: upstream-sync`** — prevents parallel sync runs if manually triggered during a scheduled run.
7. **Checkout `ref: dev`** — we branch from dev since that's our target.
8. **Clean merge fast path** — skips Claude entirely when there are no conflicts (saves subscription quota).
9. **`--draft`** — new PRs always open as draft, never auto-merges.
10. **Fallback PR body** — if Claude fails or times out, we still get a PR (just without the nice summary).
11. **`--allowedTools Bash,Read,Edit,Write,Glob,Grep`** — gives Claude file access + git commands. No web access or dangerous tools.

### Lifecycle:

```
Day 1: No upstream-sync branch → creates branch, opens draft PR #N
Day 2: PR #N still open → rebuilds branch, force-pushes, updates PR #N body
Day 3: You pull the branch, fix a conflict, push, add 'manual-edits' label
Day 4: Workflow sees label → skips rebuild, logs "manual-edits label present"
Day 5: You're happy with fixes → merge PR #N (or remove label to resume auto-sync)
Day 6: No open PR → creates fresh branch, opens new draft PR #M
```

### Success Criteria:

#### Automated Verification:

- [ ] Workflow YAML is valid: `actionlint .github/workflows/upstream-sync.yaml` (or GitHub validates on push)
- [ ] Workflow appears in Actions tab after push to dev

#### Manual Verification:

- [ ] `workflow_dispatch` trigger works from Actions tab
- [ ] First run creates a new draft PR
- [ ] Second run updates the existing PR (not a new one)
- [ ] After merging the PR, next run creates a fresh PR
- [ ] Clean merge scenario skips Claude
- [ ] Conflicted merge scenario invokes Claude
- [ ] PR targets `dev` branch
- [ ] PR checks (from `pr-checks.yaml`) trigger on the opened/updated PR

**Implementation Note**: After completing this phase, pause for manual testing via `workflow_dispatch` before considering it done.

---

## Phase 3: One-Time Setup (Manual Steps)

### Overview

Configure the OAuth token secret and create the `upstream-sync` label. These are manual steps in the GitHub UI.

### Steps:

#### 1. Generate OAuth token

Run locally:
```bash
claude setup-token
```
Copy the output token.

#### 2. Add GitHub secret

- Go to `Gradient-DS/open-webui` → Settings → Secrets and variables → Actions
- New repository secret:
  - **Name**: `CLAUDE_CODE_OAUTH_TOKEN`
  - **Value**: the token from step 1

#### 3. Create the `upstream-sync` label

```bash
gh label create upstream-sync --description "Automated upstream sync PRs" --color "0E8A16" --repo Gradient-DS/open-webui
gh label create manual-edits --description "Skip auto-sync rebuild — human is editing" --color "D93F0B" --repo Gradient-DS/open-webui
```

Or create via GitHub UI: Issues → Labels → New label.

#### 4. Test with workflow_dispatch

- Go to Actions → "Sync Upstream (Open WebUI)" → Run workflow
- Select `dev` branch
- Monitor the run

### Success Criteria:

#### Manual Verification:

- [ ] `CLAUDE_CODE_OAUTH_TOKEN` secret exists in repo settings
- [ ] `upstream-sync` label exists
- [ ] Manual workflow run completes successfully
- [ ] Draft PR is created (or "already up to date" message if no upstream changes)

---

## Testing Strategy

### Scenario 1: No upstream changes
- Expected: workflow logs "Fork is up to date" and exits
- Verify: no PR created, no branch changes

### Scenario 2: First run — clean merge (no conflicts)
- Expected: new draft PR opened with upstream changelog, no Claude invocation
- Verify: PR description has "None — clean merge" under conflicts

### Scenario 3: Subsequent run — PR already exists
- Expected: branch force-pushed, existing PR body updated with new date
- Verify: same PR number, updated title with today's date, no duplicate PRs

### Scenario 4: Run after PR was merged
- Expected: new draft PR created (old one is closed/merged)
- Verify: fresh PR number, branch rebuilt from current dev

### Scenario 5: Merge with conflicts
- Expected: Claude resolves conflicts, draft PR opened/updated with detailed description
- Verify: custom code preserved, conflicts documented in PR body

### Scenario 6: Claude can't resolve all conflicts
- Expected: draft PR with `UPSTREAM-SYNC: needs human review` markers in files
- Verify: "Needs Your Review" section in PR body lists affected files

### Scenario 7: Claude times out or fails
- Expected: fallback PR body, branch still pushed
- Verify: PR exists with generic "review manually" message

### Scenario 8: PR has `manual-edits` label
- Expected: workflow skips rebuild entirely, logs "Skipping — PR has 'manual-edits' label"
- Verify: branch untouched, PR body unchanged, your manual commits preserved

## Performance Considerations

- **Subscription quota**: Opus with 30 max turns per day. For typical 1-5 commit incremental syncs, this is minimal. For large catch-ups (post-vacation), consider running manually and monitoring.
- **GitHub Actions minutes**: ~5 min for clean merges, ~15-25 min with Claude. Well within free tier.
- **OAuth token expiry**: Monitor for failures — `claude setup-token` may need periodic refresh.

## References

- claude-code-action docs: `anthropics/claude-code-action` README
- Existing PR checks: `.github/workflows/pr-checks.yaml`
- Branch guard: `.github/workflows/branch-guard.yaml`
- Manual merge plan: `collab/docs/upstream-merge-260416-plan.md`
- Upstream merge policy: `CLAUDE.md` (repo root, "Upstream Merge Policy" section)
