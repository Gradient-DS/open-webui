# CI/CD Quality Gates Implementation Plan

## Overview

Build out comprehensive CI/CD quality gates for the open-webui repo across the `dev → test → main` promotion chain. Currently the only active quality gate is the branch-guard (source branch check). This plan adds: PR validation builds, unit tests, E2E testing, security scanning, and enforced branch protection — while ensuring Docker images are only pushed on actual merges (not PR opens) so FluxCD in `soev-gitops` picks up the right tags.

## Current State Analysis

**Active workflows:**
- `docker-build-soev.yaml` — multi-arch Docker build + Helm OCI publish on push to `main`/`dev`/`test`
- `branch-guard.yaml` — enforces `dev→test→main` PR source branch

**Disabled upstream workflows (reference):**
- `format-build-frontend.yaml.disabled` — Prettier, i18n, `npm run build`, Vitest
- `format-backend.yaml.disabled` — Black format check (Python 3.11/3.12)
- `integration-test.disabled` — Cypress E2E + SQLite/Postgres migration tests

**Testing infrastructure:**
- 1 frontend test file (Vitest, ~40 tests) — working
- 5 backend test files (pytest) — only 3 working (`test_provider.py`, `test_redis.py`, `test_features.py`); 3 router tests broken (missing `AbstractPostgresTest`)
- 4 Cypress E2E specs (~12 tests) — working
- No security scanning whatsoever

**PR template:** 2-item checklist (tested locally, reviewed code)

**Image tags:** Branch-name tags (`dev`, `test`, `main`) consumed by FluxCD in `soev-gitops`

### Key Discoveries:
- `.github/workflows/docker-build-soev.yaml` — already triggers on push (merge), so the image push workflow is correct; we need to ADD a PR-triggered build-only workflow
- `.github/workflows/integration-test.disabled` — complete Cypress + migration test reference we can adapt
- `format-build-frontend.yaml.disabled` — reference for frontend build + Vitest jobs
- `helm/open-webui-tenant/Chart.yaml` — Helm chart already has `helm lint` in publish job but not as a PR gate

## Desired End State

After implementation:
1. PRs to `dev` are blocked unless: Docker image builds, frontend builds, unit tests pass, backend format check passes, Helm chart lints
2. PRs to `test` are blocked unless: all `dev` gates pass PLUS Cypress E2E + migration tests pass
3. PRs to `main` are blocked unless: all `test` gates pass (inherited — PRs to `main` must come from `test`)
4. Images are pushed to GHCR only on merge (push) to `dev`/`test`/`main` — not on PR open
5. Security scanning runs on pushes + weekly schedule, with results visible on PRs
6. PR template guides developers through testing, docs, compose, and helm checklist items

### Verification:
- Open a test PR to `dev` → all quality gate checks appear and must pass
- Merge to `dev` → image pushed with `dev` tag, FluxCD picks it up
- Open a test PR to `test` → E2E tests run in addition to `dev` gates
- Security scanning runs on schedule and on pushes

## What We're NOT Doing

- **Linting** (ESLint, svelte-check, PyLint) — upstream has ~17,000 pre-existing errors, not fixable on our fork
- **Fixing broken backend tests** — the 3 router tests (`test_auths.py`, `test_models.py`, `test_users.py`) need missing `AbstractPostgresTest` utility; separate effort
- **genai-utils E2E integration** — documented as future enhancement in E2E phase but not implemented (needs cross-repo coordination)
- **Auto-merging or auto-deploying** — FluxCD handles deployment; we only gate quality

---

## Phase 1: Enhanced PR Template

### Overview
Replace the minimal 2-item PR template with a comprehensive checklist that covers testing, documentation, Docker Compose, and Helm chart considerations.

### Changes Required:

#### 1. PR Template
**File**: `.github/pull_request_template.md`
**Changes**: Replace entire file with comprehensive template

