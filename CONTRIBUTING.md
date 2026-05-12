# Contributing to soev.ai

Thanks for your interest in contributing! soev.ai is a fork of [Open WebUI](https://github.com/open-webui/open-webui). Contributions are accepted via the standard GitHub fork & pull request workflow.

## Where should this contribution go?

- **soev.ai–specific** (cloud sync, agent API, GDPR features, feature flags, Helm chart, SSO invites, TOTP, …) → here.
- **Upstream Open WebUI behaviour** (chat UI, core RAG, model providers, …) → please contribute at [open-webui/open-webui](https://github.com/open-webui/open-webui). Fixes merged upstream flow back to us automatically via our upstream sync.

If you are unsure, open a draft PR or a discussion here and we will help route it.

## Workflow

1. **Fork** `Gradient-DS/open-webui` on GitHub.
2. **Clone** your fork locally.
3. **Branch from `dev`**:
   ```bash
   git checkout dev
   git pull
   git checkout -b feat/your-feature
   ```
4. Make changes, commit, push to your fork.
5. **Open a PR targeting the `dev` branch** of `Gradient-DS/open-webui`.

CI enforces branch promotion: PRs to `test` must come from `dev`, and PRs to `main` from `test`. External contributors only need to target `dev`. Maintainers promote releases through `test` and `main`.

## Local development

The README has a quickstart. For the full set of commands and project structure, see [CLAUDE.md](./CLAUDE.md).

Minimal loop:

```bash
npm install
pip install -e ".[dev]"
open-webui dev          # backend on :8080
npm run dev             # frontend on :5173
```

## Code style

**Frontend:** SvelteKit 5 (runes), TypeScript strict mode, Tailwind CSS 4. ESLint + Prettier.

```bash
npm run check           # svelte-check
npm run lint:frontend   # ESLint
npm run format          # Prettier
npm run test:frontend   # Vitest
```

**Backend:** FastAPI, async SQLAlchemy. Ruff (88-char line length), type hints on signatures.

```bash
ruff format backend/
ruff check backend/
pytest backend/         # see CI for the subset we currently gate on
```

**Upstream-friendly patterns:** prefer additive changes over modifying upstream code. Use separate files, feature flags, and conditional mounts. This dramatically reduces upstream merge conflicts.

## Internationalization

All user-facing text must include both **English** (`en-US`) and **Dutch** (`nl-NL`) translations.

- Translation files: `src/lib/i18n/locales/{lang}/translation.json`.
- Keys are alphabetically sorted.
- An empty value in `en-US` means "use the key itself as display text".
- Run `npm run i18n:parse` to extract new keys after adding `$i18n.t(...)` calls.

## Pull request guidelines

- Keep PRs **focused** — one feature or fix per PR.
- Write commit messages that explain **why**, not just what.
- Update `.env.example` when adding env vars, and Helm chart values when changing deployment-relevant config.
- Add tests where practical.
- Fill in the [pull request template](./.github/pull_request_template.md) checklist honestly.

CI on PRs runs: frontend build + Vitest, backend Ruff format check + pytest subset, Helm lint + template, Docker build (no push), Bandit SAST, pip-audit, Trivy image scan.

## Reporting bugs & requesting features

- **Bug** — open an issue using the Bug Report template; include reproduction steps and logs.
- **Feature** — open an issue using the Feature Request template, or start a discussion first for larger ideas.
- **Security vulnerability** — see [SECURITY.md](./SECURITY.md). Do not open a public issue.

## License

By contributing, you agree that your contributions will be licensed under the same [BSD-3-Clause-modified license](./LICENSE) as the rest of the project. You retain copyright of your contributions.

There is no separate CLA — the LICENSE handles inbound=outbound licensing. Thanks for keeping soev.ai sovereign, secure, and useful.
