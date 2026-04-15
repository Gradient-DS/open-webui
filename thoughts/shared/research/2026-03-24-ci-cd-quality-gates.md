---
date: '2026-03-24T12:00:00+01:00'
researcher: Claude Code
git_commit: c463eff54
branch: dev
repository: Gradient-DS/open-webui
topic: 'CI/CD Quality Gates: PR Template, Testing, Security Scanning, E2E'
tags: [research, codebase, ci-cd, github-actions, testing, security, helm, docker, fluxcd]
status: complete
last_updated: '2026-03-24'
last_updated_by: Claude Code
last_updated_note: 'Excluded linting from scope (upstream errors), added FluxCD image tag strategy'
---

# Research: CI/CD Quality Gates — PR Template, Testing, Security Scanning, E2E

**Date**: 2026-03-24
**Researcher**: Claude Code
**Git Commit**: c463eff54
**Branch**: dev
**Repository**: Gradient-DS/open-webui

## Research Question

What is the current state of CI/CD, PR templates, testing infrastructure, Docker/Helm setup, and security scanning? What needs to be built to:

1. Make the PR template more comprehensive (testing, docs, compose files, helm charts)
2. Add quality gates for PRs to `dev` (image builds, unit tests)
3. Add E2E testing for PRs to `test` (including external retrieval via genai-utils)
4. Add security scanning (SAST, SCA, container scanning)
5. Block PRs from merging if tests fail
6. Push images on merge to `dev`/`main` (not on PR open), with FluxCD-compatible tags

**Out of scope**: Linting (ESLint, svelte-check, PyLint) — upstream has ~17,000 pre-existing errors that are not fixable on our fork.

## Summary

The repo has a **three-branch promotion model** (`dev` → `test` → `main`) enforced by `branch-guard.yaml`, but the **only active quality gate is the branch guard** — all testing and security workflows are disabled. The testing infrastructure is minimal (~10 test files total, 3 of which are broken). The PR template is a bare 2-item checklist. There is significant opportunity to build out a proper CI/CD pipeline.

## Detailed Findings

### 1. Current Active Workflows

| Workflow                 | Trigger                           | Purpose                                    |
| ------------------------ | --------------------------------- | ------------------------------------------ |
| `docker-build-soev.yaml` | Push to `main`/`dev`/`test`, tags | Multi-arch Docker build + Helm OCI publish |
| `branch-guard.yaml`      | PRs to `main`/`test`              | Enforces `dev→test→main` promotion         |

**No workflows run on PRs to `dev`** — PRs can be merged without any automated checks.

### 2. Disabled Upstream Workflows (7 total)

These were disabled from upstream Open WebUI and provide a useful reference:

| File                                  | What it did                                         |
| ------------------------------------- | --------------------------------------------------- |
| `format-build-frontend.yaml.disabled` | Prettier check, i18n parse, `npm run build`, Vitest |
| `format-backend.yaml.disabled`        | Black formatting (Python 3.11/3.12 matrix)          |
| `lint-backend.disabled`               | PyLint via Bun                                      |
| `lint-frontend.disabled`              | ESLint + svelte-check via Bun                       |
| `integration-test.disabled`           | Cypress E2E + SQLite/Postgres migration tests       |
| `codespell.disabled`                  | Spell checking                                      |
| `build-release.yml.disabled`          | GitHub Release creation                             |

### 3. Current PR Template

`.github/pull_request_template.md` — extremely minimal:

```markdown
## Summary

<!-- Brief description of changes -->

## Checklist

- [ ] I have tested these changes locally
- [ ] I have reviewed my own code for obvious issues
```

### 4. Testing Infrastructure

| Layer         | Framework       | Files | Approx Tests | Status                                                                    |
| ------------- | --------------- | ----- | ------------ | ------------------------------------------------------------------------- |
| Frontend unit | Vitest 1.6.1    | 1     | ~40          | Working                                                                   |
| Backend unit  | pytest 8.3.2    | 5     | ~63          | 3/5 broken (missing `AbstractPostgresTest` + `mock_webui_user` utilities) |
| E2E           | Cypress 13.15.0 | 4     | ~12          | Working (needs running app at localhost:8080)                             |