```markdown
## Summary

<!-- Brief description of what this PR does and why -->

## Type of Change

- [ ] Bug fix (non-breaking change that fixes an issue)
- [ ] New feature (non-breaking change that adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to change)
- [ ] Refactoring (no functional changes)
- [ ] Documentation update
- [ ] Infrastructure / CI/CD change

## Testing

- [ ] I have tested these changes locally
- [ ] I have added/updated unit tests for my changes
- [ ] I have verified existing tests still pass (`npm run test:frontend` / `pytest`)
- [ ] I have tested E2E scenarios affected by my changes

## Documentation & Configuration

- [ ] I have updated relevant documentation (if applicable)
- [ ] I have updated Docker Compose files (if services/config changed)
- [ ] I have updated Helm chart values/templates (if new features/config introduced)
- [ ] I have updated environment variable documentation (if new env vars added)

## Review Checklist

- [ ] I have reviewed my own code for obvious issues
- [ ] I have verified no secrets or credentials are included
- [ ] My changes follow the existing code patterns in this repository
```

### Success Criteria:

#### Automated Verification:
- [x] File exists at `.github/pull_request_template.md`
- [ ] Template renders correctly when opening a new PR on GitHub

#### Manual Verification:
- [ ] Open a draft PR and verify the template renders with all sections
- [ ] Checklist items are relevant and comprehensive

---

## Phase 2: PR Quality Gates for `dev`

### Overview
Create a new workflow that runs on PRs to `dev` (and `test`/`main` since those inherit). This validates that code compiles, tests pass, backend formatting is correct, and the Helm chart is valid — WITHOUT pushing any images.

### Changes Required:

#### 1. New PR Validation Workflow
**File**: `.github/workflows/pr-checks.yaml`
**Changes**: New file — triggered on `pull_request` to `dev`, `test`, `main`

