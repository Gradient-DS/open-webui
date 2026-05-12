# soev.ai

> **Sovereign AI for the public sector.**

soev.ai is a self-hosted AI platform built for Dutch public sector organizations. It is a fork of [**Open WebUI**](https://github.com/open-webui/open-webui), preserving the full upstream feature set and adding the integrations, compliance, and operational tooling needed for regulated environments.

![Open WebUI Banner](./banner.png)

A product by [Gradient-DS](https://gradient-ds.com). Learn more at [soev.ai](https://soev.ai).

---

## Why a fork

- **Data sovereignty** — public sector deployments need a source-available, hardened, self-hostable stack.
- **Compliance** — GDPR-driven retention, export, and archival flows are first-class.
- **Ecosystem fit** — tight integration with Microsoft 365 (OneDrive, Graph), Google Workspace, and our external agents and pipelines.
- **Upstream compatibility** — we sync regularly with Open WebUI and prefer additive changes (separate files, feature flags, conditional mounts) so upstream improvements keep flowing in.

## What soev.ai adds

On top of everything Open WebUI already offers:

- **Cloud sync** — Native OneDrive and Google Drive knowledge base sync with incremental updates and permission lifecycle.
- **Typed knowledge bases** — `local` / `onedrive` / `google_drive` / `custom` with appropriate UI per type.
- **External agents API** — Reverse proxy at `/api/v1/agent/`, OpenAI-compatible, for routing to a configurable agent service.
- **External pipeline & integration providers** — Pluggable upload / parse / retrieval providers via env config.
- **GDPR features** — User data export (zip), configurable data retention (TTL with warning emails), per-user archival.
- **TOTP 2FA** — With admin enforcement, recovery codes, and partial-JWT login flow.
- **SSO invite auto-provisioning** — Microsoft Graph email invites with SSO pre-provisioning.
- **Granular feature flags** — Disable any custom UI feature per deployment.
- **Helm chart** — Production-ready Kubernetes deployment.
- **Security hardening** — Trivy + Bandit + pip-audit in CI, slim Docker image, replay-token fixes.

For everything else (chat UI, model providers, RAG, voice, image gen, multi-model conversations, …) see the [Open WebUI documentation](https://docs.openwebui.com/).

## Quickstart

```bash
# Docker (fastest)
docker compose up -d
```

Open [http://localhost:3000](http://localhost:3000) after startup.

For local development:

```bash
npm install
pip install -e ".[dev]"

# Terminal 1: backend on :8080
open-webui dev

# Terminal 2: frontend on :5173 (proxies to backend)
npm run dev
```

See [`.env.example`](./.env.example) for configuration. soev.ai–specific options (cloud sync, agent API, feature flags, retention) are annotated in the file. For the full installation matrix (pip, Docker, Kubernetes, GPU variants) see the [Open WebUI install docs](https://docs.openwebui.com/getting-started/).

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](./CONTRIBUTING.md) before opening a PR.

In short: fork this repo, branch from `dev`, PR back to `dev`. Maintainers handle the `dev → test → main` promotion.

## Security

Please report vulnerabilities privately — see [SECURITY.md](./SECURITY.md). Do not open public issues for security reports.

## License & credits

soev.ai is distributed under the same [BSD-3-Clause-modified license](./LICENSE) as Open WebUI. Open WebUI branding is preserved per the license terms.

All upstream code and features remain the work of the Open WebUI project and its community. We are grateful to [Timothy Jaeryang Baek](https://github.com/tjbck) and the Open WebUI contributors for the foundation this fork is built on. Bugs and features that affect upstream behaviour are best contributed at [open-webui/open-webui](https://github.com/open-webui/open-webui) so the wider community benefits.

Copyright © 2023– Open WebUI Inc. soev.ai customizations © Gradient-DS.