**Working backend tests**: `test_provider.py` (storage), `test_redis.py` (Redis sentinel), `test_features.py` (feature flags)
**Broken backend tests**: `test_auths.py`, `test_models.py`, `test_users.py` — import `AbstractPostgresTest` and `mock_webui_user` which don't exist

**Test scripts**:

- `npm run test:frontend` → `vitest --passWithNoTests`
- `npm run cy:open` → Cypress GUI
- No npm script for pytest

### 5. Docker Setup

**Single Dockerfile** — multi-stage: Node frontend build → Python backend runtime. Build args control variants:

- `USE_SLIM=true` — skips model pre-download (used in CI)
- `USE_CUDA` — NVIDIA GPU support
- `USE_OLLAMA` — bundles Ollama server

**9 Docker Compose files**: base, GPU (NVIDIA/AMD), API exposure, data binding, OTEL, Stable Diffusion test, Playwright, soev-dev (Weaviate + PostgreSQL).

**Image published to**: `ghcr.io/gradient-ds/open-webui`

### 6. Image Tag Strategy (FluxCD-compatible)

The `docker-build-soev.yaml` workflow uses `docker/metadata-action` to generate these tags per branch:

| Branch   | Tags produced                                              |
| -------- | ---------------------------------------------------------- |
| `dev`    | `dev`, `git-<sha7>`, `dev-<sha7>-<run_number>`             |
| `test`   | `test`, `git-<sha7>`, `test-<sha7>-<run_number>`           |
| `main`   | `main`, `git-<sha7>`, `latest`, `main-<sha7>-<run_number>` |
| `v*` tag | `v<version>`, `git-<sha7>`, `<branch>-<sha7>-<run_number>` |

FluxCD (in `soev-gitops` repo) uses the branch-name tags (`dev`, `test`, `main`) to track deployments. The `pullPolicy: Always` in the Helm values ensures the latest image is pulled on each reconciliation.

**Current issue**: The Docker build triggers on **push** to all branches, which means images are already pushed on merge. However, there's no separation between "PR validation build" (should not push) and "merge build" (should push). Currently no builds run on PRs at all.

**Desired state**:

- PRs to `dev`: build image (no push) to verify compilation
- Merge to `dev`: build + push image with `dev` tag (FluxCD picks it up)
- PRs to `test`: run E2E tests
- Merge to `test`: build + push image with `test` tag (FluxCD picks it up)
- Merge to `main`: build + push image with `main` + `latest` tags (FluxCD picks it up)

### 7. Helm Chart

`helm/open-webui-tenant/` — per-tenant chart deploying:

- Open WebUI Deployment
- PostgreSQL StatefulSet
- Weaviate StatefulSet
- Ingress, NetworkPolicy, ExternalSecrets

Published as OCI to `oci://ghcr.io/gradient-ds/charts` on every push to `main`/`dev`/`test`.

Default image tag in values.yaml: `main` (overridden per environment).

### 8. Security Scanning

**None exists.** No `.bandit`, `.trivyignore`, `.snyk`, or security workflows.

### 9. Dependabot

Configured for `uv`, `pip`, and `github-actions` on monthly schedule, targeting `dev` branch.

## Recommendations for Implementation

### Phase 1: PR Template Enhancement

Expand `.github/pull_request_template.md` with sections for:

- Change type (feature, bugfix, refactor, docs, etc.)
- Testing checklist (unit tests, E2E, manual testing)
- Documentation updates
- Docker Compose file updates (if services changed)
- Helm chart updates (if new features/config introduced)
- Breaking changes

### Phase 2: PR Quality Gates for `dev`

Create a workflow triggered on PRs to `dev`:

1. **Docker build check** — build the slim image (don't push) to verify it compiles
2. **Frontend build** — `npm run build` (confirms TypeScript/Svelte compilation)
3. **Frontend unit tests** — `npm run test:frontend`
4. **Backend format check** — Black `--check`
5. **Backend unit tests** — pytest on the working test files
6. **Helm lint** — `helm lint` + `helm template` to validate chart

### Phase 3: Refactor Docker Build Workflow

Split `docker-build-soev.yaml` into:

- **PR workflow**: build only (no push), triggered on `pull_request`
- **Merge workflow**: build + push + Helm publish, triggered on `push` to `dev`/`test`/`main`

This ensures images are only pushed on actual merges, and FluxCD picks up the branch-name tags.

### Phase 4: E2E Testing for `test`

Enhance the disabled `integration-test.disabled` workflow:

1. **Build and start the full stack** via Docker Compose
2. **Run Cypress E2E** against the running app
3. **Run migration tests** (SQLite + Postgres startup verification)
4. **Optional: genai-utils integration** — add a compose overlay that starts genai-utils containers for testing external retrieval/web search

### Phase 5: Security Scanning

Create `.github/workflows/security-scanning.yaml`:

- **Python SAST**: Bandit scanning `backend/` (adapt from user's example)
- **Python SCA**: pip-audit on `backend/requirements.txt`
- **Node.js SCA**: npm audit at root
- **Container scanning**: Trivy on the built Docker image
- Trigger on push to `main`/`dev`/`test`, PRs to `main`, weekly schedule

### Phase 6: Branch Protection

Configure via GitHub repo settings:

- Require status checks to pass before merging
- Required checks for `dev`: docker-build, frontend-tests, backend-tests
- Required checks for `test`: all of above + E2E tests
- Required checks for `main`: all of above (inherits from test)

## Code References

- `.github/workflows/docker-build-soev.yaml` — Active Docker build workflow
- `.github/workflows/branch-guard.yaml` — Branch promotion guard
- `.github/pull_request_template.md` — Current PR template
- `.github/workflows/integration-test.disabled` — Reference E2E + migration test workflow
- `.github/workflows/format-build-frontend.yaml.disabled` — Reference frontend CI
- `src/lib/utils/features.test.ts` — Only working frontend test
- `backend/open_webui/test/` — Backend test directory (5 files, 2 working)
- `cypress/e2e/` — Cypress E2E tests (4 specs)
- `helm/open-webui-tenant/` — Helm chart
- `Dockerfile` — Multi-stage Docker build
- `docker-compose.soev-dev.yaml` — Local dev compose
- `pyproject.toml:212-217` — Codespell configuration

## Architecture Insights

1. **Branch flow is well-defined** (`dev→test→main`) but lacks quality gates — the guard only checks source branch, not code quality
2. **The disabled upstream workflows are a goldmine** — they can be adapted rather than built from scratch
3. **Testing is minimal** — only 1 frontend test file and 2 working backend test files. The existing Cypress suite is the most complete testing layer
4. **Docker build is the most reliable "does it compile" check** — the Docker build (which runs `npm run build`) is a good compilation gate
5. **The Helm chart is published on every push** including `dev` — broken Helm charts could be published; adding a `helm lint` + `helm template` check would catch issues early
6. **Security scanning is completely absent** — no SAST, SCA, or container scanning
7. **Image push should only happen on merge** — currently the workflow triggers on push (which is merge), but PR validation builds need to be added separately without pushing

## Open Questions

1. **genai-utils E2E**: How to authenticate/configure the genai-utils containers for E2E testing? What test data to use?
2. **Backend test utilities**: Should we create the missing `AbstractPostgresTest` and `mock_webui_user` to enable the 3 broken backend test files?
3. **Cypress parallelization**: Should E2E tests run in parallel for speed, or serial for reliability?
4. **Security scanning thresholds**: Should security findings block PRs immediately, or start as informational?