```yaml
name: PR Quality Gates

on:
  pull_request:
    branches:
      - dev
      - test
      - main

jobs:
  frontend-build:
    name: Frontend Build & Tests
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Repository
        uses: actions/checkout@v5

      - name: Setup Node.js
        uses: actions/setup-node@v5
        with:
          node-version: '22'

      - name: Install Dependencies
        run: npm ci --force

      - name: Build Frontend
        run: npm run build

      - name: Run Frontend Unit Tests
        run: npm run test:frontend

  backend-format:
    name: Backend Format Check
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Repository
        uses: actions/checkout@v5

      - name: Set up Python
        uses: actions/setup-python@v6
        with:
          python-version: '3.11'

      - name: Install Black
        run: pip install black

      - name: Check backend formatting
        run: black --check backend/ --exclude ".venv/|/venv/"

  backend-tests:
    name: Backend Unit Tests
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Repository
        uses: actions/checkout@v5

      - name: Set up Python
        uses: actions/setup-python@v6
        with:
          python-version: '3.11'

      - name: Set up uv
        uses: astral-sh/setup-uv@v6

      - name: Install dependencies
        run: |
          uv venv
          uv pip install -r backend/requirements.txt

      - name: Run pytest (working tests only)
        env:
          WEBUI_SECRET_KEY: test-secret-key
        run: |
          source .venv/bin/activate
          cd backend
          PYTHONPATH=. pytest \
            open_webui/test/apps/webui/storage/test_provider.py \
            open_webui/test/util/test_redis.py \
            open_webui/test/util/test_features.py \
            -v --tb=short

  helm-lint:
    name: Helm Chart Validation
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Repository
        uses: actions/checkout@v5

      - name: Set up Helm
        uses: azure/setup-helm@v4
        with:
          version: v3.16.0

      - name: Lint Helm chart
        run: helm lint helm/open-webui-tenant

      - name: Template Helm chart (catch rendering errors)
        run: helm template test-release helm/open-webui-tenant > /dev/null

  docker-build:
    name: Docker Image Build (no push)
    runs-on: ubuntu-latest
    steps:
      - name: Maximize build space
        uses: AdityaGarg8/remove-unwanted-software@v4.1
        with:
          remove-android: 'true'
          remove-haskell: 'true'
          remove-codeql: 'true'

      - name: Checkout Repository
        uses: actions/checkout@v5

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build Docker image (slim, no push)
        uses: docker/build-push-action@v5
        with:
          context: .
          push: false
          load: true
          tags: open-webui:pr-test
          build-args: |
            BUILD_HASH=${{ github.sha }}
            USE_SLIM=true
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

### Success Criteria:

#### Automated Verification:
- [x] Workflow file is valid YAML: `python -c "import yaml; yaml.safe_load(open('.github/workflows/pr-checks.yaml'))"`
- [ ] All 5 jobs (`frontend-build`, `backend-format`, `backend-tests`, `helm-lint`, `docker-build`) appear in GitHub Actions when a PR is opened to `dev`

#### Manual Verification:
- [ ] Open a test PR to `dev` and verify all 5 check jobs run
- [ ] Jobs run in parallel (no dependencies between them)
- [ ] Docker build does NOT push any image (verify no new tag appears in GHCR)
- [ ] A PR with a Black formatting issue fails the `backend-format` job

**Implementation Note**: After completing this phase and verifying all checks run on a test PR, pause for manual confirmation before proceeding.

---

## Phase 3: Refactor Image Publishing

### Overview
The current `docker-build-soev.yaml` already triggers on push (merge) to `dev`/`test`/`main`, which is correct. No structural change needed — images are already only pushed on merge. The PR validation build (Phase 2) handles the "build without push" case.

However, we should verify and document that the current workflow is doing the right thing, and ensure the PR checks workflow from Phase 2 does NOT overlap with the push-triggered build.

### Changes Required:

#### 1. No changes to `docker-build-soev.yaml`
The existing workflow triggers on `push` (which is merge), not on `pull_request`. This is already the correct behavior:
- Push to `dev` → builds + pushes image with `dev` tag → FluxCD deploys to dev environment
- Push to `test` → builds + pushes image with `test` tag → FluxCD deploys to test environment
- Push to `main` → builds + pushes image with `main` + `latest` tags → FluxCD deploys to production

The PR checks workflow (Phase 2) handles the `pull_request` trigger with build-only (no push).

### Success Criteria:

#### Automated Verification:
- [x] `docker-build-soev.yaml` triggers only on `push` (not `pull_request`) — verify with: `grep -A5 '^on:' .github/workflows/docker-build-soev.yaml`
- [x] `pr-checks.yaml` triggers only on `pull_request` (not `push`) — verify with: `grep -A5 '^on:' .github/workflows/pr-checks.yaml`

#### Manual Verification:
- [ ] Open a PR to `dev` — only `pr-checks.yaml` runs, NOT `docker-build-soev.yaml`
- [ ] Merge the PR — `docker-build-soev.yaml` runs and pushes the `dev` tagged image
- [ ] Verify the `dev` tag in GHCR updates after merge
- [ ] Verify FluxCD in `soev-gitops` picks up the new `dev` image

---

## Phase 4: E2E Testing for `test`

### Overview
Create a workflow that runs Cypress E2E tests and migration tests on PRs to `test`. This is based on the disabled `integration-test.disabled` workflow but adapted for our branch strategy. Includes Compose stack with Weaviate + PostgreSQL for full-stack testing.

### Changes Required:

#### 1. E2E Test Workflow
**File**: `.github/workflows/e2e-tests.yaml`
**Changes**: New file — triggered on PRs to `test` and `main`

```yaml
name: E2E Tests

on:
  pull_request:
    branches:
      - test
      - main

jobs:
  cypress-e2e:
    name: Cypress E2E Tests
    runs-on: ubuntu-latest
    steps:
      - name: Maximize build space
        uses: AdityaGarg8/remove-unwanted-software@v4.1
        with:
          remove-android: 'true'
          remove-haskell: 'true'
          remove-codeql: 'true'

      - name: Checkout Repository
        uses: actions/checkout@v5

      - name: Build and run Compose Stack
        run: |
          docker compose \
            --file docker-compose.yaml \
            --file docker-compose.api.yaml \
            up --detach --build

      - name: Delete Docker build cache
        run: docker builder prune --all --force

      - name: Wait for Ollama to be up
        timeout-minutes: 5
        run: |
          until curl --output /dev/null --silent --fail http://localhost:11434; do
            printf '.'
            sleep 1
          done
          echo "Ollama is up!"

      - name: Preload Ollama model
        run: docker exec ollama ollama pull qwen:0.5b-chat-v1.5-q2_K

      - name: Wait for Open WebUI to be ready
        timeout-minutes: 3
        run: |
          until curl --output /dev/null --silent --fail http://localhost:3000; do
            printf '.'
            sleep 1
          done
          echo "Open WebUI is up!"

      - name: Cypress run
        uses: cypress-io/github-action@v6
        env:
          LIBGL_ALWAYS_SOFTWARE: 1
        with:
          browser: chrome
          wait-on: 'http://localhost:3000'
          config: baseUrl=http://localhost:3000

      - name: Upload Cypress videos
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: cypress-videos
          path: cypress/videos
          if-no-files-found: ignore

      - name: Extract Compose logs
        if: always()
        run: docker compose logs > compose-logs.txt

      - name: Upload Compose logs
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: compose-logs
          path: compose-logs.txt
          if-no-files-found: ignore

  migration-tests:
    name: Migration Tests (SQLite + Postgres)
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres
        env:
          POSTGRES_PASSWORD: postgres
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
        ports:
          - 5432:5432
    steps:
      - name: Checkout Repository
        uses: actions/checkout@v5

      - name: Set up Python
        uses: actions/setup-python@v6
        with:
          python-version: '3.11'

      - name: Set up uv
        uses: astral-sh/setup-uv@v6

      - name: Install dependencies
        run: |
          uv venv
          uv pip install -r backend/requirements.txt
          uv pip install psycopg2-binary

      - name: Test backend startup with SQLite
        id: sqlite
        env:
          WEBUI_SECRET_KEY: secret-key
          GLOBAL_LOG_LEVEL: debug
        run: |
          source .venv/bin/activate
          cd backend
          uvicorn open_webui.main:app --port "8080" --forwarded-allow-ips '*' &
          UVICORN_PID=$!
          for i in {1..40}; do
              curl -s http://localhost:8080/api/config > /dev/null && break
              sleep 1
              if [ $i -eq 40 ]; then
                  echo "Server failed to start"
                  kill -9 $UVICORN_PID
                  exit 1
              fi
          done
          sleep 5
          if ! kill -0 $UVICORN_PID; then
              echo "Server has stopped"
              exit 1
          fi
          kill $UVICORN_PID || true

      - name: Test backend startup with Postgres
        if: success() || steps.sqlite.conclusion == 'failure'
        env:
          WEBUI_SECRET_KEY: secret-key
          GLOBAL_LOG_LEVEL: debug
          DATABASE_URL: postgresql://postgres:postgres@localhost:5432/postgres
          DATABASE_POOL_SIZE: 10
          DATABASE_POOL_MAX_OVERFLOW: 10
          DATABASE_POOL_TIMEOUT: 30
        run: |
          source .venv/bin/activate
          cd backend
          uvicorn open_webui.main:app --port "8081" --forwarded-allow-ips '*' &
          UVICORN_PID=$!
          for i in {1..20}; do
              curl -s http://localhost:8081/api/config > /dev/null && break
              sleep 1
              if [ $i -eq 20 ]; then
                  echo "Server failed to start"
                  kill -9 $UVICORN_PID
                  exit 1
              fi
          done
          sleep 5
          if ! kill -0 $UVICORN_PID; then
              echo "Server has stopped"
              exit 1
          fi

          # Verify DB reconnection after connection drop
          status_code=$(curl --write-out %{http_code} -s --output /dev/null http://localhost:8081/health/db)
          if [[ "$status_code" -ne 200 ]]; then
            echo "Server failed before postgres reconnect check"
            exit 1
          fi

          echo "Terminating all connections to postgres..."
          python -c "import os, psycopg2 as pg2; \
            conn = pg2.connect(dsn=os.environ['DATABASE_URL'].replace('+pool', '')); \
            cur = conn.cursor(); \
            cur.execute('SELECT pg_terminate_backend(psa.pid) FROM pg_stat_activity psa WHERE datname = current_database() AND pid <> pg_backend_pid();')"

          status_code=$(curl --write-out %{http_code} -s --output /dev/null http://localhost:8081/health/db)
          if [[ "$status_code" -ne 200 ]]; then
            echo "Server has not reconnected to postgres: returned status $status_code"
            exit 1
          fi
```

### Future Enhancement: genai-utils Integration
Once cross-repo coordination is established, add a Compose overlay for E2E testing with external retrieval:
- Create `docker-compose.e2e-genai.yaml` overlay that starts genai-utils API + Weaviate with test data
- Configure Open WebUI to connect to genai-utils for RAG queries
- Add Cypress tests that exercise document upload → retrieval → chat flows
- This requires: test data fixtures, genai-utils Docker image accessible from GHCR, and auth token configuration

### Success Criteria:

#### Automated Verification:
- [x] Workflow file is valid YAML
- [ ] Both jobs (`cypress-e2e`, `migration-tests`) appear on PRs to `test`
- [x] Workflow does NOT trigger on PRs to `dev` (only `test` and `main`)

#### Manual Verification:
- [ ] Open a PR from `dev` to `test` and verify both E2E and migration tests run
- [ ] Cypress videos are uploaded as artifacts on failure
- [ ] Compose logs are uploaded as artifacts on failure
- [ ] SQLite and Postgres migration tests both pass

**Implementation Note**: After completing this phase and verifying E2E tests pass on a test PR to `test`, pause for manual confirmation before proceeding.

---

## Phase 5: Security Scanning

### Overview
Add security scanning workflow with Python SAST (Bandit), dependency auditing (pip-audit, npm audit), and container vulnerability scanning (Trivy). Runs on pushes to protected branches, PRs to `main`, and weekly schedule.

### Changes Required:

#### 1. Security Scanning Workflow
**File**: `.github/workflows/security-scanning.yaml`
**Changes**: New file

```yaml
name: Security Scanning

on:
  push:
    branches: [main, dev, test]
  pull_request:
    branches: [main]
  schedule:
    - cron: '0 6 * * 1'  # Weekly Monday 6 AM UTC

jobs:
  sast-python:
    name: Python SAST (Bandit)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5

      - uses: actions/setup-python@v6
        with:
          python-version: '3.11'

      - name: Install Bandit
        run: pip install bandit

      - name: Run Bandit
        run: bandit -r backend/ -ll --confidence-level=medium -x backend/.venv,backend/open_webui/test

  sca-python:
    name: Python Dependencies (pip-audit)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5

      - uses: actions/setup-python@v6
        with:
          python-version: '3.11'

      - name: Install pip-audit and dependencies
        run: |
          pip install pip-audit
          pip install -r backend/requirements.txt

      - name: Run pip-audit
        run: pip-audit --desc on

  sca-node:
    name: Node.js Dependencies (npm audit)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5

      - uses: actions/setup-node@v5
        with:
          node-version: '22'

      - run: npm ci --force

      - name: npm audit
        run: npm audit --audit-level=high

  container-scan:
    name: Container Scan (Trivy)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5

      - name: Maximize build space
        uses: AdityaGarg8/remove-unwanted-software@v4.1
        with:
          remove-android: 'true'
          remove-haskell: 'true'
          remove-codeql: 'true'

      - name: Build Docker image for scanning
        run: |
          docker build -t open-webui:scan \
            --build-arg USE_SLIM=true \
            --build-arg BUILD_HASH=${{ github.sha }} \
            .

      - name: Trivy vulnerability scan
        uses: aquasecurity/trivy-action@0.35.0
        with:
          image-ref: 'open-webui:scan'
          format: 'table'
          severity: 'CRITICAL,HIGH'
          exit-code: '1'
          ignore-unfixed: true
```

### Success Criteria:

#### Automated Verification:
- [x] Workflow file is valid YAML
- [ ] All 4 jobs appear on pushes to `dev`/`test`/`main`
- [ ] Weekly schedule triggers correctly (verify in GitHub Actions → Schedules)

#### Manual Verification:
- [ ] Push to `dev` triggers all 4 security scanning jobs
- [ ] Bandit correctly scans `backend/` (not `server/`)
- [ ] pip-audit reports on known vulnerabilities
- [ ] npm audit runs at root level (not in `frontend/` subdirectory)
- [ ] Trivy scans the built slim Docker image
- [ ] Review initial scan results and add any necessary ignores (`.trivyignore`, `--ignore-vuln` flags)

**Implementation Note**: The first run will likely surface existing vulnerabilities. Review results and add targeted ignores for false positives or unfixable issues before making security scanning a blocking gate. Start as informational, then promote to blocking.

---

## Phase 6: Branch Protection Configuration

### Overview
Configure GitHub repository settings to require status checks before merging. This is done in the GitHub UI (Settings → Branches → Branch protection rules), not via committed files.

### Changes Required:

#### 1. Branch Protection Rules (GitHub UI)

**`dev` branch protection:**
- Require status checks to pass before merging: **Yes**
- Required checks:
  - `Frontend Build & Tests` (from `pr-checks.yaml`)
  - `Backend Format Check` (from `pr-checks.yaml`)
  - `Backend Unit Tests` (from `pr-checks.yaml`)
  - `Helm Chart Validation` (from `pr-checks.yaml`)
  - `Docker Image Build (no push)` (from `pr-checks.yaml`)
- Require branches to be up to date before merging: **Yes**

**`test` branch protection:**
- Require status checks to pass before merging: **Yes**
- Required checks:
  - All `dev` checks (they run on PRs to `test` too via `pr-checks.yaml`)
  - `Cypress E2E Tests` (from `e2e-tests.yaml`)
  - `Migration Tests (SQLite + Postgres)` (from `e2e-tests.yaml`)
  - `check-source-branch` (from `branch-guard.yaml` — must come from `dev`)
- Require branches to be up to date before merging: **Yes**

**`main` branch protection:**
- Require status checks to pass before merging: **Yes**
- Required checks:
  - All `dev` checks
  - `check-source-branch` (from `branch-guard.yaml` — must come from `test`)
- Require branches to be up to date before merging: **Yes**
- Note: E2E tests already ran when code was promoted from `dev` to `test`

#### 2. Documentation
**File**: `docs/CI-CD.md` (new file, optional)
**Changes**: Document the CI/CD pipeline, branch strategy, and required checks for team reference

### Success Criteria:

#### Manual Verification:
- [ ] Branch protection rules are configured in GitHub for `dev`, `test`, and `main`
- [ ] A PR to `dev` with a failing check cannot be merged
- [ ] A PR to `test` from a non-`dev` branch is blocked by branch-guard
- [ ] A PR to `test` with failing E2E tests cannot be merged
- [ ] Merging to `dev` triggers image push with `dev` tag
- [ ] Merging to `test` triggers image push with `test` tag
- [ ] Merging to `main` triggers image push with `main` + `latest` tags

---

## Testing Strategy

### Per-Phase Testing:
Each phase has its own success criteria. Test in order since later phases depend on earlier ones.

### Integration Testing:
After all phases are complete, run through the full promotion cycle:
1. Create a feature branch from `dev`
2. Open PR to `dev` → verify all PR quality gates run
3. Merge to `dev` → verify image pushed with `dev` tag
4. Open PR from `dev` to `test` → verify E2E tests run
5. Merge to `test` → verify image pushed with `test` tag
6. Open PR from `test` to `main` → verify checks pass
7. Merge to `main` → verify image pushed with `main` + `latest` tags

### Edge Cases:
- PR with only backend changes — frontend build still runs (no path filtering for simplicity)
- PR with Helm chart changes — helm-lint catches template errors
- Dependabot PRs to `dev` — all gates apply
- Security scan finding a critical vulnerability — verify it blocks (once promoted to blocking)

## Performance Considerations

- **Docker build in PR checks** is the slowest job (~5-10 min). Uses GHA cache to speed up repeat builds.
- **E2E tests** require building and starting the full Compose stack (~5-10 min). Only runs on PRs to `test`/`main` to avoid slowing down `dev` PRs.
- **Security scanning** runs container build (~5-10 min for Trivy). Runs on push (post-merge) and weekly, not as a PR gate initially.
- All PR check jobs run in **parallel** (no `needs:` dependencies) to minimize wall-clock time.

## References

- Research document: `thoughts/shared/research/2026-03-24-ci-cd-quality-gates.md`
- Current Docker build: `.github/workflows/docker-build-soev.yaml`
- Current branch guard: `.github/workflows/branch-guard.yaml`
- Disabled integration test reference: `.github/workflows/integration-test.disabled`
- Disabled frontend build reference: `.github/workflows/format-build-frontend.yaml.disabled`
- Disabled backend format reference: `.github/workflows/format-backend.yaml.disabled`
- Helm chart: `helm/open-webui-tenant/`
